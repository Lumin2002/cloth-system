import re
import unicodedata
from decimal import Decimal
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any, Callable, Optional

import pandas as pd
from django.db import transaction
import logging
logger = logging.getLogger(__name__)
from openpyxl import load_workbook

# 本模块大量使用 pandas 动态索引与 Django ORM 描述符，静态类型检查误报居高；
# 关闭对应的推断类检查，保留语法/未定义名检查。
# pyright: reportArgumentType=false, reportOperatorIssue=false, reportAttributeAccessIssue=false, reportCallIssue=false, reportGeneralTypeIssues=false, reportOptionalMemberAccess=false

from .models import ClothOrder, Customer, Supplier
from .constants import DEFAULT_STAGES_MAP

SHEET_BULK = '大货'

# Excel 原始表头 -> 模型字段（与「大货」工作表一致）
EXCEL_COLUMN_MAP: dict[str, str] = {
    '序号': 'serial_number',
    '订单状态': 'order_status',
    '创建人': 'created_by',
    '创建时间': 'created_at',
    '*下单日期': 'order_date',
    '*客户': 'customer',
    '跟单员': 'order_follower',
    '*订单类型': 'order_type',
    '*订单类似': 'order_type',
    '订单号': 'order_number',
    '款号': 'style_number',
    '规格': 'specification',
    '布种': 'cloth_type',
    '纺织类型': 'textile_type',
    '颜色': 'color',
    '色号': 'color_code',
    '成份': 'composition',
    '门幅': 'width',
    '克重': 'weight',
    '加工别': 'processing_type',
    '*订单数量': 'order_quantity',
    '单位': 'quantity_unit',
    '*价格': 'price',
    '单位.1': 'price_unit',
    '小缸费': 'small_vat_fee',
    '客人要求交期': 'customer_delivery_date',
    '大货进度跟踪': 'bulk_progress_tracking',
    '*成品出货对账总金额': 'finished_product_total_amount',
    '*付款方式\n（货前、货后）': 'payment_method',
    '付款方式\n（货前、货后）': 'supplier_payment_method',
    '*账期/天': 'payment_period',
    '*对账时间': 'reconciliation_date',
    '*货款支付时间': 'payment_date',
    '*是否付款': 'payment_status',
    '*是否逾期': 'overdue_status',
    '*是否开票': 'invoice_status',
    '*是否开证': 'certificate_status',
    '*证书类型': 'certificate_type',
    '最迟开证时间': 'latest_certificate_date',
    '实际操作时间': 'actual_operation_date',
    '实际开证时间': 'actual_certificate_date',
    '*成品供应商': 'finished_product_supplier',
    '档口': 'booth',
    '编号': 'supplier_code',
    '合同号': 'contract_number',
    '是否开票': 'supplier_invoice_status',
    '是否开票.1': 'supplier_invoice_status',
    '是否开证书': 'supplier_certificate_status',
    '对账时间': 'supplier_reconciliation_date',
    '货款支付时间': 'supplier_payment_date',
    '*已付款': 'supplier_paid',
    '*总金额': 'total_amount',
    '*成品成本价格': 'finished_product_cost_price',
    '单位.2': 'cost_price_unit',
    '*出货总数量': 'total_shipment_quantity',
    '单位.3': 'shipment_quantity_unit',
    '*成品出货时间': 'finished_product_shipment_date_1',
    '*成品出货数量': 'finished_product_shipment_quantity_1',
    '成品出货时间': 'finished_product_shipment_date_2',
    '成品出货数量': 'finished_product_shipment_quantity_2',
    '成品出货时间.1': 'finished_product_shipment_date_3',
    '成品出货数量.1': 'finished_product_shipment_quantity_3',
    '成品出货时间.2': 'finished_product_shipment_date_4',
    '成品出货数量.2': 'finished_product_shipment_quantity_4',
    '成品出货时间.3': 'finished_product_shipment_date_5',
    '成品出货数量.3': 'finished_product_shipment_quantity_5',
    '备注': 'remark',
    '地址': 'address',
    # —— 供应商订单表（供应商界面导出/备份）列名别名 ——
    '数量单位': 'quantity_unit',
    '档口编号': 'supplier_code',
    '昇奕隆编号': 'cloth_type',
    '供应商已出货': 'supplier_shipped',
    '出货数量单位': 'shipment_quantity_unit',
    '给供应商备注': 'supplier_remark',
    '供应商备注': 'remark',
}

# 出货记录列（供应商订单表固定 7 组，兼容更多批次）
SHIPMENT_COLUMN_PATTERN = re.compile(r'^出货(批次|日期|数量)(\d+)$')

DATE_FIELDS = {
    f for f in EXCEL_COLUMN_MAP.values()
    if f.endswith('_date') or 'date' in f
}
YES_NO_FIELDS = set()
BOOL_FIELDS = {'supplier_paid', 'supplier_shipped'}

DECIMAL_FIELDS = {
    'order_quantity', 'price', 'small_vat_fee', 'width', 'weight',
    'finished_product_total_amount', 'total_amount',
    'finished_product_cost_price', 'total_shipment_quantity',
    'finished_product_shipment_quantity_1', 'finished_product_shipment_quantity_2',
    'finished_product_shipment_quantity_3', 'finished_product_shipment_quantity_4',
    'finished_product_shipment_quantity_5',
}
INTEGER_FIELDS = {'serial_number', 'payment_period'}

MODEL_FIELD_NAMES = {
    f.name for f in ClothOrder._meta.get_fields()
    if hasattr(f, 'column') and not f.many_to_many
}

IMPORT_DEFAULTS = {
    'order_type': 'bulk',
    'payment_status': 'unpaid',
    'overdue_status': 'not_overdue',
    'invoice_status': 'not_invoiced',
    'certificate_status': 'not_invoiced',
    'payment_method': 'before_delivery',
    'certificate_type': 'none',
    'bulk_progress_tracking': '',
    'order_status': 'active',
}

# 导出列定义：(Excel 表头, 模型字段)；模型字段为 None 时输出空列。
# 顺序与客户端汇总表保持一致，未在下列的字段不导出。
EXPORT_COLUMNS: list[tuple[str, Optional[str]]] = [
    ("序号", "serial_number"),
    ("订单状态", "order_status"),
    ("*下单日期", "order_date"),
    ("*客户", "customer"),
    ("跟单员", "order_follower"),
    ("*订单类似", "order_type"),
    ("订单号", "order_number"),
    ("款号", "style_number"),
    ("规格", "specification"),
    ("布种", "cloth_type"),
    ("纺织类型", "textile_type"),
    ("颜色", "color"),
    ("色号", "color_code"),
    ("成份", "composition"),
    ("门幅", "width"),
    ("克重", "weight"),
    ("加工别", "processing_type"),
    ("*订单数量", "order_quantity"),
    ("单位", "quantity_unit"),
    ("*价格", "price"),
    ("单位", "price_unit"),
    ("大货进度跟踪", "bulk_progress_tracking"),
    ("*成品出货对账总金额", "finished_product_total_amount"),
    ("*付款方式\n（货前、货后）", "payment_method"),
    ("*账期/天", "payment_period"),
    ("*对账时间", "reconciliation_date"),
    ("*货款支付时间", "payment_date"),
    ("*是否付款", "payment_status"),
    ("*是否逾期", "overdue_status"),
    ("*是否开票", "invoice_status"),
    ("*是否开证", "certificate_status"),
    ("*证书类型", "certificate_type"),
    ("最迟开证时间", "latest_certificate_date"),
    ("实际操作时间", "actual_operation_date"),
    ("实际开证时间", "actual_certificate_date"),
    ("*成品供应商", "finished_product_supplier"),
    ("档口", "booth"),
    ("编号", "supplier_code"),
    ("对账时间", "supplier_reconciliation_date"),
    ("货款支付时间", "supplier_payment_date"),
    ("*已付款", "supplier_paid"),
    ("*总金额", "total_amount"),
    ("*成品成本价格", "finished_product_cost_price"),
    ("单位", "cost_price_unit"),
    ("*出货总数量", "total_shipment_quantity"),
    ("单位", "shipment_quantity_unit"),
    ("*针织布出货公斤数", None),
    ("*成品出货时间", "finished_product_shipment_date_1"),
    ("*成品出货数量", "finished_product_shipment_quantity_1"),
    ("成品出货时间", "finished_product_shipment_date_2"),
    ("成品出货数量", "finished_product_shipment_quantity_2"),
    ("成品出货时间", "finished_product_shipment_date_3"),
    ("成品出货数量", "finished_product_shipment_quantity_3"),
    ("成品出货时间", "finished_product_shipment_date_4"),
    ("成品出货数量", "finished_product_shipment_quantity_4"),
    ("成品出货时间", "finished_product_shipment_date_5"),
    ("成品出货数量", "finished_product_shipment_quantity_5"),
    ("创建人", "created_by"),
    ("创建时间", "created_at"),
]

# 财务对账表格：不需要订单状态，也不需要创建人 / 创建时间
FINANCE_EXPORT_COLUMNS: list[tuple[str, Optional[str]]] = [
    column for column in EXPORT_COLUMNS
    if column[1] not in ("order_status", "created_by", "created_at")
]

# 按创建人分组导出：财务对账字段 + 创建人 / 创建时间
GROUPED_EXPORT_COLUMNS: list[tuple[str, Optional[str]]] = [
    *FINANCE_EXPORT_COLUMNS,
    ("创建人", "created_by"),
    ("创建时间", "created_at"),
]

# 完整备份：订单数据（含订单状态）+ 出货记录 + 订单进度 + 创建人/时间 + 供应商账号
FULL_BACKUP_ORDER_COLUMNS: list[tuple[str, Optional[str]]] = [
    column for column in EXPORT_COLUMNS
    if column[1] not in ("created_by", "created_at")
]
FULL_BACKUP_TAIL_COLUMNS: list[tuple[str, Optional[str]]] = [
    ("当前进度阶段", None),
    ("进度百分比", None),
    ("进度明细", None),
    ("创建人", "created_by"),
    ("创建时间", "created_at"),
    ("供应商公司", None),
    ("供应商账号", None),
]
# 批次列最少保留 5 组，与旧版固定 5 个批次字段保持一致
MIN_BACKUP_BATCH_COLUMNS = 5

SUPPLIER_EXPORT_COLUMNS: list[tuple[str, str]] = [
    ('序号', 'serial_number'),
    ('订单状态', 'order_status'),
    ('下单日期', 'order_date'),
    ('客户', 'customer'),
    ('跟单员', 'order_follower'),
    ('订单类型', 'order_type'),
    ('订单号', 'order_number'),
    ('款号', 'style_number'),
    ('昇奕隆编号', 'cloth_type'),
    ('纺织类型', 'textile_type'),
    ('档口', 'booth'),
    ('档口编号', 'supplier_code'),
    ('颜色', 'color'),
    ('色号', 'color_code'),
    ('成份', 'composition'),
    ('门幅', 'width'),
    ('克重', 'weight'),
    ('规格', 'specification'),
    ('订单数量', 'order_quantity'),
    ('数量单位', 'quantity_unit'),
    ('客人要求交期', 'customer_delivery_date'),
    ('成品成本价格', 'finished_product_cost_price'),
    ('成本价格单位', 'cost_price_unit'),
    ('出货总数量', 'total_shipment_quantity'),
    ('出货数量单位', 'shipment_quantity_unit'),
    ('供应商已出货', 'supplier_shipped'),
    ('地址', 'address'),
    ('给供应商备注', 'supplier_remark'),
    ('供应商备注', 'remark'),
]


def _normalize_header(name: Any) -> str:
    text = str(name).replace('*', '').replace('\n', ' ').strip()
    return re.sub(r'\s+', ' ', text)


def _is_serial_header(name: str) -> bool:
    return _normalize_header(name) == '序号'


def map_dataframe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """将 Excel 原始列名映射为模型字段名（先精确匹配，再归一化匹配）。"""
    normalized_map: dict[str, str] = {}
    for excel_col, model_field in EXCEL_COLUMN_MAP.items():
        if model_field in MODEL_FIELD_NAMES:
            normalized_map[_normalize_header(excel_col)] = model_field

    rename_map: dict[str, str] = {}
    for col in df.columns:
        col_str = str(col).strip()
        if col_str in EXCEL_COLUMN_MAP and EXCEL_COLUMN_MAP[col_str] in MODEL_FIELD_NAMES:
            rename_map[col] = EXCEL_COLUMN_MAP[col_str]
            continue
        norm = _normalize_header(col_str)
        if norm in normalized_map:
            rename_map[col] = normalized_map[norm]
    return df.rename(columns=rename_map)


def _cell_has_strikethrough(cell) -> bool:
    """检测单元格字体是否带删除线。"""
    if cell is None or cell.value is None:
        return False
    font = cell.font
    if font is None:
        return False
    return bool(getattr(font, 'strike', None)) or bool(getattr(font, 'strikethrough', None))


def _row_has_data_from_cells(cells_by_col: dict[int, Any]) -> bool:
    for cell in cells_by_col.values():
        if cell.value is not None and str(cell.value).strip() != '':
            return True
    return False


def _row_is_cancelled_from_cells(
    cells_by_col: dict[int, Any],
    strike_check_cols: set[int],
    serial_col: Optional[int],
) -> bool:
    """
    汇总表约定：行内带删除线表示已取消订单。
    仅检查序号列及前几列有内容单元格，避免全表扫描过慢。
    """
    if serial_col and serial_col in cells_by_col:
        if _cell_has_strikethrough(cells_by_col[serial_col]):
            return True

    struck = 0
    filled = 0
    for col in strike_check_cols:
        cell = cells_by_col.get(col)
        if cell is None:
            continue
        if cell.value is None or str(cell.value).strip() == '':
            continue
        filled += 1
        if _cell_has_strikethrough(cell):
            struck += 1

    if filled == 0:
        return False
    return struck >= max(1, (filled + 1) // 2)


def read_bulk_sheet_with_status(
    file_obj,
    progress_callback: Optional[Callable[..., None]] = None,
) -> tuple[pd.DataFrame, list[bool]]:
    """
    用 openpyxl 读取「大货」表：返回 DataFrame 及每行是否已取消（删除线）。
    使用 iter_rows 单次遍历（read_only + cell() 随机访问会导致极慢甚至假死）。
    """
    raw = file_obj.read()
    file_obj.seek(0)

    _report_progress(progress_callback, 3, '正在打开 Excel 文件…', 'read')

    try:
        wb = load_workbook(filename=BytesIO(raw), data_only=True, read_only=False)
    except Exception as exc:
        raise ValueError('请上传 .xlsx 格式文件（需读取删除线格式，不支持旧版 .xls）') from exc

    sheet_name = SHEET_BULK if SHEET_BULK in wb.sheetnames else wb.sheetnames[0]
    ws = wb[sheet_name]

    _report_progress(progress_callback, 5, '正在读取表头…', 'read')

    header_cells = next(ws.iter_rows(min_row=1, max_row=1, values_only=False))
    headers: dict[int, str] = {}
    seen_headers: dict[str, int] = {}
    for col_idx, cell in enumerate(header_cells, start=1):
        if cell.value is not None and str(cell.value).strip():
            raw = str(cell.value).strip()
            # 处理重复列名（如单位、单位.1）→与pandas读取时一致
            if raw in seen_headers:
                seen_headers[raw] += 1
                raw = f"{raw}.{seen_headers[raw]}"
            else:
                seen_headers[raw] = 0
            headers[col_idx] = raw

    if not headers:
        wb.close()
        return pd.DataFrame(), []

    min_col = min(headers.keys())
    max_col = max(headers.keys())
    serial_col = next((c for c, h in headers.items() if _is_serial_header(h)), None)
    strike_check_cols = set(list(headers.keys())[:15])
    if serial_col:
        strike_check_cols.add(serial_col)

    rows: list[dict[str, Any]] = []
    cancelled_flags: list[bool] = []
    empty_streak = 0
    max_empty_streak = 80
    scanned = 0
    sheet_max_row = ws.max_row or 1
    estimated = max(sheet_max_row - 1, 1)

    _report_progress(progress_callback, 6, '正在扫描数据行（检测删除线）…', 'read', 0, estimated)

    for row_cells in ws.iter_rows(
        min_row=2,
        min_col=min_col,
        max_col=max_col,
        values_only=False,
    ):
        scanned += 1
        if scanned % 100 == 0:
            pct = 6 + min(6, int(6 * scanned / estimated))
            _report_progress(
                progress_callback, pct,
                f'已扫描 {scanned} 行，有效 {len(rows)} 条…',
                'read', scanned, estimated,
            )

        cells_by_col: dict[int, Any] = {}
        for offset, cell in enumerate(row_cells):
            col_idx = min_col + offset
            if col_idx in headers:
                cells_by_col[col_idx] = cell

        if not _row_has_data_from_cells(cells_by_col):
            empty_streak += 1
            if empty_streak >= max_empty_streak:
                break
            continue
        empty_streak = 0

        cancelled_flags.append(
            _row_is_cancelled_from_cells(cells_by_col, strike_check_cols, serial_col)
        )
        rows.append({headers[col]: cells_by_col[col].value for col in headers if col in cells_by_col})

    wb.close()
    _report_progress(
        progress_callback, 12,
        f'解析完成，共 {len(rows)} 条有效数据',
        'read', len(rows), len(rows),
    )
    return pd.DataFrame(rows), cancelled_flags


def _scalar(value: Any) -> Any:
    if isinstance(value, pd.Series):
        return value.iloc[0] if len(value) else None
    return value


def _parse_date(value: Any):
    value = _scalar(value)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y-%m', '%Y/%m'):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        try:
            serial = float(text)
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
        except ValueError:
            return None
    try:
        parsed = pd.to_datetime(value, errors='coerce')
        if pd.notna(parsed):
            return parsed.date()
    except (TypeError, ValueError):
        pass
    return None


def _parse_decimal(value: Any):
    value = _scalar(value)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        text = str(value).strip().replace(',', '')
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None


def _parse_int(value: Any) -> Optional[int]:
    num = _parse_decimal(value)
    if num is None:
        return None
    return int(num)


def _parse_datetime(value: Any):
    """解析 Excel 单元格里的日期时间（支持纯日期、Excel 序列号、时间字符串）。"""
    value = _scalar(value)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    result = None
    if isinstance(value, pd.Timestamp):
        result = value.to_pydatetime()
    elif isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            result = datetime(1899, 12, 30) + timedelta(days=float(text))
        except ValueError:
            try:
                parsed = pd.to_datetime(text, errors='coerce')
            except (TypeError, ValueError):
                return None
            if pd.isna(parsed):
                return None
            result = parsed.to_pydatetime()
    return _aware(result)


def _aware(value):
    """按当前时区为 naive datetime 补上 tzinfo，避免 Django 时区告警与时间偏差。"""
    if value is None:
        return None
    from django.utils import timezone

    if timezone.is_naive(value):
        try:
            return timezone.make_aware(value, timezone.get_current_timezone())
        except Exception:
            return value
    return value


_USER_CACHE: dict[str, Any] = {}


def _resolve_user(name: Any):
    """根据 Excel 「创建人」列匹配 auth.User，匹配不到返回 None（回退到当前登录用户）。"""
    text = str(_scalar(name) or '').strip()
    if not text:
        return None
    if text in _USER_CACHE:
        return _USER_CACHE[text]
    from django.contrib.auth.models import User

    user = (
        User.objects.filter(username=text).first()
        or User.objects.filter(first_name=text).first()
        or User.objects.filter(last_name=text).first()
    )
    _USER_CACHE[text] = user
    return user


def _parse_yes_no(value: Any) -> Optional[str]:
    text = str(_scalar(value) or '').strip().lower()
    if text in {'是', 'yes', 'true', 'y', '1', '已付款', '已付'}:
        return 'yes'
    if text in {'否', 'no', 'false', 'n', '0', '未付款', '未付'}:
        return 'no'
    return None


def _parse_bool(value: Any) -> Optional[bool]:
    """解析布尔值，支持Excel中的是/否、yes/no等"""
    text = str(_scalar(value) or '').strip().lower()
    if text in {'是', 'yes', 'true', 'y', '1', '已付款', '已付'}:
        return True
    if text in {'否', 'no', 'false', 'n', '0', '未付款', '未付'}:
        return False
    return None


def _parse_yes_no_status(value: Any, field_name: str) -> str:
    text = str(_scalar(value) or "").strip()

    _PAYMENT = {  # payment_status, supplier_payment_method
        "paid": {"paid", "已付款", "已付", "付清", "已付清", "yes", "true", "是", "y", "1"},
        "unpaid": {"unpaid", "未付款", "未付", "未付清", "no", "false", "否", "n", "0"},
        "partial": {"partial", "部分付款", "部分"},
    }
    _OVERDUE = {  # overdue_status
        "overdue": {"overdue", "逾期", "过期", "超期", "已逾期"},
        "not_overdue": {"not_overdue", "未逾期", "未过期", "正常"},
    }
    _INVOICE = {  # invoice_status, certificate_status, supplier_*
        "invoiced": {"invoiced", "已开票", "已开证", "已开证书", "已开", "是", "yes", "y", "1"},
        "not_invoiced": {"not_invoiced", "未开票", "未开证", "未开证书", "未开", "否", "no", "n", "0"},
    }

    mapping = _OVERDUE if field_name == "overdue_status" else _PAYMENT if "payment" in field_name else _INVOICE

    low = text.lower()
    for result_key, aliases in mapping.items():
        for alias in aliases:
            if text == alias or low == alias.lower():
                return result_key

    return "not_overdue" if field_name == "overdue_status" else "unpaid" if "payment" in field_name else "not_invoiced"


def _parse_choice_field(field_name: str, value: Any) -> Any:
    text = str(_scalar(value) or '').strip()
    if not text:
        return None
    if field_name == 'order_type':
        if '印花' in text:
            return 'bulk_print'
        if '大货订单' in text:
            return 'bulk'
        # 「样品订单」「样板单」都归为样品单（汇总表实际写作「样品订单」）
        if '样' in text:
            return 'sample'
        return 'other'
    if field_name == 'order_status':
        return 'cancelled' if '取消' in text else 'active'
    if field_name in ('payment_method', 'supplier_payment_method'):
        if '货后' in text:
            return 'after_delivery'
        if '货前' in text:
            return 'before_delivery'
        return 'before_delivery'
    if field_name == 'certificate_type':
        upper = text.upper()
        if 'GRS' in upper:
            return 'grs'
        if 'OEKO' in upper or 'TEX' in upper:
            return 'oeko'
        if '无' in text:
            return 'none'
        return 'other'
    if 'status' in field_name and field_name != 'order_status':
        return _parse_yes_no_status(value, field_name)
    return text


def _extract_shipments(row: pd.Series) -> list[dict[str, Any]]:
    """
    从「出货批次N / 出货日期N / 出货数量N」三列提取出货记录。
    批次列可缺失（此时按列序号编号）；日期和数量都为空的组视为无效。
    """
    batches: dict[int, dict[str, Any]] = {}
    for col in row.index:
        match = SHIPMENT_COLUMN_PATTERN.match(str(col).strip())
        if not match:
            continue
        kind, index_text = match.groups()
        position = int(index_text)
        raw = row[col]
        if kind == '批次':
            value = _parse_int(raw)
            if value is not None:
                batches.setdefault(position, {})['batch_number'] = value
        elif kind == '日期':
            value = _parse_date(raw)
            if value is not None:
                batches.setdefault(position, {})['date'] = value
        else:
            value = _parse_decimal(raw)
            if value is not None:
                batches.setdefault(position, {})['quantity'] = value

    shipments = []
    for position in sorted(batches):
        item = batches[position]
        if item.get('date') is None and item.get('quantity') is None:
            continue
        shipments.append({
            'batch_number': item.get('batch_number') or position,
            'date': item.get('date'),
            'quantity': item.get('quantity'),
        })
    return shipments


def parse_row(row: pd.Series) -> dict[str, Any]:
    """将一行 DataFrame 数据解析为模型字段字典。"""
    data: dict[str, Any] = {}
    shipments = _extract_shipments(row)
    if shipments:
        data['_shipments'] = shipments
    for col in row.index:
        if col not in MODEL_FIELD_NAMES:
            continue
        raw = row[col]
        if pd.isna(_scalar(raw)):
            continue
        if col in DATE_FIELDS:
            parsed = _parse_date(raw)
            if parsed is not None:
                data[col] = parsed
        elif col in YES_NO_FIELDS:
            parsed = _parse_yes_no(raw)
            if parsed is not None:
                data[col] = parsed
        elif col in BOOL_FIELDS:
            parsed = _parse_bool(raw)
            if parsed is not None:
                data[col] = parsed
        elif col in DECIMAL_FIELDS:
            parsed = _parse_decimal(raw)
            if parsed is not None:
                data[col] = parsed
        elif col in INTEGER_FIELDS:
            parsed = _parse_int(raw)
            if parsed is not None:
                data[col] = parsed
        elif col == 'created_by':
            user = _resolve_user(raw)
            if user is not None:
                data[col] = user
        elif col == 'created_at':
            parsed = _parse_datetime(raw)
            if parsed is not None:
                data[col] = parsed
        elif col in (
            'order_type', 'order_status', 'payment_method', 'supplier_payment_method',
            'certificate_type', 'payment_status', 'overdue_status',
            'invoice_status', 'certificate_status',
            'supplier_invoice_status', 'supplier_certificate_status',
        ):
            parsed = _parse_choice_field(col, raw)
            if parsed is not None:
                data[col] = parsed
        else:
            data[col] = str(_scalar(raw)).strip()

    # 单位与价格单位自动关联
    if "quantity_unit" in data and data["quantity_unit"] and "price_unit" not in data:
        u = data["quantity_unit"].strip()
        if u == "米":
            data["price_unit"] = "元/米"
        elif u == "码":
            data["price_unit"] = "元/码"

    # 出货总数量 = 5个分批次出货数量自动求和
    shipment_fields = [
        'finished_product_shipment_quantity_1',
        'finished_product_shipment_quantity_2',
        'finished_product_shipment_quantity_3',
        'finished_product_shipment_quantity_4',
        'finished_product_shipment_quantity_5',
    ]
    total = sum(
        float(data[f]) for f in shipment_fields
        if f in data and data[f] is not None
    )
    if total > 0:
        data['total_shipment_quantity'] = total

    return data


@dataclass
class ImportResult:
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    cancelled: int = 0
    reactivated: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.errors == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            'imported': self.imported,
            'updated': self.updated,
            'skipped': self.skipped,
            'cancelled': self.cancelled,
            'reactivated': self.reactivated,
            'errors': self.errors,
            'error_messages': self.error_messages[:30],
            'success': self.success,
        }


def _report_progress(
    callback: Optional[Callable[..., None]],
    percent: int,
    message: str,
    phase: str = 'import',
    current: int = 0,
    total: int = 0,
) -> None:
    if callback:
        callback(
            percent=min(100, max(0, percent)),
            message=message,
            phase=phase,
            current=current,
            total=total,
        )


def import_orders_from_file(
    file_obj,
    progress_callback: Optional[Callable[..., None]] = None,
    created_by=None,
) -> ImportResult:
    """
    从 Excel 导入/更新订单（按序号 upsert）。
    带删除线的行标记为 order_status=cancelled，否则为 active。
    created_by：默认创建人（auth.User 实例）。Excel 「创建人」列有值且能匹配到用户时以 Excel 为准，
    匹配不到则回退到该默认值；Excel 「创建时间」列有值时按该时间写入。
    """
    df, cancelled_flags = read_bulk_sheet_with_status(file_obj, progress_callback=progress_callback)
    df = map_dataframe_columns(df)
    result = ImportResult()

    total_rows = len(df)
    if total_rows == 0:
        _report_progress(progress_callback, 100, '工作表中没有可导入的数据行', 'done', 0, 0)
        return result

    _report_progress(
        progress_callback, 12,
        f'共 {total_rows} 条，正在写入数据库…',
        'import', 0, total_rows,
    )

    with transaction.atomic():
        for i, (index, row) in enumerate(df.iterrows()):
            if i % 5 == 0 or i == total_rows - 1:
                pct = 12 + int(83 * (i + 1) / total_rows)
                _report_progress(
                    progress_callback, pct,
                    f'正在处理 {i + 1} / {total_rows} 条…',
                    'import', i + 1, total_rows,
                )
            try:
                row_data = parse_row(row)
                # created_at 是 auto_now_add，不能随 create/setattr 直接写入，单独取出后回写
                created_at_value = row_data.pop('created_at', None)
                # 出货批次列不写进订单字段，导入后写入 Shipment 表
                shipments = row_data.pop('_shipments', None)
                serial_number = row_data.get('serial_number')
                if serial_number is None:
                    result.skipped += 1
                    continue
                # 缺少下单日期的行不导入
                if not row_data.get('order_date'):
                    result.skipped += 1
                    continue

                # 检测是否已有出货数据，有则标记供应商已出货
                if row_data.get('total_amount') and row_data.get('total_shipment_quantity') and row_data.get('finished_product_cost_price'):
                    row_data['supplier_shipped'] = True

                is_cancelled = cancelled_flags[i] if i < len(cancelled_flags) else False
                if 'order_status' not in row_data:
                    row_data['order_status'] = 'cancelled' if is_cancelled else 'active'
                else:
                    is_cancelled = (row_data['order_status'] == 'cancelled')

                for key, default in IMPORT_DEFAULTS.items():
                    if key != 'order_status':
                        row_data.setdefault(key, default)

                _ensure_customer(row_data.get("customer"))
                existing = ClothOrder.objects.filter(serial_number=serial_number).first()
                if existing:
                    was_cancelled = existing.order_status == 'cancelled'
                    for key, value in row_data.items():
                        if value is not None and key != 'serial_number':
                            setattr(existing, key, value)
                    # Excel 带了创建时间就按 Excel 的（更新时 auto_now_add 不再覆盖）
                    if created_at_value is not None:
                        existing.created_at = created_at_value
                    existing.save()
                    if shipments:
                        _write_shipments(existing, shipments, created_by)
                    else:
                        _sync_shipments_from_order(existing)
                    _match_supplier(existing)
                    result.updated += 1
                    if is_cancelled and not was_cancelled:
                        result.cancelled += 1
                    elif not is_cancelled and was_cancelled:
                        result.reactivated += 1
                else:
                    create_kwargs = dict(row_data)
                    # Excel 没提供创建人时，默认当前登录用户
                    if created_by is not None:
                        create_kwargs.setdefault("created_by", created_by)
                    new_order = ClothOrder.objects.create(**create_kwargs)
                    # 创建后回写创建时间，绕过 auto_now_add
                    if created_at_value is not None:
                        ClothOrder.objects.filter(pk=new_order.pk).update(created_at=created_at_value)
                        new_order.created_at = created_at_value
                    if shipments:
                        _write_shipments(new_order, shipments, created_by)
                    else:
                        _sync_shipments_from_order(new_order)
                    _match_supplier(new_order)
                    result.imported += 1
                    if is_cancelled:
                        result.cancelled += 1
            except Exception as exc:
                result.errors += 1
                result.error_messages.append(f'第 {index + 2} 行: {exc}')

    _report_progress(progress_callback, 100, '导入完成', 'done', total_rows, total_rows)
    fix_imported_progress_stages()
    return result


def _match_supplier(order):
    if order.supplier_id or not order.finished_product_supplier:
        return
    try:
        supplier = Supplier.objects.filter(
            company_name__iexact=order.finished_product_supplier.strip()
        ).first()
        if supplier:
            order.supplier = supplier
            order.save(update_fields=["supplier"])
    except Exception:
        pass


def _ensure_customer(name):
    """订单导入时自动补充不存在的客户。"""
    name = str(name or "").strip()
    if name:
        Customer.objects.get_or_create(name=name)


def _sync_shipments_from_order(order):
    """从订单的旧批次字段同步出货记录到 Shipment 表"""
    from .models import Shipment

    for i in range(1, 6):
        date = getattr(order, f'finished_product_shipment_date_{i}', None)
        qty = getattr(order, f'finished_product_shipment_quantity_{i}', None)
        if date or qty:
            Shipment.objects.update_or_create(
                order=order,
                batch_number=i,
                is_deleted=False,
                defaults={"date": date, "quantity": qty},
            )


def _write_shipments(order, shipments, created_by=None):
    """
    按批次号写入/更新订单出货记录，并同步「出货总数量」。
    shipments：_extract_shipments 解析出的列表，元素含 batch_number/date/quantity。
    """
    from .models import Shipment

    existing = {
        item.batch_number: item
        for item in Shipment.objects.filter(order=order, is_deleted=False)
    }
    for shipment in shipments:
        batch_number = shipment['batch_number']
        current = existing.get(batch_number)
        if current is None:
            Shipment.objects.create(
                order=order,
                batch_number=batch_number,
                date=shipment['date'],
                quantity=shipment['quantity'],
                created_by=created_by,
            )
            continue
        changed_fields = []
        if shipment['date'] is not None and current.date != shipment['date']:
            current.date = shipment['date']
            changed_fields.append('date')
        if shipment['quantity'] is not None and current.quantity != shipment['quantity']:
            current.quantity = shipment['quantity']
            changed_fields.append('quantity')
        if changed_fields:
            current.save(update_fields=changed_fields)

    total = order.total_shipment_from_shipments()
    order.total_shipment_quantity = total
    order.save(update_fields=['total_shipment_quantity'])


def fix_imported_progress_stages():
    """导入后将订单进度阶段与订单类型匹配"""
    for order_type, stages in DEFAULT_STAGES_MAP.items():
        count = ClothOrder.objects.filter(order_type=order_type).exclude(progress_stages=stages).update(progress_stages=stages)
        if count:
            logger.info(f"  {order_type}: {count} 条更新")


def _display_value(order: ClothOrder, field_name: str):
    value = getattr(order, field_name, None)
    if value is None:
        return None

    # 所有 choices 字段统一取中文显示名（如 invoiced -> 已开票）
    display = getattr(order, f"get_{field_name}_display", None)
    if callable(display):
        return display()

    # 布尔字段显示为是/否
    if field_name in ("supplier_paid", "supplier_shipped"):
        return "是" if value else "否"

    # 创建人显示用户名
    if field_name == "created_by":
        return value.username if value is not None else None

    if hasattr(value, "isoformat"):
        # 带时区的时间转为本地时区（Asia/Shanghai）后再导出，避免早 8 小时
        if isinstance(value, datetime):
            from django.utils.timezone import is_aware, localtime
            if is_aware(value):
                return localtime(value).replace(tzinfo=None)
        return value
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


_SHIPMENT_DATE_RE = re.compile(r'^finished_product_shipment_date_(\d+)$')
_SHIPMENT_QUANTITY_RE = re.compile(r'^finished_product_shipment_quantity_(\d+)$')


def _load_shipments_by_order(order_ids):
    """批量预取出货记录：{order_id: {batch_number: {date, quantity}}}。"""
    from collections import defaultdict
    from .models import Shipment

    result: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    order_ids = [pk for pk in order_ids if pk is not None]
    if not order_ids:
        return result
    rows = (
        Shipment.objects
        .filter(order_id__in=order_ids, is_deleted=False)
        .values('batch_number', 'date', 'quantity', 'order_id')
    )
    for row in rows:
        result[row['order_id']][row['batch_number']] = row
    return result


def _order_shipment_value(model_field, shipments):
    """出货相关列从 Shipment 表取数；返回 None 时由调用方回退模型字段。"""
    if not shipments or not model_field:
        return None
    match = _SHIPMENT_DATE_RE.match(model_field)
    if match:
        shipment = shipments.get(int(match.group(1)))
        if shipment and shipment.get('date') is not None:
            return shipment['date']
        return None
    match = _SHIPMENT_QUANTITY_RE.match(model_field)
    if match:
        shipment = shipments.get(int(match.group(1)))
        if shipment and shipment.get('quantity') is not None:
            return float(shipment['quantity'])
        return None
    if model_field == 'total_shipment_quantity':
        total = sum(float(item.get('quantity') or 0) for item in shipments.values())
        if total:
            return total
    return None


def _export_cell(order, model_field, shipments):
    """单列取值：出货列优先取 Shipment 表，取不到再回退模型字段的显示值。"""
    if not model_field:
        return None
    value = _order_shipment_value(model_field, shipments)
    if value is None:
        value = _display_value(order, model_field)
    return value


def _build_export_dataframe(orders, columns, shipments_map) -> pd.DataFrame:
    rows = [
        [_export_cell(order, model_field, shipments_map.get(order.pk, {}))
         for _excel_col, model_field in columns]
        for order in orders
    ]
    return pd.DataFrame(rows, columns=[excel_col for excel_col, _ in columns])


def export_orders_dataframe(queryset=None, columns=None) -> pd.DataFrame:
    """将订单 queryset 转为导出 DataFrame（默认列顺序严格按 EXPORT_COLUMNS）。

    出货时间/数量列优先取 Shipment 表（界面维护的出货记录），
    旧的分批次字段仅作为兜底，避免导出时出货记录全部空白。
    """
    columns = columns or EXPORT_COLUMNS
    if queryset is None:
        queryset = (
            ClothOrder.objects.select_related("created_by").order_by(
                '-serial_number', '-order_date', '-created_at'
            )
        )

    orders = list(queryset)
    shipments_map = _load_shipments_by_order([order.pk for order in orders])
    return _build_export_dataframe(orders, columns, shipments_map)


def export_finance_dataframe(queryset=None) -> pd.DataFrame:
    """财务对账表格：不含订单状态、创建人、创建时间。"""
    return export_orders_dataframe(queryset, columns=FINANCE_EXPORT_COLUMNS)


def _shipment_summary(shipments):
    """返回（汇总文本, 按批次号排序的批次列表）。"""
    batches = sorted(shipments.values(), key=lambda item: item.get('batch_number') or 0)
    parts = []
    for batch in batches:
        date_text = batch['date'].strftime('%Y-%m-%d') if batch.get('date') else ''
        quantity = float(batch['quantity']) if batch.get('quantity') is not None else 0
        parts.append(f"批次{batch.get('batch_number')}:{date_text} 数量{quantity}")
    return ' | '.join(parts), batches


def _order_progress_text(order):
    """订单进度三元组：（当前阶段, 百分比, 全部阶段明细）。"""
    try:
        stages = order.stage_list()
    except Exception:
        stages = []
    try:
        current = order.current_stage_name
    except Exception:
        current = ''
    try:
        percent = order.progress_percent
    except Exception:
        percent = ''
    return current or '', percent, ' → '.join(str(stage) for stage in stages)


def export_full_backup_dataframe(queryset=None) -> pd.DataFrame:
    """完整备份：订单数据 + 出货记录 + 订单进度 + 创建人/时间 + 供应商账号。"""
    if queryset is None:
        queryset = (
            ClothOrder.objects.select_related("created_by", "supplier__user").order_by(
                '-serial_number', '-order_date', '-created_at'
            )
        )

    orders = list(queryset)
    shipments_map = _load_shipments_by_order([order.pk for order in orders])
    max_batches = max(
        [MIN_BACKUP_BATCH_COLUMNS] + [len(shipments) for shipments in shipments_map.values()]
    )

    columns = [*FULL_BACKUP_ORDER_COLUMNS, ("出货记录", None)]
    for index in range(max_batches):
        columns.extend([
            (f"出货批次{index + 1}", None),
            (f"出货日期{index + 1}", None),
            (f"出货数量{index + 1}", None),
        ])
    columns.extend(FULL_BACKUP_TAIL_COLUMNS)

    rows = []
    for order in orders:
        shipments = shipments_map.get(order.pk, {})
        summary, batches = _shipment_summary(shipments)
        row = [
            _export_cell(order, model_field, shipments)
            for _excel_col, model_field in FULL_BACKUP_ORDER_COLUMNS
        ]
        row.append(summary)
        for index in range(max_batches):
            batch = batches[index] if index < len(batches) else None
            row.append(batch.get('batch_number') if batch else '')
            row.append(batch['date'].strftime('%Y-%m-%d') if batch and batch.get('date') else '')
            row.append(float(batch['quantity']) if batch and batch.get('quantity') is not None else '')

        current_stage, percent, stage_text = _order_progress_text(order)
        supplier = getattr(order, 'supplier', None)
        row.extend([
            current_stage,
            percent,
            stage_text,
            _display_value(order, 'created_by'),
            _display_value(order, 'created_at'),
            supplier.company_name if supplier else '',
            supplier.user.username if supplier and supplier.user_id else '',
        ])
        rows.append(row)

    return pd.DataFrame(rows, columns=[excel_col for excel_col, _ in columns])


def export_orders_grouped_dataframe(columns=None) -> dict[str, pd.DataFrame]:
    """按创建人（管理员）分组，返回 {分组名: DataFrame}，用于一个 Excel 多 Sheet 导出。"""
    from collections import defaultdict

    columns = columns or EXPORT_COLUMNS
    groups: dict[str, list[ClothOrder]] = defaultdict(list)
    orders = (
        ClothOrder.objects
        .select_related("created_by")
        .order_by("-order_date", "-serial_number")
    )
    for order in orders:
        name = order.created_by.username if order.created_by else "未分配"
        groups[name].append(order)

    return {
        name: export_orders_dataframe(group, columns=columns)
        for name, group in sorted(groups.items())
    }


# ---------------------------------------------------------------------------
# Excel 导出样式：表头底色、边框、冻结首行、自适应列宽

# xlsxwriter 使用 #RRGGBB 普通色值，写入标准 solid fill，并生成 sharedStrings.xml。
# 注：openpyxl 3.1+ 会把字符串写成 inlineStr 且不再生成 sharedStrings.xml，
# 微信 / 部分移动端预览器无法解析，导致表头等文本显示为空，因此导出统一改用 xlsxwriter。
# 表头样式：仅加粗（无背景色），兼容性最好。
BORDER_COLOR = "#D0D7DE"
HEADER_ROW_HEIGHT = 36
MIN_COL_WIDTH = 8
MAX_COL_WIDTH = 60
WIDTH_SAMPLE_ROWS = 500


def _visual_width(value: Any) -> int:
    """估算文本显示宽度：中文/全角字符按 2，其余按 1；多行取最长行。"""
    text = "" if value is None else str(value)
    if not text:
        return 0
    return max(
        sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in line)
        for line in text.splitlines()
    )


def write_dataframe_to_sheet(writer, df: pd.DataFrame, sheet_name: str):
    """把 DataFrame 写入指定 Sheet（xlsxwriter 引擎），统一表头样式、边框与列宽。"""
    df.to_excel(writer, sheet_name=sheet_name, index=False)
    ws = writer.sheets[sheet_name]

    if len(df.columns) == 0:
        return ws

    workbook = writer.book
    header_format = workbook.add_format({
        "bold": True,
        "align": "center",
        "valign": "vcenter",
        "text_wrap": True,
        "border": 1,
        "border_color": BORDER_COLOR,
    })
    data_format = workbook.add_format({
        "valign": "vcenter",
        "border": 1,
        "border_color": BORDER_COLOR,
    })

    # 表头行：格式 + 行高（行格式优先级高于列默认格式）
    ws.set_row(0, HEADER_ROW_HEIGHT, header_format)

    # 自适应列宽，并为数据列设置边框/垂直居中
    sample = df.head(WIDTH_SAMPLE_ROWS)
    for col_idx, column_name in enumerate(df.columns):
        width = _visual_width(column_name)
        if col_idx < sample.shape[1]:
            for value in sample.iloc[:, col_idx].tolist():
                width = max(width, _visual_width(value))
        ws.set_column(
            col_idx,
            col_idx,
            min(max(width + 4, MIN_COL_WIDTH), MAX_COL_WIDTH),
            data_format,
        )

    # 冻结首行 + 自动筛选
    ws.freeze_panes(1, 0)
    if len(df) > 0:
        ws.autofilter(0, 0, len(df), len(df.columns) - 1)

    return ws


def export_supplier_orders_dataframe(queryset=None) -> pd.DataFrame:
    """将供应商可见订单转换为 Excel 数据，不包含内部收款等字段。"""
    if queryset is None:
        queryset = ClothOrder.objects.all().order_by('-serial_number', '-order_date', '-pk')

    from collections import defaultdict
    from .models import Shipment

    shipment_rows = (
        Shipment.objects
        .filter(order__in=queryset, is_deleted=False)
        .order_by('order_id', 'batch_number')
        .values_list('order_id', 'batch_number', 'date', 'quantity')
    )
    shipments_by_order = defaultdict(list)
    max_shipment_count = 0
    for order_id, batch_number, ship_date, quantity in shipment_rows:
        shipments_by_order[order_id].append({
            'batch_number': batch_number,
            'date': ship_date,
            'quantity': quantity,
        })
        max_shipment_count = max(max_shipment_count, len(shipments_by_order[order_id]))

    rows = []
    for order in queryset:
        row = {}
        for excel_col, model_field in SUPPLIER_EXPORT_COLUMNS:
            value = getattr(order, model_field, None)
            if model_field == 'order_status':
                value = order.get_order_status_display()
            elif model_field == 'order_type':
                value = order.get_order_type_display()
            elif model_field == 'supplier_shipped':
                value = '是' if value else '否'
            elif hasattr(value, 'isoformat'):
                value = value.isoformat()
            row[excel_col] = value

        shipments = shipments_by_order.get(order.pk, [])
        total_quantity = sum(
            float(shipment['quantity'] or 0)
            for shipment in shipments
        )
        cost_price = float(order.finished_product_cost_price or 0)
        row['出货总数量'] = total_quantity
        row['出货总金额'] = round(cost_price * total_quantity, 2)

        summary_parts = []
        for shipment in shipments:
            date_text = shipment['date'].strftime('%Y-%m-%d') if shipment['date'] else ''
            quantity_text = float(shipment['quantity']) if shipment['quantity'] is not None else 0
            summary_parts.append(
                f"批次{shipment['batch_number']}:{date_text} 数量{quantity_text}"
            )
        row['出货记录'] = ' | '.join(summary_parts)

        for index in range(max_shipment_count):
            shipment = shipments[index] if index < len(shipments) else None
            row[f'出货批次{index + 1}'] = shipment['batch_number'] if shipment else ''
            row[f'出货日期{index + 1}'] = (
                shipment['date'].strftime('%Y-%m-%d')
                if shipment and shipment['date'] else ''
            )
            row[f'出货数量{index + 1}'] = (
                float(shipment['quantity'])
                if shipment and shipment['quantity'] is not None else ''
            )

        rows.append(row)

    columns = [excel_col for excel_col, _ in SUPPLIER_EXPORT_COLUMNS]
    columns.extend(['出货总金额', '出货记录'])
    for index in range(max_shipment_count):
        columns.extend([
            f'出货批次{index + 1}',
            f'出货日期{index + 1}',
            f'出货数量{index + 1}',
        ])
    return pd.DataFrame(rows, columns=columns)
