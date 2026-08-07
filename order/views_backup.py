"""数据库备份页面与操作（仅管理员）。"""
import logging
from datetime import datetime
from datetime import timezone as std_timezone
from pathlib import Path

from django.contrib import messages
from django.http import FileResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .db_backup import create_backup, delete_backup, get_db_info, list_backups, resolve_backup
from .decorators import admin_required
from .i18n import t

logger = logging.getLogger(__name__)


@admin_required
@require_GET
def backup_list(request):
    backups = list_backups()
    for b in backups:
        b["mtime_text"] = timezone.localtime(
            datetime.fromtimestamp(b["mtime"], tz=std_timezone.utc)
        ).strftime("%Y-%m-%d %H:%M:%S")
    return render(
        request,
        "order/settings_backup.html",
        {
            "backups": backups,
            "db_info": get_db_info(),
        },
    )


@admin_required
@require_POST
def backup_create(request):
    try:
        path, method = create_backup()
        logger.info(
            t(
                "log.backup_create",
                username=request.user.username,
                filename=path.name,
                method=method,
            )
        )
        messages.success(request, t("msg.backup_created", filename=path.name, method=method))
    except Exception as exc:
        logger.error(f"[数据库备份] 备份失败：{exc}", exc_info=True)
        messages.error(request, t("msg.backup_failed", error=exc))
    return redirect("backup_list")


@admin_required
@require_GET
def backup_download(request, name):
    path = resolve_backup(name)
    if path is None:
        messages.error(request, t("msg.backup_not_found"))
        return redirect("backup_list")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)


@admin_required
@require_POST
def backup_delete(request, name):
    filename = Path(name).name
    if delete_backup(name):
        logger.info(
            t(
                "log.backup_delete",
                username=request.user.username,
                filename=filename,
            )
        )
        messages.success(request, t("msg.backup_deleted", filename=filename))
    else:
        messages.error(request, t("msg.backup_not_found"))
    return redirect("backup_list")
