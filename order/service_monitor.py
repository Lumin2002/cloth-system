"""依赖服务健康状态探测（MySQL/Redis/Nginx）。"""
import ssl
import time
import urllib.error
import urllib.request

from django.conf import settings
from django.db import connection
from django.utils import timezone

try:
    from django_redis import get_redis_connection

    DJANGO_REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover - django-redis 在 requirements 中
    get_redis_connection = None
    DJANGO_REDIS_AVAILABLE = False

PROBE_TIMEOUT = 2.0
SERVICE_CACHE_TTL = 15

_STATUS_LABELS = {
    "ok": "正常",
    "error": "异常",
    "not_configured": "未配置",
}

_service_cache = {"ts": 0.0, "data": None}


def _status(name, status, detail, latency_ms=None, **extra):
    data = {
        "name": name,
        "status": status,
        "label": _STATUS_LABELS.get(status, status),
        "detail": detail,
        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        "checked_at": timezone.localtime().isoformat(timespec="seconds"),
    }
    data.update(extra)
    return data


def _database_status():
    vendor = connection.vendor
    start = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        return _status("数据库", "error", f"连接失败：{exc}")

    latency = (time.perf_counter() - start) * 1000
    label = "MySQL" if vendor == "mysql" else ("SQLite" if vendor == "sqlite" else vendor)
    return _status("数据库", "ok", f"{label} 连接正常", latency, engine=vendor)


def _redis_status():
    redis_url = getattr(settings, "REDIS_URL", "") or ""
    if not redis_url:
        return _status("Redis", "not_configured", "未配置 REDIS_URL")
    if not DJANGO_REDIS_AVAILABLE:
        return _status("Redis", "error", "django-redis 未安装")

    try:
        client = get_redis_connection("default")
        pool = getattr(client, "connection_pool", None)
        if pool is not None:
            pool.connection_kwargs.update(
                {
                    "socket_connect_timeout": PROBE_TIMEOUT,
                    "socket_timeout": PROBE_TIMEOUT,
                }
            )
        start = time.perf_counter()
        client.ping()
        latency = (time.perf_counter() - start) * 1000
        return _status("Redis", "ok", "PING 正常", latency)
    except Exception as exc:
        return _status("Redis", "error", f"连接失败：{exc}")


def _nginx_status():
    check_url = getattr(settings, "NGINX_CHECK_URL", "") or ""
    if not check_url:
        return _status("Nginx", "not_configured", "未配置 NGINX_CHECK_URL")

    request = urllib.request.Request(check_url, method="GET")
    start = time.perf_counter()
    open_kwargs = {"timeout": PROBE_TIMEOUT}
    if check_url.startswith("https://"):
        # 公网 FRP 使用自签名证书，健康探测不校验证书
        open_kwargs["context"] = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, **open_kwargs) as resp:
            resp.read(1024)
            latency = (time.perf_counter() - start) * 1000
            return _status("Nginx", "ok", f"HTTP {resp.status} (GET)", latency, url=check_url)
    except urllib.error.HTTPError as exc:
        latency = (time.perf_counter() - start) * 1000
        return _status("Nginx", "ok", f"HTTP {exc.code} (GET)", latency, url=check_url)
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return _status("Nginx", "error", f"连接失败：{exc}", latency, url=check_url)


def get_service_status():
    """返回依赖服务状态；15 秒内复用上次探测结果，避免监控轮询频繁探测。"""
    now = time.monotonic()
    cached = _service_cache["data"]
    if cached is not None and now - _service_cache["ts"] < SERVICE_CACHE_TTL:
        return cached

    data = {
        "database": _database_status(),
        "redis": _redis_status(),
        "nginx": _nginx_status(),
    }
    _service_cache["ts"] = now
    _service_cache["data"] = data
    return data
