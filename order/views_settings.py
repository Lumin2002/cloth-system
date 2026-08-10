"""系统设置页面（仅管理员）：设置总览、Redis、MySQL、Nginx、日志清除。"""
import logging
import re
import shutil
import subprocess
import time
from datetime import datetime
from datetime import timezone as std_timezone
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .db_backup import get_db_info
from .decorators import admin_required
from .i18n import t
from .monitor import get_log_files

logger = logging.getLogger(__name__)
ENV_PATH = Path(settings.BASE_DIR) / ".env"


def update_env_file(updates):
    """更新 .env 中的键值，保留其他行与注释；不存在的键追加到末尾。"""
    lines = (
        ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
        if ENV_PATH.exists()
        else []
    )
    written = set()
    out = []
    for line in lines:
        stripped = line.strip()
        matched = False
        for key, value in updates.items():
            if stripped.startswith(key + "="):
                out.append(f"{key}={value}\n")
                written.add(key)
                matched = True
                break
        if not matched:
            out.append(line)
    for key, value in updates.items():
        if key not in written:
            out.append(f"{key}={value}\n")
    ENV_PATH.write_text("".join(out), encoding="utf-8")


def _mask_redis_url(url):
    """把 Redis 连接串中的密码打码后展示。"""
    if not url:
        return ""
    return re.sub(r"://([^:/@]+):[^@/]+@", r"://\1:******@", url)


def _redis_info():
    url = getattr(settings, "REDIS_URL", "") or ""
    if not url:
        return {"configured": False, "masked_url": "", "host": "-", "port": "-", "db": "-"}
    info = {"configured": True, "masked_url": _mask_redis_url(url), "host": "-", "port": "-", "db": "-"}
    try:
        from urllib.parse import urlparse

        p = urlparse(url)
        info["host"] = p.hostname or "-"
        info["port"] = p.port or "6379"
        info["db"] = (p.path or "/0").lstrip("/") or "0"
    except Exception:
        pass
    return info


def _parse_redis_url(url):
    """解析 redis:// 连接串，返回 host / port / db / password。"""
    info = {"host": "", "port": "6379", "db": "0", "password": ""}
    if not url:
        return info
    try:
        from urllib.parse import unquote, urlparse

        p = urlparse(url)
        info["host"] = p.hostname or ""
        if p.port:
            info["port"] = str(p.port)
        info["db"] = (p.path or "/0").lstrip("/") or "0"
        if p.password:
            info["password"] = unquote(p.password)
    except Exception:
        pass
    return info


def _log_files_with_text():
    files = get_log_files()
    for f in files:
        f["mtime_text"] = timezone.localtime(
            datetime.fromtimestamp(f["mtime"], tz=std_timezone.utc)
        ).strftime("%Y-%m-%d %H:%M:%S")
    return files


@admin_required
@require_GET
def settings_index(request):
    return render(request, "order/settings_accounts.html")


@admin_required
@require_GET
def settings_redis(request):
    return render(request, "order/settings_redis.html", {"redis": _redis_info()})


@admin_required
@require_GET
def settings_mysql(request):
    return render(
        request,
        "order/settings_mysql.html",
        {
            "db_info": get_db_info(),
        },
    )


@admin_required
@require_GET
def settings_nginx(request):
    nginx_path = Path(settings.BASE_DIR) / "nginx.conf"
    content = ""
    try:
        content = nginx_path.read_text(encoding="utf-8")
    except OSError:
        content = ""
    return render(
        request,
        "order/settings_nginx.html",
        {
            "nginx_path": str(nginx_path),
            "nginx_content": content,
            "nginx_missing": not nginx_path.exists(),
            "check_url": getattr(settings, "NGINX_CHECK_URL", "") or "",
        },
    )


def _nginx_validate(path):
    """尝试用 nginx -t 校验配置；本机无 nginx 时返回 (None, 提示)。"""
    nginx_bin = shutil.which("nginx")
    if not nginx_bin:
        return None, "本机未安装 nginx，已跳过语法校验"
    try:
        proc = subprocess.run(
            [nginx_bin, "-t", "-c", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"语法校验无法执行：{exc}"
    output = (proc.stderr or proc.stdout or "").strip()
    return proc.returncode == 0, output


@admin_required
@require_POST
def settings_redis_save(request):
    host = request.POST.get("host", "").strip()
    port = request.POST.get("port", "").strip() or "6379"
    db = request.POST.get("db", "").strip() or "0"
    password = request.POST.get("password", "")
    if not host:
        messages.error(request, t("msg.redis_host_required"))
        return redirect("settings_redis")
    if not port.isdigit() or not db.isdigit():
        messages.error(request, t("msg.redis_invalid"))
        return redirect("settings_redis")

    old = _parse_redis_url(getattr(settings, "REDIS_URL", "") or "")
    if not password:
        password = old["password"]
    password_part = f":{password}@" if password else ""
    url = f"redis://{password_part}{host}:{port}/{db}"
    try:
        update_env_file({"REDIS_URL": url})
    except OSError as exc:
        logger.error(f"[Redis 设置] 写入 .env 失败：{exc}", exc_info=True)
        messages.error(request, t("msg.env_write_failed", error=exc))
        return redirect("settings_redis")
    settings.REDIS_URL = url  # 内存同步，页面立即显示新配置；实际连接需重启
    logger.info(t("log.redis_save", username=request.user.username, host=host, port=port, db=db))
    messages.success(request, t("msg.redis_saved"))
    return redirect("settings_redis")


@admin_required
@require_POST
def settings_redis_test(request):
    """测试 Redis 连接（PING）。"""
    url = getattr(settings, "REDIS_URL", "") or ""
    if not url:
        messages.error(request, t("msg.redis_not_configured"))
        return redirect("settings_redis")
    try:
        try:
            from django_redis import get_redis_connection

            client = get_redis_connection("default")
        except ImportError:
            import redis

            client = redis.from_url(url)
        pool = getattr(client, "connection_pool", None)
        if pool is not None:
            pool.connection_kwargs.update(
                {
                    "socket_connect_timeout": 2,
                    "socket_timeout": 2,
                }
            )
        start = time.perf_counter()
        client.ping()
        latency = (time.perf_counter() - start) * 1000
        logger.info(t("log.redis_test", username=request.user.username, result="成功"))
        messages.success(request, t("msg.redis_test_ok", latency=round(latency, 1)))
    except Exception as exc:
        logger.warning(t("log.redis_test", username=request.user.username, result="失败"))
        messages.error(request, t("msg.redis_test_failed", error=exc))
    return redirect("settings_redis")


@admin_required
@require_POST
def settings_mysql_save(request):
    name = request.POST.get("db_name", "").strip()
    host = request.POST.get("db_host", "").strip()
    port = request.POST.get("db_port", "").strip() or "3306"
    user = request.POST.get("db_user", "").strip()
    password = request.POST.get("db_password", "")
    if port and not port.isdigit():
        messages.error(request, t("msg.mysql_invalid"))
        return redirect("settings_mysql")

    updates = {"DB_NAME": name, "DB_HOST": host, "DB_PORT": port, "DB_USER": user}
    if password:
        updates["DB_PASSWORD"] = password
    try:
        update_env_file(updates)
    except OSError as exc:
        logger.error(f"[MySQL 设置] 写入 .env 失败：{exc}", exc_info=True)
        messages.error(request, t("msg.env_write_failed", error=exc))
        return redirect("settings_mysql")

    db = settings.DATABASES.get("default", {})
    db["NAME"] = name
    db["HOST"] = host
    db["PORT"] = port
    db["USER"] = user
    if password:
        db["PASSWORD"] = password
    logger.info(
        t(
            "log.mysql_save",
            username=request.user.username,
            name=name or "(SQLite)",
            host=host or "-",
        )
    )
    messages.success(request, t("msg.mysql_saved"))
    return redirect("settings_mysql")


@admin_required
@require_POST
def settings_nginx_save(request):
    content = request.POST.get("content", "")
    nginx_path = Path(settings.BASE_DIR) / "nginx.conf"
    old = nginx_path.read_text(encoding="utf-8") if nginx_path.exists() else ""
    nginx_path.write_text(content, encoding="utf-8")

    ok, check_msg = _nginx_validate(nginx_path)
    if ok is False:
        nginx_path.write_text(old, encoding="utf-8")
        logger.warning(f"[Nginx 设置] {request.user.username} 保存的配置未通过校验，已还原")
        messages.error(request, t("msg.nginx_reverted", error=(check_msg or "语法错误")[:300]))
    else:
        logger.info(t("log.nginx_save", username=request.user.username))
        extra = f"；校验结果：{check_msg}" if check_msg else "；请执行 nginx -s reload 生效"
        messages.success(request, t("msg.nginx_saved", extra=extra))
    return redirect("settings_nginx")


@admin_required
@require_GET
def settings_logs(request):
    return render(request, "order/settings_logs.html", {"log_files": _log_files_with_text()})


@admin_required
@require_POST
def settings_logs_clear(request):
    files = get_log_files()
    cleared = 0
    names = []
    for f in files:
        path = Path(settings.LOG_DIR) / f["name"]
        try:
            with open(path, "r+b") as fh:
                fh.seek(0)
                fh.truncate()
            cleared += 1
            names.append(f["name"])
        except OSError as exc:
            logger.warning(f"[清除日志] 无法清空 {f['name']}：{exc}")
    logger.info(
        t(
            "log.logs_clear",
            username=request.user.username,
            names=", ".join(names) or "无",
        )
    )
    messages.success(request, t("msg.logs_cleared", count=cleared))
    return redirect("settings_logs")
