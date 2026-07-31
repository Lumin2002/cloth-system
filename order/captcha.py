"""登录验证码：生成 SVG 验证码并校验，配合登录限流防止暴力破解。"""
import random
import time

CAPTCHA_SESSION_KEY = "login_captcha"
CAPTCHA_LENGTH = 4
CAPTCHA_TTL = 300
CAPTCHA_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
SVG_COLORS = ["#1e40af", "#7c3aed", "#0f766e", "#be185d", "#b45309", "#1f2937"]
NOISE_COLORS = ["#94a3b8", "#cbd5e1", "#fca5a5", "#86efac", "#93c5fd"]


def generate_code(length=CAPTCHA_LENGTH):
    rng = random.SystemRandom()
    return "".join(rng.choice(CAPTCHA_CHARS) for _ in range(length))


def render_captcha_svg(code):
    """把验证码绘制成带噪点和干扰线的 SVG。"""
    width, height = 150, 52
    rng = random.SystemRandom()
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc" rx="8"/>',
    ]
    for _ in range(rng.randint(5, 8)):
        x1, y1 = rng.randint(0, width), rng.randint(0, height)
        x2, y2 = rng.randint(0, width), rng.randint(0, height)
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{rng.choice(NOISE_COLORS)}" stroke-width="{rng.randint(1, 2)}" '
            'stroke-linecap="round" opacity="0.45"/>'
        )
    for _ in range(rng.randint(18, 30)):
        parts.append(
            f'<circle cx="{rng.randint(0, width)}" cy="{rng.randint(0, height)}" '
            f'r="{rng.randint(1, 2)}" fill="{rng.choice(NOISE_COLORS)}" opacity="0.5"/>'
        )
    step = width / (len(code) + 1)
    for index, ch in enumerate(code):
        x = int(step * (index + 1))
        y = rng.randint(31, 41)
        angle = rng.randint(-18, 18)
        parts.append(
            f'<text x="{x}" y="{y}" transform="rotate({angle} {x} {y})" '
            f'font-family="Consolas, monospace" font-size="30" font-weight="bold" '
            f'fill="{rng.choice(SVG_COLORS)}">{ch}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def set_captcha(request, code=None):
    """生成新验证码并写入会话，返回验证码文本。"""
    payload = {
        "code": code or generate_code(),
        "expires": time.time() + CAPTCHA_TTL,
    }
    request.session[CAPTCHA_SESSION_KEY] = payload
    return payload["code"]


def verify_captcha(request, answer):
    """校验用户输入，大小写不敏感；正确后立即清除验证码。"""
    payload = request.session.get(CAPTCHA_SESSION_KEY)
    if not payload:
        return False
    try:
        expires = float(payload.get("expires") or 0)
    except (TypeError, ValueError):
        expires = 0
    if time.time() > expires:
        request.session.pop(CAPTCHA_SESSION_KEY, None)
        return False
    expected = str(payload.get("code") or "").strip().upper()
    given = str(answer or "").strip().upper()
    if not expected or not given or given != expected:
        return False
    request.session.pop(CAPTCHA_SESSION_KEY, None)
    return True


def clear_captcha(request):
    request.session.pop(CAPTCHA_SESSION_KEY, None)
