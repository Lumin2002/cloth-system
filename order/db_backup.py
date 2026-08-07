"""数据库备份：SQLite 一致性快照 / MySQL mysqldump / dumpdata 兜底。"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from django.conf import settings

BACKUP_DIR = Path(settings.BASE_DIR) / "backups"


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


def _backup_sqlite():
    """SQLite：使用 sqlite3 备份 API 生成一致性快照（应用运行中也安全）。"""
    import sqlite3

    src = get_db_info()["name"]
    dest = _ensure_dir() / f"db_backup_{_timestamp()}.sqlite3"
    src_conn = sqlite3.connect(src)
    try:
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    return dest


def _backup_mysqldump():
    """MySQL：调用 mysqldump 导出 SQL（密码通过环境变量传递，避免命令行暴露）。"""
    info = get_db_info()
    dest = _ensure_dir() / f"db_backup_{_timestamp()}.sql"
    env = os.environ.copy()
    if info.get("password"):
        env["MYSQL_PWD"] = info["password"]
    cmd = ["mysqldump", "--single-transaction", "--default-character-set=utf8mb4"]
    if info.get("host"):
        cmd += ["-h", info["host"]]
    if info.get("port"):
        cmd += ["-P", str(info["port"])]
    cmd += ["-u", info.get("user") or "root", info["name"]]
    with open(dest, "wb") as f:
        subprocess.run(cmd, stdout=f, env=env, check=True)
    return dest


def _backup_dumpdata():
    """兜底方案：通过 manage.py dumpdata 导出 JSON（各数据库通用）。"""
    dest = _ensure_dir() / f"db_backup_{_timestamp()}.json"
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
    with open(dest, "w", encoding="utf-8") as f:
        subprocess.run(cmd, cwd=str(settings.BASE_DIR), stdout=f, check=True)
    return dest


def create_backup():
    """创建一次备份，返回 (文件路径, 备份方式说明)。"""
    info = get_db_info()
    if info["engine"] == "sqlite":
        return _backup_sqlite(), "SQLite 一致性快照"
    if info["engine"] == "mysql":
        try:
            return _backup_mysqldump(), "mysqldump"
        except Exception:
            return _backup_dumpdata(), "dumpdata JSON（mysqldump 不可用，已自动回退）"
    return _backup_dumpdata(), "dumpdata JSON"


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
