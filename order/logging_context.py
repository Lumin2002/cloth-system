"""日志请求上下文：为每条日志附加操作人用户名与来源 IP。"""
import logging
import threading

_local = threading.local()


def set_request(request):
    """由中间件在每个请求开始时调用，记录当前请求。"""
    _local.request = request


def clear_request():
    """请求结束后清除上下文，避免线程复用导致串号。"""
    _local.request = None


def get_client_ip(request):
    """提取客户端 IP，优先取 X-Forwarded-For 第一段（反向代理场景）。"""
    if request is None:
        return "-"
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "-")


def _current_username(request):
    if request is None:
        return "-"
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return "-"
    return getattr(user, "username", "-")


class RequestContextFilter(logging.Filter):
    """给日志记录补充 record.ip 和 record.username，由 formatter 使用。"""

    def filter(self, record):
        request = getattr(_local, "request", None)
        record.ip = get_client_ip(request)
        record.username = _current_username(request)
        return True
