"""数据库备份：dumpdata JSON 压缩为 zip 压缩包（各数据库通用）。"""
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from django.conf import settings

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


def _backup_dumpdata():
    """通过 manage.py dumpdata 导出 JSON 并压缩为 zip 压缩包。"""
    json_name = f"db_backup_{_timestamp()}.json"
    dest = _ensure_dir() / f"{json_name}.zip"
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
    return dest


def create_backup():
    """创建一次备份，返回 (文件路径, 备份方式说明)。"""
    return _backup_dumpdata(), "dumpdata JSON 压缩包"


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
