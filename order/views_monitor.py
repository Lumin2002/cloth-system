"""系统监控页面与数据接口。"""
from datetime import datetime
from datetime import timezone as std_timezone

from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .decorators import admin_required
from .monitor import (
    format_duration,
    get_log_files,
    get_metrics,
    read_log_tail,
    resolve_log_file,
)


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
    })
