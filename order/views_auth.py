"""视图模块：认证：登录/登出/验证码（由 views.py 拆分而来）。"""

from django.contrib import messages
from django.core.cache import cache
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from .captcha import clear_captcha, render_captcha_svg, set_captcha, verify_captcha
from .i18n import t
from .views_common import logger


def home_view(request):
    if request.method == "POST":
        # Rate limiting: 5 attempts per minute per IP
        ip = request.META.get("REMOTE_ADDR", "")
        rate_key = f"login_rate:{ip}"
        attempts = cache.get(rate_key, 0)
        if attempts >= 5:
            logger.warning(t("log.login_rate_limited", ip=ip))
            messages.error(request, "登录尝试过于频繁，请60秒后再试")
            return redirect("home")
        cache.set(rate_key, attempts + 1, 60)

        if not verify_captcha(request, request.POST.get("captcha")):
            messages.error(request, "验证码错误或已过期，请重新输入")
            return redirect("home")

        user = authenticate(
            request,
            username=request.POST.get("username"),
            password=request.POST.get("password"),
        )
        if user is not None:
            if hasattr(user, "supplier_profile") and not user.supplier_profile.is_active:
                messages.error(request, t("auth.supplier_disabled"))
                return redirect("home")
            login(request, user)
            clear_captcha(request)
            # mark_session_activity(request)  # 会话超时功能已注释停用
            logger.info(t("log.login", username=request.POST.get("username")))
            if hasattr(user, "supplier_profile"):
                return redirect("supplier_dashboard")
            return redirect("dashboard")

        # 检查是否是被禁用的供应商（密码错误也提示禁用，避免用户名枚举）
        try:
            from django.contrib.auth.models import User as AuthUser

            disabled_user = AuthUser.objects.get(username=request.POST.get("username"))
            if hasattr(disabled_user, "supplier_profile") and not disabled_user.supplier_profile.is_active:
                messages.error(request, t("auth.supplier_disabled"))
                return redirect("home")
        except Exception:
            pass

        logger.warning(t("log.login_failed", username=request.POST.get("username", ""), ip=ip))
        messages.error(request, t("auth.login_error"))

    if request.user.is_authenticated:
        if hasattr(request.user, "supplier_profile") and not request.user.supplier_profile.is_active:
            logout(request)
            return redirect("home")
        if hasattr(request.user, "supplier_profile"):
            return redirect("supplier_dashboard")
        return redirect("dashboard")

    return render(request, "order/home.html")


def captcha_image(request):
    code = set_captcha(request)
    response = HttpResponse(
        render_captcha_svg(code),
        content_type="image/svg+xml; charset=utf-8",
    )
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    return response


def logout_view(request):
    logger.info(t("log.logout", username=request.user.username))
    logout(request)
    return redirect("home")


def supplier_logout_view(request):
    logout(request)
    return redirect("home")
