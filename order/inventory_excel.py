"""库存 Excel 导入 / 导出工具"""
import io
import pandas as pd
from .models import InventoryItem

# Excel 列名 → 模型字段 映射
COLUMN_MAP = {
    '序号':                   'serial_no',
    '布种类型':                'cloth_type_id',
    '库存数量':                'quantity',
    '唯一标识':                'unique_id',
    '布种/加工别':             'cloth_name',
    '颜色/COLOR':             'color',
    'COMPOSITION':            'composition_en',
    '成份':                   'composition_cn',
    '规格/SPECIFICATION':     'specification',
    'FINISHING':              'finishing_en',
    '整理':                   'finishing_cn',
    '幅宽/WIDTH（"）':        'width',
    '克重/WEIGTH（g/㎡）':   'weight',
    '正反面（SIDES）':         'sides',
    '客户/CLIENT':            'customer',
    '用途/USE':               'usage',
    '生产缸号/BATH.NO':       'bath_no',
    '存放位置/POSITION':      'position',
    '备注/REMARK':            'remark',
    '胚布编号':                'grey_fabric_no',
    '胚布价格':                'grey_fabric_price',
    '胚布来源':                'grey_fabric_source',
    '调胚时间':                'grey_fabric_date',
    '成品价格':                'finished_price',
}

INT_FIELDS   = {'serial_no', 'cloth_type_id', 'quantity'}
FLOAT_FIELDS = {'width', 'weight'}


def _clean(val, field):
    if pd.isna(val) or val == '' or str(val).strip() in ('nan', 'NaT', 'None'):
        return None if field in INT_FIELDS | FLOAT_FIELDS else ''
    if field in INT_FIELDS:
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return None
    if field in FLOAT_FIELDS:
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
    return str(val).strip()


def import_inventory_from_file(file_bytes):
    """
    从 xlsx bytes 导入库存，返回统计字典:
    {imported, updated, skipped, errors, error_messages}
    """
    df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)

    stats = dict(imported=0, updated=0, skipped=0, errors=0, error_messages=[])

    for idx, row in df.iterrows():
        row_num = idx + 2
        try:
            kwargs = {}
            for col, field in COLUMN_MAP.items():
                if col in df.columns:
                    kwargs[field] = _clean(row.get(col), field)

            # 必须有 serial_no
            if not kwargs.get('serial_no'):
                stats['skipped'] += 1
                continue

            serial = kwargs['serial_no']
            obj = InventoryItem.objects.filter(serial_no=serial).first()
            if obj:
                for k, v in kwargs.items():
                    if v is not None:
                        setattr(obj, k, v)
                obj.save()
                stats['updated'] += 1
            else:
                InventoryItem.objects.create(**{k: v for k, v in kwargs.items() if v is not None})
                stats['imported'] += 1

        except Exception as exc:
            stats['errors'] += 1
            stats['error_messages'].append(f'第 {row_num} 行：{exc}')

    return stats


def export_inventory_dataframe():
    """导出全部库存为 DataFrame"""
    qs = InventoryItem.objects.all().values(
        'serial_no', 'cloth_type_id', 'quantity', 'unique_id',
        'cloth_name', 'color', 'composition_en', 'composition_cn',
        'specification', 'finishing_en', 'finishing_cn',
        'width', 'weight', 'sides', 'customer', 'usage',
        'bath_no', 'position', 'remark',
        'grey_fabric_no', 'grey_fabric_price', 'grey_fabric_source',
        'grey_fabric_date', 'finished_price',
    )
    df = pd.DataFrame(list(qs))
    if df.empty:
        return df
    # 还原列名
    reverse_map = {v: k for k, v in COLUMN_MAP.items()}
    df = df.rename(columns=reverse_map)
    return df
