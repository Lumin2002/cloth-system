"""视图模块：布种编号（由 views.py 拆分而来）。"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import (
    Count,
    DateField,
    DecimalField,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from .decorators import admin_required, validate_file_upload
from .i18n import t
from .import_progress import (
    create_catalog_task,
    create_import_task,
    create_inventory_task,
    get_catalog_task,
    get_import_task,
    get_inventory_task,
    start_catalog_task,
    start_import_task,
    start_inventory_task,
)
from .models import (
    ClothCatalog,
    ClothOrder,
    FabricQuotation,
    InventoryItem,
    InventoryLog,
    Notification,
    Shipment,
    Supplier,
    notify_all_staff,
    notify_user,
)
from .views_common import AdminRequiredMixin, logger


class ClothCatalogListView(AdminRequiredMixin, LoginRequiredMixin, ListView):
    model = ClothCatalog
    template_name = "order/cloth_catalog_list.html"
    context_object_name = "items"
    paginate_by = None

    def get_queryset(self):
        return ClothCatalog.objects.all()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        items = list(ClothCatalog.objects.all().order_by("cloth_code"))
        ctx["total"] = len(items)
        ctx["items_json"] = [
            {
                "id": item.pk,
                "cloth_code": item.cloth_code or "",
                "cloth_name": item.cloth_name or "",
                "cloth_type": item.cloth_type or "",
                "customer": item.customer or "",
                "composition_cn": item.composition_cn or "",
                "width": item.width or "",
                "weight": item.weight or "",
                "specification": item.specification or "",
                "detail_url": reverse("cloth_catalog_detail", kwargs={"pk": item.pk}),
                "delete_url": reverse("cloth_catalog_delete", kwargs={"pk": item.pk}),
            }
            for item in items
        ]
        return ctx


class ClothCatalogDetailView(AdminRequiredMixin, LoginRequiredMixin, DetailView):
    model = ClothCatalog
    template_name = "order/cloth_catalog_detail.html"
    context_object_name = "item"


class ClothCatalogCreateView(AdminRequiredMixin, LoginRequiredMixin, CreateView):
    model = ClothCatalog
    fields = [
        "cloth_code",
        "cloth_name",
        "cloth_type",
        "customer",
        "composition_cn",
        "composition_en",
        "width",
        "weight",
        "specification",
        "density",
        "process_cn",
        "process_en",
        "remark",
        "supplier1",
        "supplier1_code",
        "supplier2",
    ]
    template_name = "order/cloth_catalog_form.html"
    success_url = "/cloth-catalog/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = t("label.catalog_add")
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        logger.info(
            t(
                "log.catalog_create",
                username=self.request.user.username,
                code=self.object.cloth_code,
                name=self.object.cloth_name or "-",
            )
        )
        return response


@admin_required
def cloth_catalog_import(request):
    return render(request, "order/cloth_catalog_import.html")


@admin_required
def cloth_catalog_import_start(request):
    task_id = create_catalog_task(request.user.id)
    logger.info(t("log.catalog_task", task_id=task_id, user_id=request.user.id))
    start_catalog_task(task_id, request.upload_file_bytes)
    return JsonResponse({"task_id": task_id})


@admin_required
def cloth_catalog_import_progress(request, task_id):
    try:
        task = get_catalog_task(str(task_id), request.user.id)
        if not task:
            return JsonResponse(
                {
                    "status": "pending",
                    "percent": 0,
                    "message": t("page.task_initializing"),
                    "phase": "pending",
                    "current": 0,
                    "total": 0,
                    "result": None,
                    "error": None,
                }
            )
        return JsonResponse(
            {
                "status": task.get("status"),
                "percent": task.get("percent", 0),
                "message": task.get("message", ""),
                "phase": task.get("phase", ""),
                "current": task.get("current", 0),
                "total": task.get("total", 0),
                "result": task.get("result"),
                "error": task.get("error"),
            }
        )
    except Exception as e:
        logger.error(f"Catalog progress error: {e}", exc_info=True)
        return JsonResponse(
            {
                "status": "error",
                "percent": 0,
                "message": str(e),
                "phase": "error",
                "current": 0,
                "total": 0,
                "result": None,
                "error": str(e),
            }
        )


@admin_required
def cloth_catalog_delete(request, pk):
    obj = get_object_or_404(ClothCatalog, pk=pk)
    code = obj.cloth_code
    obj.delete()
    logger.info(t("log.catalog_delete", username=request.user.username, code=code))
    messages.success(request, t("msg.catalog_deleted", code=obj.cloth_code))
    return redirect("cloth_catalog_list")


@admin_required
def cloth_catalog_delete_all(request):
    count = ClothCatalog.objects.all().delete()[0]
    logger.info(t("log.catalog_delete_all", username=request.user.username, count=count))
    messages.success(request, t("msg.all_catalog_cleared", count=count))
    return redirect("cloth_catalog_list")


@admin_required
def cloth_catalog_autocomplete(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 1:
        return JsonResponse([], safe=False)
    qs = ClothCatalog.objects.filter(
        Q(cloth_code__icontains=q) | Q(cloth_name__icontains=q)
    ).values(
        "id",
        "cloth_code",
        "cloth_name",
        "cloth_type",
        "composition_cn",
        "composition_en",
        "width",
        "weight",
        "specification",
        "density",
        "process_cn",
        "process_en",
        "customer",
        "supplier1",
        "supplier1_code",
        "supplier2",
    )[:20]
    return JsonResponse(list(qs), safe=False)
