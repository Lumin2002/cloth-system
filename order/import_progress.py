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

        imported = updated = skipped = errors = 0
        error_messages = []
        BATCH = 200

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

                obj = InventoryItem.objects.filter(serial_no=serial).first()
                if obj:
                    old_qty = obj.quantity
                    for k, v in kwargs.items():
                        setattr(obj, k, v)
                    obj.save()

                    diff = quantity - old_qty
                    if diff != 0:
                        log_type = 'in' if diff > 0 else 'out'
                        InventoryLog.objects.create(
                            item=obj,
                            log_type=log_type,
                            quantity=abs(diff),
                            remark=f"Excel导入更新 | 原数量:{old_qty} → 新数量:{quantity}",
                            created_by=username
                        )
                    else:
                        InventoryLog.objects.create(
                            item=obj,
                            log_type='adjust',
                            quantity=0,
                            remark="Excel导入调整资料，数量无变动",
                            created_by=username
                        )
                    updated += 1

                else:
                    new_item = InventoryItem.objects.create(**kwargs)
                    if new_item.quantity > 0:
                        InventoryLog.objects.create(
                            item=new_item,
                            log_type='in',
                            quantity=new_item.quantity,
                            remark="Excel导入新增库存",
                            created_by=username
                        )
                    imported += 1

            except Exception as exc:
                errors += 1
                error_messages.append(f'第 {row_num} 行：{exc}')

            current = idx + 1
            if current % BATCH == 0 or current == total:
                pct = 10 + int(current / total * 88)
                update_inventory_task(
                    task_id,
                    percent=pct,
                    message=f'已处理 {current} / {total} 行…',
                    current=current,
                    total=total,
                )

        update_inventory_task(
            task_id,
            status='done',
            percent=100,
            message='导入完成',
            current=total,
            result={
                'imported': imported,
                'updated': updated,
                'skipped': skipped,
                'errors': errors,
                'error_messages': error_messages[:20],
            },
        )

    except Exception as exc:
        update_inventory_task(
            task_id,
            status='error',
            percent=100,
            message=f'导入失败：{exc}',
            error=str(exc),
        )

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
    from .models import ClothCatalog

    close_old_connections()

    update_catalog_task(task_id, status="running", percent=5, message="正在读取文件...")

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

    except Exception as exc:
        update_catalog_task(
            task_id,
            status="error",
            percent=100,
            message=f"导入失败: {exc}",
            error=str(exc),
        )

def start_catalog_task(task_id: str, file_bytes: bytes) -> None:
    thread = threading.Thread(
        target=run_catalog_import_in_background,
        args=(task_id, file_bytes),
        daemon=True,
    )
    thread.start()
