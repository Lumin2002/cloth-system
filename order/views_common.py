"""视图公共模块：日志与权限混入类（由 views.py 拆分而来）。"""
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect

from .i18n import t

logger = logging.getLogger(__name__)


class AdminRequiredMixin:
    """管理员权限 Mixin：供应商用户会被重定向到供应商仪表盘。"""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and hasattr(request.user, "supplier_profile") and not request.user.is_staff:
            messages.warning(request, t("auth.no_access"))
            return redirect("supplier_dashboard")
        return super().dispatch(request, *args, **kwargs)


class SupplierRequiredMixin(LoginRequiredMixin):
    """供应商权限 Mixin：只有供应商用户能访问。"""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not hasattr(request.user, "supplier_profile"):
            messages.error(request, t("auth.supplier_no_access"))
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)
