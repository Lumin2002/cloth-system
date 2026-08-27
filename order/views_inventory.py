"""视图模块：库存（由 views.py 拆分而来）。"""

import pandas as pd
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import models
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
from django.utils.timezone import now
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from .decorators import admin_required, validate_file_upload
from .forms import (
    CREATE_DEFAULTS,
    ORDER_CREATE_PRIMARY_COUNT,
    ORDER_FORM_SECTIONS,
    ClothOrderForm,
    InventoryItemForm,
    ShipmentForm,
    SupplierManageForm,
    SupplierPriceForm,
)
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


@admin_required
def inventory_import_page(request):
    return render(request, "order/inventory_import.html")


@admin_required
def inventory_import_start(request):
    task_id = create_inventory_task(request.user.id)
    logger.info(t("log.import_inventory_start", username=request.user.username))
    start_inventory_task(task_id, request.upload_file_bytes)
    return JsonResponse({"task_id": task_id})


@admin_required
def inventory_import_progress(request, task_id):
    task = get_inventory_task(str(task_id), request.user.id)
    if not task:
        return JsonResponse(
            {
                "status": "pending",
                "percent": 0,
                "message":  t("page.task_initializing"),
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
            "phase": task.get("phase", "import"),
            "current": task.get("current", 0),
            "total": task.get("total", 0),
            "result": task.get("result"),
            "error": task.get("error"),
        }
    )


@admin_required
def inventory_export(request):
    return redirect("inventory_list")


@admin_required
def inventory_delete_all(request):
    count = InventoryItem.objects.all().delete()[0]
    logger.info(t("log.inventory_delete_all", username=request.user.username, count=count))
    messages.success(request, t("msg.all_inventory_cleared", count=count))
    return redirect("inventory_list")


class InventoryListView(AdminRequiredMixin, LoginRequiredMixin, ListView):
    model = InventoryItem
    template_name = 'order/inventory_list.html'
    context_object_name = 'items'
    paginate_by = 50
    per_page_options = (20, 50, 100, 200)

    def get_paginate_by(self, queryset):
        raw = self.request.GET.get('per_page', '')
        try:
            size = int(raw)
            if size in self.per_page_options:
                return size
        except (TypeError, ValueError):
            pass
        return self.paginate_by

    def get_queryset(self):
        qs = InventoryItem.objects.all()
        p = self.request.GET
        search = p.get('search', '').strip()
        if search:
            from django.db.models import Q
            qs = qs.filter(
                Q(cloth_name__icontains=search) | Q(color__icontains=search) | Q(unique_id__icontains=search) |
                Q(customer__icontains=search) | Q(specification__icontains=search) | Q(composition_cn__icontains=search)
            )

        customer = p.get('customer', '').strip()
        if customer:
            qs = qs.filter(customer=customer)

        position = p.get('position', '').strip()
        if position:
            qs = qs.filter(position__icontains=position)

        qty_min = p.get('qty_min', '').strip()
        if qty_min:
            try:
                qs = qs.filter(quantity__gte=int(qty_min))
            except ValueError:
                pass

        qty_max = p.get('qty_max', '').strip()
        if qty_max:
            try:
                qs = qs.filter(quantity__lte=int(qty_max))
            except ValueError:
                pass

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        context.update({
            'total_count': qs.count(),
            'total_quantity': qs.aggregate(total=models.Sum('quantity'))['total'] or 0,
            'chip_zero': InventoryItem.objects.filter(quantity=0).count(),
            'chip_low': InventoryItem.objects.filter(quantity__gt=0, quantity__lt=50).count(),
            'chip_normal': InventoryItem.objects.filter(quantity__gte=50).count(),
            'customers': InventoryItem.objects.exclude(customer='').values_list('customer', flat=True).distinct().order_by('customer'),
            'positions': InventoryItem.objects.exclude(position='').values_list('position', flat=True).distinct().order_by('position'),
            'per_page': self.get_paginate_by(qs),
            'per_page_options': self.per_page_options,
            'active_filters': any(self.request.GET.get(k, '').strip() for k in ('search', 'customer', 'position', 'qty_min', 'qty_max')),
        })
        return context


class InventoryDetailView(AdminRequiredMixin, LoginRequiredMixin, DetailView):
    model = InventoryItem
    template_name = "order/inventory_detail.html"
    context_object_name = "item"


class InventoryUpdateView(AdminRequiredMixin, LoginRequiredMixin, UpdateView):
    model = InventoryItem
    form_class = InventoryItemForm
    template_name = "order/inventory_form.html"
    success_url = "/inventory/"
    context_object_name = "item"

    def form_valid(self, form):
        response = super().form_valid(form)
        item = self.object
        logger.info(
            t(
                "log.inventory_update",
                username=self.request.user.username,
                pk=item.pk,
                name=item.cloth_name or item.unique_id or "-",
                qty=item.quantity,
            )
        )
        return response


@admin_required
def inventory_bulk_action(request):
    return redirect("inventory_list")


class InventoryLogListView(AdminRequiredMixin, LoginRequiredMixin, ListView):
    model = InventoryLog
    template_name = "order/inventory_log_list.html"
    context_object_name = "items"
    paginate_by = 50
    per_page_options = (20, 50, 100, 200)

    def get_paginate_by(self, queryset):
        raw = self.request.GET.get('per_page', '')
        try:
            size = int(raw)
            if size in self.per_page_options:
                return size
        except (TypeError, ValueError):
            pass
        return self.paginate_by

    def get_queryset(self):
        qs = InventoryLog.objects.select_related('item').all()
        p = self.request.GET

        search = p.get('search', '').strip()
        if search:
            from django.db.models import Q
            qs = qs.filter(
                Q(item__cloth_name__icontains=search) |
                Q(item__customer__icontains=search) |
                Q(created_by__icontains=search) |
                Q(remark__icontains=search)
            )

        log_type = p.get('log_type', '').strip()
        if log_type:
            qs = qs.filter(log_type=log_type)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        total_qs = InventoryLog.objects

        ctx['total_count'] = total_qs.count()
        ctx['in_count'] = total_qs.filter(log_type='in').count()
        ctx['out_count'] = total_qs.filter(log_type='out').count()
        ctx['adjust_count'] = total_qs.filter(log_type='adjust').count()
        ctx['log_type_choices'] = InventoryLog.LOG_TYPE_CHOICES
        ctx['per_page_options'] = self.per_page_options
        ctx['per_page'] = self.request.GET.get('per_page', self.paginate_by)
        ctx['active_filters'] = bool(
            self.request.GET.get('search') or
            self.request.GET.get('log_type')
        )
        ctx['logs'] = ctx.get('items') or ctx.get('object_list') or []
        return ctx


@admin_required
def inventory_log_export(request):
    """导出全部库存日志到 Excel"""
    qs = InventoryLog.objects.select_related('item').order_by('-created_at')
    rows = [{
        '操作时间': log.created_at.strftime('%Y-%m-%d %H:%M'),
        '布料名称': log.item.cloth_name if log.item else '',
        '唯一标识': log.item.unique_id if log.item else '',
        '客户': log.item.customer if log.item else '',
        '变动类型': log.get_log_type_display(),
        '变动数量': float(log.quantity),
        '操作人': log.created_by or '系统',
        '备注': log.remark or '',
    } for log in qs]
    df = pd.DataFrame(rows)
    filename = f"inventory_logs_{now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    with pd.ExcelWriter(response, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="库存日志", index=False)
    return response


@admin_required
def inventory_log_delete_all(request):
    """清空全部库存日志"""
    count = InventoryLog.objects.all().delete()[0]
    logger.info(t("log.inventory_log_delete_all", username=request.user.username, count=count))
    messages.success(request, f"已清空全部 {count} 条库存日志")
    return redirect("inventory_log_list")
