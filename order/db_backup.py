"""数据库备份：每次生成 SQL、JSON 与全部数据表格，打包为 zip（各数据库通用）。"""
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

BACKUP_DIR = Path(settings.BASE_DIR) / "backups"
BACKUP_CONFIG_PATH = Path(settings.BASE_DIR) / "logs" / "backup_config.json"

DEFAULT_BACKUP_INTERVAL_HOURS = 24
BACKUP_MODES = {
    "off": 0,
    "hourly": 1,
    "hourly6": 6,
    "hourly12": 12,
    "daily": 24,
    "custom": None,
}
BACKUP_MODE_LABELS = {
    "off": "关闭",
    "hourly": "每小时",
    "hourly6": "每 6 小时",
    "hourly12": "每 12 小时",
    "daily": "每天",
    "custom": "自定义",
}


def get_backup_config():
    """读取自动备份配置：返回 {"mode": ..., "interval_hours": ...}。"""
    config = {"mode": "daily", "interval_hours": DEFAULT_BACKUP_INTERVAL_HOURS}
    try:
        data = json.loads(BACKUP_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}

    mode = data.get("mode")
    if mode in BACKUP_MODES:
        config["mode"] = mode
    elif mode is None and data.get("interval_hours"):
        # 兼容旧配置（只有 interval_hours）：视为自定义模式
        config["mode"] = "custom"

    try:
        hours = float(data.get("interval_hours", 0))
        if hours > 0:
            config["interval_hours"] = hours
    except (TypeError, ValueError):
        pass

    # 未配置任何文件时，用环境变量 DB_BACKUP_INTERVAL_HOURS 兜底
    if not data:
        try:
            hours = float(os.environ.get("DB_BACKUP_INTERVAL_HOURS", ""))
            if hours > 0:
                config["mode"] = "custom"
                config["interval_hours"] = hours
        except ValueError:
            pass
    return config


def set_backup_config(mode, interval_hours=None):
    """保存自动备份模式与间隔到配置文件。"""
    if mode not in BACKUP_MODES:
        raise ValueError("无效的备份模式")
    if mode == "off":
        hours = 0
    elif mode == "custom":
        try:
            hours = float(interval_hours or 0)
        except (TypeError, ValueError):
            raise ValueError("自定义间隔必须是数字（小时）")
        if hours <= 0:
            raise ValueError("自定义间隔必须大于 0")
    else:
        hours = BACKUP_MODES[mode]
    data = {"mode": mode, "interval_hours": hours}
    BACKUP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    BACKUP_CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data


def get_backup_interval_hours():
    """兼容接口：返回当前生效的间隔小时数。"""
    config = get_backup_config()
    return config["interval_hours"]


def get_backup_mode():
    """兼容接口：返回当前生效的备份模式。"""
    return get_backup_config()["mode"]


def _ensure_dir():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def get_db_info():
    """返回当前默认数据库的连接信息（用于页面展示与备份命令）。"""
    db = settings.DATABASES.get("default", {})
    engine = db.get("ENGINE", "")
    if "sqlite" in engine:
        return {
            "engine": "sqlite",
            "label": "SQLite",
            "name": str(db.get("NAME", "")),
        }
    if "mysql" in engine:
        return {
            "engine": "mysql",
            "label": "MySQL",
            "name": db.get("NAME", ""),
            "host": db.get("HOST", "127.0.0.1"),
            "port": db.get("PORT", "3306"),
            "user": db.get("USER", ""),
            "password": db.get("PASSWORD", ""),
        }
    return {"engine": "other", "label": engine.split(".")[-1] or engine, "name": ""}


def _timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_sheet_name(name, used: set[str]) -> str:
    """Excel Sheet 名：去非法字符、限 31 字符、重名加序号。"""
    import re

    cleaned = re.sub(r"[\\/*?:\[\]]", "_", str(name)).strip() or "Sheet"
    cleaned = cleaned[:31]
    base = cleaned
    index = 2
    while cleaned.lower() in used:
        suffix = f"_{index}"
        cleaned = f"{base[:31 - len(suffix)]}{suffix}"
        index += 1
    used.add(cleaned.lower())
    return cleaned


def _excel_safe(value):
    """把带时区的 datetime 转为本地时区 naive 值（Excel 不支持时区）。"""
    from datetime import datetime as _datetime

    if isinstance(value, _datetime):
        from django.utils.timezone import is_aware, localtime

        if is_aware(value):
            return localtime(value).replace(tzinfo=None)
    return value


def _model_dataframe(model):
    """把一个模型导出为 DataFrame，列名用字段的中文名（重名加序号）。"""
    from collections import Counter

    import pandas as pd

    fields = list(model._meta.concrete_fields)
    labels = [str(field.verbose_name) for field in fields]
    counts = Counter(labels)
    seen: Counter[str] = Counter()
    columns = []
    for label in labels:
        if counts[label] > 1:
            seen[label] += 1
            columns.append(f"{label}{seen[label]}")
        else:
            columns.append(label)
    rows = [
        tuple(_excel_safe(value) for value in row)
        for row in model.objects.all().values_list(*[field.attname for field in fields])
    ]
    return pd.DataFrame(rows, columns=columns)


def _build_all_data_excel(timestamp):
    """生成「全部备份数据表格」：一个多 Sheet 的 xlsx 字节。"""
    import pandas as pd
    from django.contrib.auth import get_user_model

    from .models import (
        ClothCatalog,
        CostPriceHistory,
        Customer,
        ExhibitionContact,
        FabricQuotation,
        InventoryItem,
        InventoryLog,
        Notification,
        Shipment,
        Supplier,
    )
    from .order_excel import (
        export_full_backup_dataframe,
        export_supplier_orders_dataframe,
        write_dataframe_to_sheet,
    )

    sheets = [
        ("订单数据", export_full_backup_dataframe()),
        ("供应商订单", export_supplier_orders_dataframe()),
        ("出货记录", _model_dataframe(Shipment)),
        ("客户", _model_dataframe(Customer)),
        ("供应商账号", _model_dataframe(Supplier)),
        ("库存", _model_dataframe(InventoryItem)),
        ("库存变动", _model_dataframe(InventoryLog)),
        ("布种编号", _model_dataframe(ClothCatalog)),
        ("成本价变更", _model_dataframe(CostPriceHistory)),
        ("面料报价", _model_dataframe(FabricQuotation)),
        ("展会联系", _model_dataframe(ExhibitionContact)),
        ("通知", _model_dataframe(Notification)),
        ("用户", _model_dataframe(get_user_model())),
    ]
    output = BytesIO()
    used: set[str] = set()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        for sheet_name, dataframe in sheets:
            write_dataframe_to_sheet(writer, dataframe, _safe_sheet_name(sheet_name, used))
    return output.getvalue()


def _sql_literal(value):
    """把 Python 值转成 SQL 字面量。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return "X'" + bytes(value).hex() + "'"
    text = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{text}'"


def _generic_sql_dump():
    """通用 SQL 备份：遍历所有表生成 INSERT 语句（不依赖外部命令）。"""
    from django.db import connection

    lines = [
        "-- 数据库全量数据备份",
        f"-- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"-- 数据库引擎：{connection.vendor}",
        "",
    ]
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        tables = connection.introspection.table_names(cursor)
    for table in tables:
        try:
            with connection.cursor() as table_cursor:
                table_cursor.execute(f"SELECT * FROM {quote(table)}")
                column_names = [column[0] for column in table_cursor.description]
                rows = table_cursor.fetchall()
        except Exception as exc:
            lines.append(f"-- 跳过表 {table}：{exc}")
            continue
        if not rows:
            continue
        column_sql = ", ".join(quote(column) for column in column_names)
        lines.append(f"-- {table}：{len(rows)} 行")
        for row in rows:
            values = ", ".join(_sql_literal(value) for value in row)
            lines.append(f"INSERT INTO {quote(table)} ({column_sql}) VALUES ({values});")
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def _sqlite_sql_dump(db_path):
    """SQLite 用自带 iterdump()：含建表语句与数据，不依赖外部命令。"""
    import sqlite3

    path = str(db_path or "")
    if not path or path.startswith("file:") or path == ":memory:":
        return "-- 当前为内存/测试数据库，跳过 SQL 备份\n".encode("utf-8")
    sqlite_connection = sqlite3.connect(path, timeout=30)
    try:
        dump = "\n".join(sqlite_connection.iterdump())
    finally:
        sqlite_connection.close()
    header = (
        "-- SQLite 全量备份\n"
        f"-- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return (header + dump + "\n").encode("utf-8")


def _mysql_sql_dump(info):
    """MySQL 优先用 mysqldump，未安装时退回通用 INSERT 导出。"""
    mysqldump = shutil.which("mysqldump")
    if not mysqldump:
        return _generic_sql_dump()
    cmd = [
        mysqldump,
        f"--host={info.get('host') or '127.0.0.1'}",
        f"--port={info.get('port') or '3306'}",
        f"--user={info.get('user') or 'root'}",
        "--single-transaction",
        "--skip-lock-tables",
        "--no-tablespaces",
        "--default-character-set=utf8mb4",
        str(info.get("name") or ""),
    ]
    env = os.environ.copy()
    if info.get("password"):
        # 用环境变量传密码，避免出现在命令行进程列表里
        env["MYSQL_PWD"] = str(info["password"])
    result = subprocess.run(cmd, capture_output=True, env=env, check=True)
    return result.stdout


def _dump_sql_bytes():
    """生成 SQL 备份内容；失败时返回说明注释，不中断整份备份。"""
    info = get_db_info()
    try:
        if info["engine"] == "sqlite":
            return _sqlite_sql_dump(info.get("name"))
        if info["engine"] == "mysql":
            return _mysql_sql_dump(info)
        return _generic_sql_dump()
    except Exception as exc:
        logger.warning(f"[备份] 生成 SQL 备份失败：{exc}", exc_info=True)
        return f"-- SQL 备份生成失败：{exc}\n".encode("utf-8")


def _backup_dumpdata():
    """将 SQL、JSON 与全部数据表格一同压缩为备份包。"""
    timestamp = _timestamp()
    base_name = f"db_backup_{timestamp}"
    sql_name = f"{base_name}.sql"
    json_name = f"{base_name}.json"
    excel_name = f"all_data_backup_{timestamp}.xlsx"
    dest = _ensure_dir() / f"{base_name}.zip"
    cmd = [
        sys.executable,
        "manage.py",
        "dumpdata",
        "--indent",
        "2",
        "--exclude",
        "contenttypes",
        "--exclude",
        "auth.Permission",
        "--exclude",
        "sessions",
    ]
    env = os.environ.copy()
    # 强制子进程以 UTF-8 输出，避免 Windows GBK 编码报错
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory() as tmp:
        json_path = Path(tmp) / json_name
        with open(json_path, "w", encoding="utf-8") as f:
            subprocess.run(cmd, cwd=str(settings.BASE_DIR), stdout=f, env=env, check=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(json_path, arcname=json_name)
            zf.writestr(sql_name, _dump_sql_bytes())
            zf.writestr(excel_name, _build_all_data_excel(timestamp))
    return dest


def create_backup():
    """创建一次备份（SQL + JSON + 全部数据表格），返回 (文件路径, 备份方式说明)。"""
    return _backup_dumpdata(), "SQL + JSON + 全部数据 Excel 压缩包"


def prune_backups(keep=None):
    """自动清理旧备份，仅保留最近 keep 份（默认取环境变量 DB_BACKUP_KEEP，默认 30）。"""
    if keep is None:
        try:
            keep = int(os.environ.get("DB_BACKUP_KEEP", "30"))
        except ValueError:
            keep = 30
    backups = list_backups()
    removed = 0
    for b in backups[keep:]:
        if delete_backup(b["name"]):
            removed += 1
    return removed


def list_backups():
    """列出备份目录中的文件，按修改时间倒序。"""
    _ensure_dir()
    entries = []
    try:
        files = sorted(BACKUP_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return entries
    for p in files:
        try:
            if not p.is_file():
                continue
            stat = p.stat()
            entries.append({
                "name": p.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
        except OSError:
            continue
    return entries


def resolve_backup(name):
    """把备份文件名解析为备份目录内的绝对路径，防止路径穿越。"""
    if not name:
        return None
    name = Path(name).name
    root = BACKUP_DIR.resolve()
    target = (root / name).resolve()
    if root not in target.parents or not target.is_file():
        return None
    return target


def delete_backup(name):
    """删除指定备份文件，成功返回 True。"""
    path = resolve_backup(name)
    if path is None:
        return False
    path.unlink()
    return True
