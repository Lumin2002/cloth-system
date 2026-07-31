"""系统监控页面与数据接口。"""
import json
import os
import subprocess
from datetime import datetime
from datetime import timezone as std_timezone

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .decorators import admin_required
from .monitor import (
    format_duration,
    get_log_files,
    get_metrics,
    read_log_tail,
    resolve_log_file,
)
from .service_monitor import get_service_status
from .terminal import COMMAND_TIMEOUT, execute_terminal_command, validate_command


def _log_payload(log_name="", limit=200, include_content=True):
    files = get_log_files()
    for f in files:
        f["mtime_text"] = timezone.localtime(
            datetime.fromtimestamp(f["mtime"], tz=std_timezone.utc)
        ).strftime("%Y-%m-%d %H:%M:%S")
    valid_names = {f["name"] for f in files}
    current = log_name if log_name in valid_names else (files[0]["name"] if files else "")
    path = resolve_log_file(current)
    return {
        "files": files,
        "current": current,
        "lines": read_log_tail(path, limit) if include_content else [],
        "limit": limit,
    }


@admin_required
def monitor_view(request):
    metrics = get_metrics(blocking=True)
    if metrics.get("process"):
        metrics["process"]["uptime_text"] = format_duration(metrics["process"]["uptime_seconds"])
    logs = _log_payload(request.GET.get("log", ""), 300, include_content=True)
    context = {
        "metrics": metrics,
        "log_files": logs["files"],
        "current_log": logs["current"],
        "log_lines": logs["lines"],
        "log_limit": logs["limit"],
        "generated_at": timezone.localtime(),
        "services": get_service_status(),
    }
    return render(request, "order/monitor.html", context)


@admin_required
@require_GET
def monitor_api(request):
    try:
        limit = min(int(request.GET.get("lines", 200)), 2000)
    except (TypeError, ValueError):
        limit = 200
    include_logs = request.GET.get("include_logs") == "1"
    logs = _log_payload(request.GET.get("log", ""), limit, include_content=include_logs)
    return JsonResponse({
        "ok": True,
        "generated_at": timezone.localtime().isoformat(),
        "metrics": get_metrics(blocking=False),
        "logs": logs,
        "services": get_service_status(),
    })


@admin_required
@require_POST
def terminal_run(request):
    """管理员 Web 终端：执行受限命令并返回输出。"""
    if not request.user.is_staff:
        return JsonResponse({"ok": False, "error": "需要管理员权限"}, status=403)

    try:
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "请求格式错误"}, status=400)

    command = str(payload.get("command") or "").strip()
    allowed, reason = validate_command(command)
    if not allowed:
        return JsonResponse({"ok": False, "error": reason}, status=400)

    cwd = request.session.get("terminal_cwd") or str(settings.BASE_DIR)
    if not os.path.isdir(cwd):
        cwd = str(settings.BASE_DIR)

    try:
        result = execute_terminal_command(command, cwd)
    except subprocess.TimeoutExpired:
        return JsonResponse(
            {"ok": False, "error": f"命令执行超时（{COMMAND_TIMEOUT} 秒）"},
            status=408,
        )
    except OSError as exc:
        return JsonResponse({"ok": False, "error": f"命令执行失败：{exc}"}, status=500)

    request.session["terminal_cwd"] = result["cwd"]
    return JsonResponse({
        "ok": True,
        "output": result.get("output", ""),
        "exit_code": result.get("exit_code", 0),
        "cwd": result["cwd"],
        "clear": bool(result.get("clear")),
    })
