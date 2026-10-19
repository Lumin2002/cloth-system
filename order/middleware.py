"""供应商/超管会话空闲超时中间件。"""
import logging
import time
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect

from .logging_context import CATEGORY_OPERATION, clear_request, set_request

logger = logging.getLogger(__name__)

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

# 不写入操作日志的路径：静态资源、后台轮询、登录/登出（登录登出记入登录日志）
_SKIP_OPERATION_LOG_PREFIXES = _BACKGROUND_POLL_PATHS + (
    "/static/",
    "/media/",
    "/favicon.ico",
    "/admin/jsi18n/",
    "/logout/",
    "/supplier/logout/",
)
_SKIP_OPERATION_LOG_EXACT = ("/",)


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


def should_log_operation(request):
    """判断该请求是否写入操作日志（静态资源、后台轮询、登录登出不记）。"""
    path = request.path
    if path in _SKIP_OPERATION_LOG_EXACT:
        return False
    return not any(path.startswith(prefix) for prefix in _SKIP_OPERATION_LOG_PREFIXES)


class RequestLogContextMiddleware:
    """把当前请求放入线程局部变量供日志过滤器取用户名与 IP，并在请求结束后写操作日志。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_request(request)
        started = time.perf_counter()
        status_code = 500
        try:
            response = self.get_response(request)
            status_code = getattr(response, "status_code", 200)
            return response
        finally:
            try:
                self._log_operation(request, status_code, started)
            except Exception:  # 记日志失败不能影响正常响应
                logger.exception("[操作日志] 写入失败")
            clear_request()

    @staticmethod
    def _log_operation(request, status_code, started):
        if not should_log_operation(request):
            return
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "[操作] %s %s -> %s",
            request.method,
            request.path,
            status_code,
            extra={
                "category": CATEGORY_OPERATION,
                "duration_ms": duration_ms,
            },
        )


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
