"""系统监控数据采集工具。"""
import os
import platform
import re
import time
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover - psutil 在 requirements 中
    psutil = None
    PSUTIL_AVAILABLE = False

PROJECT_SIZE_CACHE_KEY = "monitor:project_size:v1"
PROJECT_SIZE_CACHE_TTL = 60

# 系统监控只展示应用日志（django-info.log / django-error.log 及其轮转备份）
APP_LOG_RE = re.compile(r"^django-.*\.log(\.\d+)?$")

# 统计项目磁盘占用时跳过的环境/版本库目录
SKIP_DIRS = {".venv", ".git", "node_modules", "__pycache__", ".idea", ".vscode"}


def human_size(num_bytes):
    """把字节数格式化为可读文本。"""
    if num_bytes is None:
        return "-"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_duration(seconds):
    """把秒数格式化为 天/小时/分钟。"""
    if seconds is None:
        return "-"
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes or not parts:
        parts.append(f"{minutes}分钟")
    return " ".join(parts)


def _dir_size(path, skip_dirs):
    """递归统计目录大小，不跟随符号链接。"""
    total = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name in skip_dirs:
                            continue
                        total += _dir_size(entry.path, skip_dirs)
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


def get_project_size_info():
    """返回项目目录总占用和一级目录/文件占用明细（带缓存）。"""
    cached = cache.get(PROJECT_SIZE_CACHE_KEY)
    if cached is not None:
        return cached

    base = Path(settings.BASE_DIR)
    items = []
    total = 0
    try:
        with os.scandir(base) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name in SKIP_DIRS:
                            continue
                        size = _dir_size(entry.path, SKIP_DIRS)
                        items.append({"name": entry.name, "size": size, "is_dir": True})
                    else:
                        size = entry.stat(follow_symlinks=False).st_size
                        items.append({"name": entry.name, "size": size, "is_dir": False})
                    total += size
                except OSError:
                    continue
    except OSError:
        pass

    items.sort(key=lambda item: item["size"], reverse=True)
    data = {
        "total": total,
        "path": str(base),
        "items": items,
    }
    cache.set(PROJECT_SIZE_CACHE_KEY, data, PROJECT_SIZE_CACHE_TTL)
    return data


def get_metrics(blocking=False):
    """采集一次系统指标。

    blocking=True 时做一次短采样，用于页面首次加载拿到真实 CPU 值；
    接口轮询使用 blocking=False（非阻塞增量采样）。
    """
    cpu_interval = 0.35 if blocking else None
    process_interval = 0.2 if blocking else None
    cpu_percent = 0.0
    cpu_per_core = []
    memory = None
    disk = None
    process = None
    boot_time = None

    if PSUTIL_AVAILABLE:
        try:
            cpu_per_core = [round(p, 1) for p in psutil.cpu_percent(interval=cpu_interval, percpu=True)]
            cpu_percent = round(sum(cpu_per_core) / len(cpu_per_core), 1) if cpu_per_core else 0.0

            vm = psutil.virtual_memory()
            memory = {
                "total": vm.total,
                "available": vm.available,
                "used": vm.used,
                "percent": round(vm.percent, 1),
            }

            du = psutil.disk_usage(str(settings.BASE_DIR))
            disk = {
                "total": du.total,
                "used": du.used,
                "free": du.free,
                "percent": round(du.percent, 1),
            }

            proc = psutil.Process()
            process = {
                "pid": proc.pid,
                "cpu_percent": round(proc.cpu_percent(interval=process_interval), 1),
                "memory": proc.memory_info().rss,
                "threads": proc.num_threads(),
                "uptime_seconds": int(time.time() - proc.create_time()),
                "cmdline": " ".join(proc.cmdline())[:240],
            }
            boot_time = int(psutil.boot_time())
        except Exception:
            pass

    return {
        "psutil_available": PSUTIL_AVAILABLE,
        "cpu_percent": cpu_percent,
        "cpu_per_core": cpu_per_core,
        "cpu_count": os.cpu_count() or 0,
        "memory": memory,
        "disk": disk,
        "process": process,
        "project": get_project_size_info(),
        "system": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "boot_time": boot_time,
        },
    }


def get_log_files():
    """列出日志目录中的文件，按修改时间倒序。"""
    log_dir = Path(settings.LOG_DIR)
    files = []
    try:
        entries = sorted(log_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return files
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            if not APP_LOG_RE.match(entry.name):
                continue
            stat = entry.stat()
            files.append({
                "name": entry.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
        except OSError:
            continue
    return files


def resolve_log_file(name):
    """把日志文件名解析为日志目录内的绝对路径，防止路径穿越。"""
    if not name:
        return None
    name = Path(name).name
    log_root = Path(settings.LOG_DIR).resolve()
    target = (log_root / name).resolve()
    if log_root not in target.parents or not target.is_file():
        return None
    return target


def read_log_tail(path, limit=200):
    """读取日志文件末尾指定行数（最多读取文件末尾 256KB）。"""
    if path is None:
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    read_size = min(size, 256 * 1024)
    try:
        with open(path, "rb") as f:
            f.seek(max(0, size - read_size))
            data = f.read()
    except OSError:
        return []
    text = _decode_log_bytes(data)
    return text.splitlines()[-limit:]


def _decode_log_bytes(data):
    """按内容自适应解码日志字节：优先 UTF-8，兼容 Windows 常见 GBK/GB18030。"""
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace")
