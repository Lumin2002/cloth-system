"""模板上下文处理器：向前端提供会话超时配置。"""
from django.conf import settings

from .middleware import get_session_timeout_minutes


def session_security(request):
    timeout_minutes = get_session_timeout_minutes(getattr(request, "user", None))
    return {
        "session_timeout_seconds": timeout_minutes * 60,
        "session_timeout_warning_seconds": int(
            getattr(settings, "SESSION_TIMEOUT_WARNING_SECONDS", 60)
        ),
    }
