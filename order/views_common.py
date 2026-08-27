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


def _error_response(request, status, code, title, message):
    """统一错误/异常提示页：AJAX 请求返回 JSON，其余按角色渲染统一页面"""
    from django.http import JsonResponse
    from django.shortcuts import render

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"code": code, "error": message}, status=status)

    try:
        is_supplier = hasattr(request.user, "supplier_profile") and not request.user.is_staff
    except Exception:
        is_supplier = False

    context = {
        "error_code": code,
        "error_title": title,
        "error_message": message,
        "is_supplier": is_supplier,
        "extends_template": "order/supplier_base.html" if is_supplier else "order/base.html",
        "supplier": request.user.supplier_profile if is_supplier else None,
    }
    return render(request, "order/error_page.html", context, status=status)


def handler400(request, exception=None):
    return _error_response(request, 400, "400", "请求无效", "您的请求无法被服务器处理，请检查后重试。")


def handler403(request, exception=None):
    return _error_response(request, 403, "403", "无权访问", "您没有权限访问该页面，如有疑问请联系管理员。")


def handler404(request, exception=None):
    return _error_response(request, 404, "404", "页面不存在", "您访问的页面不存在或已被移除。")


def handler500(request):
    return _error_response(request, 500, "500", "服务器异常", "服务器开小差了，请稍后重试或联系管理员。")
