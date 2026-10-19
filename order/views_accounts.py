"""管理员账号管理（仅超级管理员可访问）。

业务侧管理员 = auth.User 且 is_staff=True；超级管理员 = is_superuser=True。
删除管理员时，其创建的订单因 created_by 使用 SET_NULL 会归入「未分配」，
可在订单详情页重新指定创建人。
"""

import logging

from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .decorators import superuser_required
from .forms import AdminUserForm
from .models import ClothOrder

logger = logging.getLogger(__name__)


def _active_superuser_count(exclude_pk=None) -> int:
    qs = User.objects.filter(is_superuser=True, is_active=True)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.count()


@superuser_required
@require_GET
def admin_account_list(request):
    """管理员账号列表（系统设置首页）。"""
    order_counts = dict(
        ClothOrder.objects.filter(created_by__isnull=False)
        .values_list("created_by")
        .annotate(total=Count("id"))
    )
    users = User.objects.filter(is_staff=True).order_by("-is_superuser", "username")
    accounts = [
        {
            "user": user,
            "order_count": order_counts.get(user.pk, 0),
            "is_self": user.pk == request.user.pk,
            "is_last_superuser": user.is_superuser
            and _active_superuser_count(exclude_pk=user.pk) == 0,
        }
        for user in users
    ]
    return render(
        request,
        "order/settings_accounts.html",
        {
            "accounts": accounts,
            "total": len(accounts),
            "superuser_count": sum(1 for a in accounts if a["user"].is_superuser),
            "active_count": sum(1 for a in accounts if a["user"].is_active),
        },
    )


@superuser_required
def admin_account_add(request):
    """新增管理员账号。"""
    form = AdminUserForm(request.POST or None, current_user=request.user)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        logger.info("[管理员管理] %s 新增管理员 %s", request.user.username, user.username)
        messages.success(request, f"管理员 {user.username} 已创建")
        return redirect("system_settings")

    return render(
        request,
        "order/settings_account_form.html",
        {
            "form": form,
            "form_title": "新增管理员",
            "form_hint": "新账号默认为启用状态的管理员，可勾选设为超级管理员。",
            "submit_label": "创建账号",
        },
    )


@superuser_required
def admin_account_edit(request, pk):
    """编辑管理员账号。"""
    account = get_object_or_404(User, pk=pk, is_staff=True)
    form = AdminUserForm(
        request.POST or None, instance=account, current_user=request.user
    )
    if request.method == "POST" and form.is_valid():
        user = form.save()
        logger.info("[管理员管理] %s 修改管理员 %s", request.user.username, user.username)
        messages.success(request, f"管理员 {user.username} 已更新")
        return redirect("system_settings")

    return render(
        request,
        "order/settings_account_form.html",
        {
            "form": form,
            "account": account,
            "form_title": f"编辑管理员：{account.username}",
            "form_hint": "密码留空表示不修改；不能取消自己的超级管理员权限。",
            "submit_label": "保存修改",
        },
    )


@superuser_required
@require_POST
def admin_account_delete(request, pk):
    """删除管理员账号。"""
    account = get_object_or_404(User, pk=pk, is_staff=True)

    if account.pk == request.user.pk:
        messages.error(request, "不能删除当前登录的账号")
        return redirect("system_settings")

    if account.is_superuser and _active_superuser_count(exclude_pk=account.pk) == 0:
        messages.error(request, "不能删除最后一个超级管理员")
        return redirect("system_settings")

    username = account.username
    account.delete()
    logger.info("[管理员管理] %s 删除管理员 %s", request.user.username, username)
    messages.success(request, f"管理员 {username} 已删除，其创建的订单归入「未分配」")
    return redirect("system_settings")


@superuser_required
def admin_account_reset_password(request, pk):
    """重置管理员密码。"""
    account = get_object_or_404(User, pk=pk, is_staff=True)

    if request.method == "POST":
        new_password = (request.POST.get("new_password") or "").strip()
        if len(new_password) < 6:
            messages.error(request, "新密码至少 6 位")
        else:
            account.set_password(new_password)
            account.save(update_fields=["password"])
            logger.info(
                "[管理员管理] %s 重置了管理员 %s 的密码",
                request.user.username,
                account.username,
            )
            messages.success(request, f"管理员 {account.username} 的密码已重置")
            return redirect("system_settings")

    return render(
        request,
        "order/settings_account_reset_password.html",
        {"account": account},
    )
