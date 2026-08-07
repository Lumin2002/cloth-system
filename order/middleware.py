"""供应商/超管会话空闲超时中间件。"""
import time
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect

from .logging_context import clear_request, set_request

SESSION_LAST_ACTIVITY_KEY = "_session_last_activity"

# 这些轮询接口不应被当作“用户正在操作”，避免后台请求无限续期会话
_BACKGROUND_POLL_PATHS = (
    "/notifications/unread-count/",
    "/notifications/unread-list/",
    "/orders/updated-at/",
    "/monitor/api/",
    "/orders/import/progress/",
    "/inventory/import/progress/",
    "/cloth-catalog/import/progress/",
)


def get_session_timeout_minutes(user):
    """按角色返回空闲超时分钟数；不适用返回 0。"""
    if user is None or not getattr(user, "is_authenticated", False):
        return 0
    if hasattr(user, "supplier_profile"):
        return max(0, int(getattr(settings, "SUPPLIER_SESSION_IDLE_TIMEOUT_MINUTES", 0) or 0))
    if getattr(user, "is_superuser", False):
        return max(0, int(getattr(settings, "SUPERUSER_SESSION_IDLE_TIMEOUT_MINUTES", 0) or 0))
    return 0


def mark_session_activity(request):
    """登录成功后记录活动时间并设置会话过期时间。"""
    timeout_minutes = get_session_timeout_minutes(getattr(request, "user", None))
    if timeout_minutes > 0:
        request.session[SESSION_LAST_ACTIVITY_KEY] = time.time()
        request.session.set_expiry(timeout_minutes * 60)


def _is_background_poll(request):
    return any(request.path.startswith(prefix) for prefix in _BACKGROUND_POLL_PATHS)


class RequestLogContextMiddleware:
    """把当前请求放入线程局部变量，供日志过滤器提取用户名与 IP。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_request(request)
        try:
            return self.get_response(request)
        finally:
            clear_request()


class SessionTimeoutMiddleware:
    """请求级空闲超时校验，供应商与超管账号单独配置。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        timeout_minutes = get_session_timeout_minutes(getattr(request, "user", None))
        if timeout_minutes <= 0:
            return self.get_response(request)

        now_ts = time.time()
        timeout_seconds = timeout_minutes * 60
        last_ts = request.session.get(SESSION_LAST_ACTIVITY_KEY)
        if last_ts is not None:
            try:
                idle_seconds = now_ts - float(last_ts)
            except (TypeError, ValueError):
                idle_seconds = timeout_seconds + 1
            if idle_seconds > timeout_seconds:
                return self._expire(request)

        # 轮询接口不续期会话，但超时时仍会强制退出
        if not _is_background_poll(request):
            request.session[SESSION_LAST_ACTIVITY_KEY] = now_ts
            request.session.set_expiry(timeout_seconds)

        return self.get_response(request)

    def _expire(self, request):
        from .i18n import t

        logout(request)
        if _is_background_poll(request) or request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"error": t("auth.session_expired")}, status=401)

        messages.error(request, t("auth.session_expired"))
        if request.path.startswith("/admin/"):
            return redirect(f"/admin/login/?next={quote(request.path)}")
        return redirect("home")
