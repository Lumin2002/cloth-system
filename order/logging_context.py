"""日志请求上下文：为每条日志附加操作人用户名与来源 IP，并按类别分流。"""
import logging
import threading

_local = threading.local()

# 日志类别：业务明细（默认）/ 操作（请求级，含耗时）/ 登录
CATEGORY_BUSINESS = "business"
CATEGORY_OPERATION = "operation"
CATEGORY_LOGIN = "login"


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
    """补充 record.ip / record.username，并保证 record.duration_ms 有默认值。"""

    def filter(self, record):
        request = getattr(_local, "request", None)
        record.ip = get_client_ip(request)
        record.username = _current_username(request)
        if getattr(record, "duration_ms", None) is None:
            record.duration_ms = "-"
        return True


class CategoryFilter(logging.Filter):
    """按日志类别分流：handler 只接收指定类别（或排除指定类别）的记录。

    记录未显式指定 category 时按业务明细处理，保持原有日志文件内容不变。
    """

    def __init__(self, name="", category=None, exclude=()):
        super().__init__(name)
        self.category = category
        self.exclude = tuple(exclude)

    def filter(self, record):
        category = getattr(record, "category", CATEGORY_BUSINESS)
        if self.category is not None:
            return category == self.category
        return category not in self.exclude

