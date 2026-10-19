"""订单 → 本地 Excel 单向同步。

设计约定：
1. Django 数据库是唯一数据源；本地 Excel 只是只读输出的副本，系统不会回读。
2. 订单 / 出货记录变化后，防抖地在后台线程重建整个 Excel 文件。
3. 写入使用临时文件 + 原子替换，避免读到写了一半的文件。
4. 若文件被 Excel / WPS 占用，记录告警，下次订单变化时自动重试。
5. 如需手动立即生成，执行 `python manage.py sync_orders`。
"""

import logging
import os
import threading
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.db import close_old_connections

from .order_excel import export_orders_dataframe, write_dataframe_to_sheet

logger = logging.getLogger(__name__)

SHEET_NAME = "大货"
# 合并短时间内的多次保存，避免批量导入时反复全量写文件
DEBOUNCE_SECONDS = 2.0
# 文件被 Excel / WPS 占用时的自动重试间隔与次数
RETRY_SECONDS = 20.0
MAX_RETRIES = 6

_lock = threading.Lock()
_timer: threading.Timer | None = None


def get_sync_enabled() -> bool:
    return bool(getattr(settings, "ORDER_EXCEL_SYNC_ENABLED", True))


def get_sync_file_path() -> Path:
    path = getattr(settings, "ORDER_EXCEL_SYNC_FILE", None)
    if not path:
        path = Path(settings.BASE_DIR) / "xlsx" / "SYL - 订单汇总表.xlsx"
    return Path(path)


def export_sync_file() -> Path:
    """全量重建本地 Excel（同步执行）。返回写入的文件路径。"""
    path = get_sync_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    dataframe = export_orders_dataframe()
    tmp = path.with_name(f".{path.stem}.tmp.xlsx")
    try:
        with pd.ExcelWriter(tmp, engine="xlsxwriter") as writer:
            write_dataframe_to_sheet(writer, dataframe, SHEET_NAME)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    logger.info("[Excel同步] 已写入 %s 条订单到 %s", len(dataframe), path)
    return path


def _run_export(attempt: int = 1) -> None:
    # 后台线程需要独立处理数据库连接
    close_old_connections()
    try:
        if not get_sync_enabled():
            return
        export_sync_file()
    except PermissionError as exc:
        # 常见于 Excel / WPS 正打开该文件：延时自动重试
        if attempt < MAX_RETRIES:
            logger.warning(
                "[Excel同步] 文件被占用（可能正被 Excel/WPS 打开），%s 秒后重试（第 %s 次）：%s",
                RETRY_SECONDS,
                attempt,
                exc,
            )
            retry = threading.Timer(
                RETRY_SECONDS, _run_export, kwargs={"attempt": attempt + 1}
            )
            retry.daemon = True
            retry.start()
        else:
            logger.error("[Excel同步] 多次重试仍被占用，放弃本次同步：%s", exc)
    except Exception:
        logger.exception("[Excel同步] 写入失败")
    finally:
        close_old_connections()


def _trigger_export() -> None:
    global _timer
    if not get_sync_enabled():
        return
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(DEBOUNCE_SECONDS, _run_export)
        _timer.daemon = True
        _timer.start()


def on_order_changed(sender=None, instance=None, **kwargs) -> None:
    """订单 / 出货记录保存或删除后，安排一次异步同步。"""
    _trigger_export()
