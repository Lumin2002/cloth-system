# ---------------------------------------------------------------------------
# 订单导入后台任务
# ---------------------------------------------------------------------------

"""导入任务进度（缓存 + 后台线程）。"""
import threading
import uuid
from typing import Any, Callable, Optional

from django.core.cache import cache

CACHE_PREFIX = 'order_import:'
CACHE_TTL = 86400


def _cache_key(task_id: str) -> str:
    return f'{CACHE_PREFIX}{task_id}'


def create_import_task(user_id: int) -> str:
    task_id = str(uuid.uuid4())
    cache.set(
        _cache_key(task_id),
        {
            'status': 'pending',
            'percent': 0,
            'message': '任务已创建，等待处理…',
            'phase': 'pending',
            'current': 0,
            'total': 0,
            'user_id': user_id,
            'result': None,
            'error': None,
        },
        CACHE_TTL,
    )
    return task_id


def get_import_task(task_id: str, user_id: Optional[int] = None) -> Optional[dict]:
    data = cache.get(_cache_key(task_id))
    if not data:
        return None
    if user_id is not None and data.get('user_id') != user_id:
        return None
    return data


def update_import_task(task_id: str, **fields) -> None:
    data = cache.get(_cache_key(task_id)) or {}
    data.update(fields)
    cache.set(_cache_key(task_id), data, CACHE_TTL)


ProgressCallback = Callable[..., None]


def run_import_in_background(task_id: str, file_bytes: bytes) -> None:
    """在后台线程执行导入并更新进度。"""
    from io import BytesIO

    from django.db import close_old_connections

    from .order_excel import import_orders_from_file

    close_old_connections()

    def on_progress(**kwargs):
        update_import_task(task_id, status='running', **kwargs)

    try:
        on_progress(percent=1, message='任务已开始…', phase='read')
        result = import_orders_from_file(BytesIO(file_bytes), progress_callback=on_progress)
        update_import_task(
            task_id,
            status='done',
            percent=100,
            message='导入完成',
            phase='done',
            result=result.to_dict(),
        )
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[导入订单] 导入完成：新增{result.imported}条 更新{result.updated}条 失败{result.errors}条 跳过{result.skipped}条")
    except Exception as exc:
        update_import_task(
            task_id,
            status='error',
            percent=100,
            message=f'导入失败：{exc}',
            phase='error',
            error=str(exc),
        )


def start_import_task(task_id: str, file_bytes: bytes) -> None:
    thread = threading.Thread(
        target=run_import_in_background,
        args=(task_id, file_bytes),
        daemon=True,
    )
    thread.start()


# ---------------------------------------------------------------------------
# 库存导入后台任务
# ---------------------------------------------------------------------------

INVENTORY_CACHE_PREFIX = 'inventory_import:'


def _inventory_cache_key(task_id: str) -> str:
    return f'{INVENTORY_CACHE_PREFIX}{task_id}'


def create_inventory_task(user_id: int) -> str:
    task_id = str(uuid.uuid4())
    cache.set(
        _inventory_cache_key(task_id),
        {
            'status': 'pending',
            'percent': 0,
            'message': '任务已创建，等待处理…',
            'current': 0,
            'total': 0,
            'user_id': user_id,
            'result': None,
            'error': None,
        },
        CACHE_TTL,
    )
    return task_id


def get_inventory_task(task_id: str, user_id=None) -> Optional[dict]:
    data = cache.get(_inventory_cache_key(task_id))
    if not data:
        return None
    if user_id is not None and data.get('user_id') != user_id:
        return None
    return data


def update_inventory_task(task_id: str, **fields) -> None:
    data = cache.get(_inventory_cache_key(task_id)) or {}
    data.update(fields)
    cache.set(_inventory_cache_key(task_id), data, CACHE_TTL)

def run_inventory_import_in_background(task_id: str, file_bytes: bytes) -> None:
    """后台线程执行库存导入，分批写入并更新进度 + 自动记录库存日志。"""
    import io
    import pandas as pd
    from django.db import close_old_connections
    from django.db import transaction
    from django.contrib.auth.models import User
    from .inventory_excel import COLUMN_MAP, _clean
    from .models import InventoryItem, InventoryLog

    close_old_connections()

    task = get_inventory_task(task_id)
    user_id = task.get('user_id')
    try:
        user = User.objects.get(id=user_id)
        username = user.username
    except Exception:
        username = "系统导入"

    try:
        update_inventory_task(task_id, status='running', percent=5, message='正在读取文件…')

        df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
        total = len(df)
        update_inventory_task(task_id, percent=10, message=f'共 {total} 行，开始导入…', total=total)

        # ── 第一遍：解析所有行，建立序列号→数据的映射 ──
        rows_data = []       # list of (serial_no, kwargs_dict, quantity)
        seen_serials = set()
        skipped = errors = 0
        error_messages = []

        for idx, row in df.iterrows():
            row_num = idx + 2
            try:
                kwargs = {}
                for col, field in COLUMN_MAP.items():
                    if col in df.columns:
                        v = _clean(row.get(col), field)
                        if v is not None:
                            kwargs[field] = v

                serial = kwargs.get('serial_no')
                quantity = kwargs.get('quantity', 0)
                try:
                    quantity = float(quantity)
                except Exception:
                    quantity = 0

                if not serial:
                    skipped += 1
                    continue

                rows_data.append((serial, kwargs, quantity, row_num))
                seen_serials.add(serial)

            except Exception as exc:
                errors += 1
                error_messages.append(f'第 {row_num} 行：{exc}')

        # ── 第二遍：批量查询已存在的库存 ──
        update_inventory_task(task_id, percent=12, message='正在匹配已有库存…')
        existing_map = {
            item.serial_no: item
            for item in InventoryItem.objects.filter(serial_no__in=list(seen_serials))
        }

        # ── 第三遍：分批写入 ──
        DB_BATCH = 200
        to_create = []          # 新增的 InventoryItem
        to_update_items = []    # (obj, old_qty, kwargs) 待更新

        for serial, kwargs, qty, row_num in rows_data:
            existing = existing_map.get(serial)
            if existing:
                old_qty = existing.quantity
                for k, v in kwargs.items():
                    setattr(existing, k, v)
                to_update_items.append((existing, old_qty, qty))
            else:
                item = InventoryItem(**kwargs)
                to_create.append(item)

        total_rows = len(rows_data)
        done = 0

        # ── 分批创建新库存 ──
        imported = 0
        if to_create:
            created_items = InventoryItem.objects.bulk_create(to_create)
            imported = len(created_items)
            done += imported
            # 批量创建新增库存的日志
            qty_map = {kwargs['serial_no']: kwargs.get('quantity', 0) for _, kwargs, qty, _ in rows_data if kwargs.get('serial_no')}
            new_logs = []
            for item in created_items:
                qty = qty_map.get(item.serial_no, 0)
                if qty > 0:
                    new_logs.append(
                        InventoryLog(
                            item=item,
                            log_type='in',
                            quantity=qty,
                            remark="Excel导入新增库存",
                            created_by=username,
                        )
                    )
            if new_logs:
                InventoryLog.objects.bulk_create(new_logs)
            pct = 12 + int(done / total_rows * 60)
            update_inventory_task(task_id, percent=pct, message=f'新增 {imported} 条库存…', current=done, total=total_rows)

        # ── 分批更新已有库存 ──
        update_fields = [f.name for f in InventoryItem._meta.get_fields() if hasattr(f, 'column') and f.name != 'id']
        for i in range(0, len(to_update_items), DB_BATCH):
            batch = to_update_items[i:i + DB_BATCH]
            items = []
            logs_batch = []
            for obj, old_qty, qty in batch:
                items.append(obj)
                diff = qty - old_qty
                if diff != 0:
                    logs_batch.append(
                        InventoryLog(
                            item=obj,
                            log_type='in' if diff > 0 else 'out',
                            quantity=abs(diff),
                            remark=f"Excel导入更新 | 原数量:{old_qty} → 新数量:{qty}",
                            created_by=username,
                        )
                    )
                else:
                    logs_batch.append(
                        InventoryLog(
                            item=obj,
                            log_type='adjust',
                            quantity=0,
                            remark="Excel导入调整资料，数量无变动",
                            created_by=username,
                        )
                    )
            with transaction.atomic():
                InventoryItem.objects.bulk_update(items, update_fields)
                InventoryLog.objects.bulk_create(logs_batch)
            done += len(batch)
            pct = 12 + int(done / total_rows * 60)
            update_inventory_task(task_id, percent=pct, message=f'更新 {done}/{total_rows} 条…', current=done, total=total_rows)

        updated = len(to_update_items)

        update_inventory_task(
            task_id,
            status='done',
            percent=100,
            message='导入完成',
            current=total_rows,
            result={
                'imported': imported,
                'updated': updated,
                'skipped': skipped,
                'errors': errors,
                'error_messages': error_messages[:20],
            },
        )
        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            f"[导入库存] {username} 导入完成：新增{imported}条 更新{updated}条 失败{errors}条 跳过{skipped}条"
        )

    except Exception as exc:
        update_inventory_task(
            task_id,
            status='error',
            percent=100,
            message=f'导入失败：{exc}',
            error=str(exc),
        )
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"[导入库存] {username} 导入失败：{exc}", exc_info=True)

def start_inventory_task(task_id: str, file_bytes: bytes) -> None:
    thread = threading.Thread(
        target=run_inventory_import_in_background,
        args=(task_id, file_bytes),
        daemon=True,
    )
    thread.start()


# ---------------------------------------------------------------------------
# 布种编号导入后台任务
# ---------------------------------------------------------------------------

CATALOG_CACHE_PREFIX = "catalog_import:"

def _catalog_cache_key(task_id: str) -> str:
    return f"{CATALOG_CACHE_PREFIX}{task_id}"

def create_catalog_task(user_id: int) -> str:
    task_id = str(uuid.uuid4())
    cache.set(
        _catalog_cache_key(task_id),
        {
            "status": "pending",
            "percent": 0,
            "message": "任务已创建，等待处理...",
            "current": 0,
            "total": 0,
            "user_id": user_id,
            "result": None,
            "error": None,
        },
        CACHE_TTL,
    )
    return task_id

def get_catalog_task(task_id: str, user_id=None):
    data = cache.get(_catalog_cache_key(task_id))
    if not data:
        return None
    if user_id is not None and data.get("user_id") != user_id:
        return None
    return data

def update_catalog_task(task_id: str, **fields):
    data = cache.get(_catalog_cache_key(task_id)) or {}
    data.update(fields)
    cache.set(_catalog_cache_key(task_id), data, CACHE_TTL)

def run_catalog_import_in_background(task_id: str, file_bytes: bytes) -> None:
    import io
    import pandas as pd
    from django.db import close_old_connections
    from django.contrib.auth.models import User
    from .models import ClothCatalog

    close_old_connections()

    update_catalog_task(task_id, status="running", percent=5, message="正在读取文件...")

    task = get_catalog_task(task_id)
    try:
        user = User.objects.get(id=task.get("user_id"))
        username = user.username
    except Exception:
        username = "系统导入"

    try:
        df = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str)
        total = len(df) - 2  # 去掉两行表头
        update_catalog_task(task_id, percent=10, message=f"共 {total} 行，开始导入...", total=total)

        imported = updated = 0
        for idx in range(2, len(df)):
            row = df.iloc[idx]
            cloth_code = str(row[7]).strip() if pd.notna(row[7]) else ""
            if not cloth_code:
                continue

            cloth_name = str(row[5]).strip() if pd.notna(row[5]) else ""
            obj, created = ClothCatalog.objects.update_or_create(
                cloth_code=cloth_code,
                defaults={
                    "cloth_name": cloth_name,
                    "cloth_type": str(row[6]).strip() if pd.notna(row[6]) else "",
                    "customer": str(row[3]).strip() if pd.notna(row[3]) else "",
                    "composition_cn": str(row[8]).strip() if pd.notna(row[8]) else "",
                    "composition_en": str(row[9]).strip() if pd.notna(row[9]) else "",
                    "width": str(row[10]).strip() if pd.notna(row[10]) else "",
                    "weight": str(row[11]).strip() if pd.notna(row[11]) else "",
                    "specification": str(row[12]).strip() if pd.notna(row[12]) else "",
                    "density": str(row[13]).strip() if pd.notna(row[13]) else "",
                    "process_cn": str(row[14]).strip() if pd.notna(row[14]) else "",
                    "process_en": str(row[15]).strip() if pd.notna(row[15]) else "",
                    "remark": str(row[16]).strip() if pd.notna(row[16]) else "",
                    "supplier1": str(row[22]).strip() if pd.notna(row[22]) else "",
                    "supplier1_code": str(row[23]).strip() if pd.notna(row[23]) else "",
                    "supplier2": str(row[24]).strip() if pd.notna(row[24]) else "",
                },
            )
            if created:
                imported += 1
            else:
                updated += 1

            cur = idx - 1
            if cur % 50 == 0 or cur == total:
                pct = 10 + int(cur / total * 88)
                update_catalog_task(task_id, percent=pct, message=f"已处理 {cur}/{total} 行...", current=cur)

        update_catalog_task(
            task_id,
            status="done",
            percent=100,
            message="导入完成",
            result={"imported": imported, "updated": updated},
        )
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[导入布种] {username} 导入完成：新增{imported}条 更新{updated}条")

    except Exception as exc:
        update_catalog_task(
            task_id,
            status="error",
            percent=100,
            message=f"导入失败: {exc}",
            error=str(exc),
        )
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"[导入布种] {username} 导入失败：{exc}", exc_info=True)

def start_catalog_task(task_id: str, file_bytes: bytes) -> None:
    thread = threading.Thread(
        target=run_catalog_import_in_background,
        args=(task_id, file_bytes),
        daemon=True,
    )
    thread.start()
