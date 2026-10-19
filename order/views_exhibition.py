"""视图模块：展会客户信息。

记录展会期间接触的客户，所有字段均可留空。
"""

import io
import os
import re
import zipfile
from datetime import datetime
from functools import lru_cache

from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from PIL import Image, ImageDraw, ImageFont

from .decorators import admin_required
from .forms import ExhibitionContactForm
from .models import ExhibitionContact
from .views_common import logger


@admin_required
def exhibition_list(request):
    """展会客户列表（支持关键字与日期范围筛选）。"""
    qs = ExhibitionContact.objects.select_related("created_by").all()

    keyword = (request.GET.get("search") or "").strip()
    if keyword:
        qs = qs.filter(
            Q(company_name__icontains=keyword)
            | Q(contact_name__icontains=keyword)
            | Q(email__icontains=keyword)
            | Q(owner_name__icontains=keyword)
            | Q(whatsapp__icontains=keyword)
            | Q(company_address__icontains=keyword)
        )

    start_date = (request.GET.get("start_date") or "").strip()
    end_date = (request.GET.get("end_date") or "").strip()
    if start_date:
        qs = qs.filter(visit_date__gte=start_date)
    if end_date:
        qs = qs.filter(visit_date__lte=end_date)

    return render(
        request,
        "order/exhibition_list.html",
        {
            "contacts": qs,
            "total": qs.count(),
            "keyword": keyword,
            "start_date": start_date,
            "end_date": end_date,
        },
    )


@admin_required
def exhibition_create(request):
    """新增展会客户。"""
    form = ExhibitionContactForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        contact = form.save(commit=False)
        contact.created_by = request.user
        contact.save()
        logger.info("[展会信息] %s 新增展会客户 %s", request.user.username, contact)
        messages.success(request, "展会客户信息已保存")
        return redirect("exhibition_list")

    return render(
        request,
        "order/exhibition_form.html",
        {
            "form": form,
            "form_title": "新增展会客户",
            "submit_label": "保存",
        },
    )


@admin_required
def exhibition_h5(request):
    """H5 移动端快速录入（保存后停留在本页，方便连续录入）。"""
    saved = (request.GET.get("saved") or "") == "1"

    if request.method == "POST":
        form = ExhibitionContactForm(request.POST, request.FILES)
        if form.is_valid():
            contact = form.save(commit=False)
            contact.created_by = request.user
            contact.save()
            logger.info(
                "[展会信息] %s H5 录入展会客户 %s", request.user.username, contact
            )
            return redirect(f"{reverse('exhibition_h5')}?saved=1")
    else:
        form = ExhibitionContactForm(initial={"visit_date": timezone.localdate()})

    return render(
        request,
        "order/exhibition_h5.html",
        {"form": form, "saved": saved},
    )


@admin_required
def exhibition_edit(request, pk):
    """编辑展会客户。"""
    contact = get_object_or_404(ExhibitionContact, pk=pk)
    form = ExhibitionContactForm(
        request.POST or None, request.FILES or None, instance=contact
    )
    if request.method == "POST" and form.is_valid():
        contact = form.save()
        logger.info("[展会信息] %s 修改展会客户 %s", request.user.username, contact)
        messages.success(request, "展会客户信息已更新")
        return redirect("exhibition_list")

    return render(
        request,
        "order/exhibition_form.html",
        {
            "form": form,
            "contact": contact,
            "form_title": f"编辑展会客户：{contact}",
            "submit_label": "保存修改",
        },
    )


@admin_required
def exhibition_delete(request, pk):
    """删除展会客户（连同已上传的合照）。"""
    contact = get_object_or_404(ExhibitionContact, pk=pk)
    if request.method == "POST":
        label = str(contact)
        if contact.photo:
            contact.photo.delete(save=False)
        contact.delete()
        logger.info("[展会信息] %s 删除展会客户 %s", request.user.username, label)
        messages.success(request, f"已删除展会客户 {label}")
    return redirect("exhibition_list")


# ---------------------------------------------------------------------------
# 批量导出合照（把备注绘制到图片上，ZIP 打包）

# 中文字体候选路径（Windows 开发 / Linux 容器）
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
]


@lru_cache(maxsize=None)
def _resolve_font_path():
    """返回第一个可用的中文字体路径；没有则返回 None。"""
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def _load_font(size: int):
    path = _resolve_font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            logger.warning("[展会信息] 字体加载失败：%s", path)
    return ImageFont.load_default()


def _wrap_text(text: str, max_chars: int) -> list:
    """按字符数换行（中文场景足够），保留原有的显式换行。"""
    lines = []
    for paragraph in str(text or "").splitlines() or [""]:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while len(paragraph) > max_chars:
            lines.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        if paragraph:
            lines.append(paragraph)
    return lines or [""]


def _attach_remark(image: Image.Image, remark: str, max_lines: int = 6) -> Image.Image:
    """在图片底部绘制备注文字（半透明黑底 + 白字）。无备注则原样返回。"""
    text = (remark or "").strip()
    if not text:
        return image

    base = image.convert("RGB")
    font_size = max(18, min(48, base.width // 28))
    font = _load_font(font_size)

    max_chars = max(8, int(base.width / (font_size * 1.05)))
    all_lines = _wrap_text(text, max_chars)
    lines = all_lines[:max_lines]
    if len(all_lines) > max_lines:
        lines[-1] = lines[-1][: max(1, max_chars - 1)] + "…"

    line_height = font_size + 8
    padding = max(12, font_size // 2)
    box_height = line_height * len(lines) + padding * 2

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle(
        [(0, base.height - box_height), (base.width, base.height)],
        fill=(0, 0, 0, 170),
    )

    y = base.height - box_height + padding
    for line in lines:
        draw.text((padding, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_height

    return Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")


_SAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _safe_filename(contact, suffix: str = "jpg") -> str:
    raw = "_".join(
        part
        for part in [contact.company_name, contact.contact_name, str(contact.pk)]
        if part
    )
    name = _SAFE_NAME_RE.sub("_", raw).strip("_") or f"exhibition_{contact.pk}"
    if suffix:
        return f"{name[:80]}.{suffix}"
    return name[:80]


@admin_required
@require_POST
def exhibition_export_photos(request):
    """批量导出所选记录的合照，并把备注绘制到图片上（ZIP 打包）。"""
    raw_ids = request.POST.getlist("ids")
    if not raw_ids:
        messages.warning(request, "请先勾选要导出的记录")
        return redirect("exhibition_list")

    try:
        pk_list = [int(x) for x in raw_ids if str(x).strip()]
    except (TypeError, ValueError):
        messages.error(request, "勾选数据无效")
        return redirect("exhibition_list")

    contacts = ExhibitionContact.objects.filter(pk__in=pk_list).order_by("pk")

    buffer = io.BytesIO()
    exported = 0
    skipped = []

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for contact in contacts:
            if not contact.photo:
                skipped.append(f"{_safe_filename(contact, '')}：无合照")
                continue
            try:
                with contact.photo.open("rb") as handle:
                    image = Image.open(handle)
                    image.load()
                image = _attach_remark(image, contact.remark)
                out = io.BytesIO()
                image.save(out, format="JPEG", quality=90)
                archive.writestr(_safe_filename(contact), out.getvalue())
                exported += 1
            except Exception as exc:
                logger.exception("[展会信息] 导出合照失败 pk=%s", contact.pk)
                skipped.append(f"{_safe_filename(contact, '')}：{exc}")

        if skipped:
            archive.writestr(
                "导出说明.txt",
                "\n".join(["以下记录未导出："] + skipped),
            )

    if exported == 0:
        messages.warning(request, "所选记录中没有可导出的合照")
        return redirect("exhibition_list")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger.info(
        "[展会信息] %s 导出 %s 张合照（跳过 %s 条）",
        request.user.username,
        exported,
        len(skipped),
    )
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = (
        f'attachment; filename="exhibition_photos_{timestamp}.zip"'
    )
    return response
