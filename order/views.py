import pandas as pd
from io import BytesIO

import json

import logging

from django.contrib import messages

from django.contrib.auth import authenticate, login, logout

from django.contrib.auth.decorators import login_required

from django.contrib.auth.mixins import LoginRequiredMixin

from django.db.models import Count, Sum, Q

from django.db import transaction

from django.db import models as models

from django.http import HttpResponse, JsonResponse

from django.shortcuts import get_object_or_404, redirect, render

from django.views.decorators.http import require_GET, require_POST

from django.urls import reverse

from django.utils.http import url_has_allowed_host_and_scheme

from django.utils.timezone import now

from django.views.generic import CreateView, DetailView, ListView, UpdateView



from .decorators import admin_required, validate_ids, validate_file_upload

from .forms import (
    InventoryItemForm,

    CREATE_DEFAULTS,

    ORDER_CREATE_PRIMARY_COUNT,

    ORDER_FORM_SECTIONS,

    ClothOrderForm,

    SupplierPriceForm,

    SupplierManageForm,

)

from .models import Notification, notify_user, notify_all_staff, ClothOrder, ClothCatalog, FabricQuotation, InventoryLog, InventoryItem, Shipment, Supplier

from .import_progress import (create_import_task, get_import_task, start_import_task,

        create_catalog_task, get_catalog_task, start_catalog_task)

from .order_excel import export_orders_dataframe, import_orders_from_file

from .dashboard_stats import build_month_compare, build_monthly_chart_data

from .order_filters import filter_orders_queryset


logger = logging.getLogger(__name__)



from functools import wraps





class AdminRequiredMixin:

    """管理端视图混合器 - 供应商账号访问时重定向到供应商面料""

    def dispatch(self, request, *args, **kwargs):

        if request.user.is_authenticated and hasattr(request.user, 'supplier_profile') and not request.user.is_staff:

        
            messages.warning(request, '无权访问管理面板')

            return redirect('supplier_dashboard')

        return super().dispatch(request, *args, **kwargs)





def require_admin(view_func):

    """函数视图装饰�?-  供应商账号访问时重定向到供应商面�?""

    @wraps(view_func)

    def _wrapped(request, *args, **kwargs):

        if request.user.is_authenticated and hasattr(request.user, 'supplier_profile') and not request.user.is_staff:

    
            messages.warning(request, '无权访问管理面板')

            return redirect('supplier_dashboard')

        return view_func(request, *args, **kwargs)

    return _wrapped









# ---------------------------------------------------------------------------

# 认证

# ---------------------------------------------------------------------------



def home_view(request):

    if request.method == 'POST':

        user = authenticate(

            request,

            username=request.POST.get('username'),

            password=request.POST.get('password'),

        )

        if user is not None:
            if hasattr(user, 'supplier_profile') and not user.supplier_profile.is_active:
                messages.error(request, '该供应商账号已被禁用，请联系管理�?)
                return redirect('home')
            login(request, user)

            if hasattr(user, 'supplier_profile'):

                return redirect('supplier_dashboard')

            return redirect('dashboard')

        # Check if this is a disabled supplier
        try:
            from django.contrib.auth.models import User as AuthUser
            disabled_user = AuthUser.objects.get(username=request.POST.get('username'))
            if hasattr(disabled_user, 'supplier_profile') and not disabled_user.supplier_profile.is_active:
                messages.error(request, '该供应商账号已被禁用，请联系管理�?)
                return redirect('home')
        except AuthUser.DoesNotExist:
            pass

        logger.info(f"[登录] {request.POST.get('username')} 登录成功")
        messages.error(request, '用户名或密码错误')



    if request.user.is_authenticated:
        if hasattr(request.user, 'supplier_profile') and not request.user.supplier_profile.is_active:
            logout(request)
            return redirect('home')

        if hasattr(request.user, 'supplier_profile'):

            return redirect('supplier_dashboard')

        return redirect('dashboard')

    return render(request, 'order/home.html')



def logout_view(request):
    logger.info(f"[登出] {request.user.username} 登出系统")
    logout(request)
    return redirect('home')





# ---------------------------------------------------------------------------

# 仪表�?

# ---------------------------------------------------------------------------



@admin_required

def dashboard_view(request):

    orders = ClothOrder.objects.all()

    active_orders = orders.filter(order_status='active')

    total_orders = orders.count()

    cancelled_orders = orders.filter(order_status='cancelled').count()

    total_revenue = sum(o.computed_finished_product_total_amount for o in active_orders)

    total_cost = sum(o.computed_total_amount for o in active_orders)

    total_profit = total_revenue - total_cost

    profit_margin = (total_profit / total_revenue * 100) if total_revenue else 0



    top_customers = list(

        active_orders.exclude(customer='')

        .values('customer')

        .annotate(order_count=Count('id'), total_amount=Sum('finished_product_total_amount'))

        .order_by('-total_amount')[:5]

    )

    for row in top_customers:

        amount = row['total_amount'] or 0

        count = row['order_count'] or 0

        row['avg_amount'] = amount / count if count else 0



    order_type_stats = list(

        active_orders.exclude(order_type='')

        .values('order_type')

        .annotate(count=Count('id'))

        .order_by('-count')

    )

    type_labels = dict(ClothOrder.ORDER_TYPE_CHOICES)

    for row in order_type_stats:

        row['label'] = type_labels.get(row['order_type'], row['order_type'] or '未分�?)



    monthly_chart = build_monthly_chart_data(active_orders, months_count=12)

    month_compare = build_month_compare(active_orders)



    # �?Shipment 计算 top 供应�?
    _top_suppliers = []
    _sup_data = {}
    for o in orders.exclude(finished_product_supplier=""):
        s = o.finished_product_supplier
        if s not in _sup_data:
            _sup_data[s] = {"order_count": 0, "total_amount": 0}
        _sup_data[s]["order_count"] += 1
        _sup_data[s]["total_amount"] += o.computed_total_amount
    for s, data in sorted(_sup_data.items(), key=lambda x: -x[1]["total_amount"])[:5]:
        data["finished_product_supplier"] = s
        _top_suppliers.append(data)

    context = {

        'total_orders': total_orders,

        'cancelled_orders': cancelled_orders,

        'active_orders_count': active_orders.count(),

        'total_customers': orders.exclude(customer='').values('customer').distinct().count(),

        'total_suppliers': orders.exclude(finished_product_supplier='').values('finished_product_supplier').distinct().count(),

        'total_revenue': total_revenue,

        'total_cost': total_cost,

        'total_profit': total_profit,

        'profit_margin': profit_margin,

        'paid_orders': active_orders.filter(payment_status='paid').count(),

        'unpaid_orders': active_orders.filter(payment_status='unpaid').count(),

        'overdue_orders': active_orders.filter(overdue_status='overdue').count(),

        'paid_amount_no': active_orders.filter(paid_amount='no').count(),

        'recent_orders': orders.order_by('-order_date', '-created_at')[:8],

        'alert_unpaid': active_orders.filter(payment_status='unpaid').order_by('-order_date')[:5],

        'alert_overdue': active_orders.filter(overdue_status='overdue').order_by('-order_date')[:5],

        'top_customers': top_customers,

        'top_suppliers': _top_suppliers,
        'order_type_stats': order_type_stats,

        'monthly_chart': monthly_chart,

        'monthly_rows': monthly_chart['rows'],

        'month_compare': month_compare,

    }

    return render(request, 'order/dashboard.html', context)





# ---------------------------------------------------------------------------

# 订单单展�?

# ---------------------------------------------------------------------------



class OrderListView(LoginRequiredMixin, ListView):

    model = ClothOrder

    template_name = 'order/order_list.html'

    context_object_name = 'orders'

    paginate_by = 20

    per_page_options = (10, 20, 50, 100)

    def get_paginate_by(self, queryset):
        try:
            return int(self.request.GET.get("per_page", 50))
        except (ValueError, TypeError):
            return 50

    def get_queryset(self):
        qs = ClothOrder.objects.all()
        qs = filter_orders_queryset(self.request.GET, qs)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["per_page_options"] = self.per_page_options
        ctx["per_page"] = self.request.GET.get("per_page", self.paginate_by)
        try:
            ctx["per_page"] = int(ctx["per_page"])
        except ValueError:
            ctx["per_page"] = self.paginate_by

        # Compute stats for the filtered queryset
        filtered = self.get_queryset()
        active_orders = [o for o in filtered if o.order_status == "active"]
        total_count = len(active_orders)
        total_revenue = sum(o.computed_finished_product_total_amount for o in active_orders)
        total_cost = sum(o.computed_total_amount for o in active_orders)
        total_profit = total_revenue - total_cost
        profit_margin = (total_profit / total_revenue * 100) if total_revenue else 0

        ctx["total_count"] = total_count
        ctx["total_revenue"] = total_revenue
        ctx["total_cost"] = total_cost
        ctx["total_profit"] = total_profit
        ctx["filter_profit_margin"] = profit_margin

        # Chip counts
        base = ClothOrder.objects.all()
        ctx["chip_unpaid"] = base.filter(payment_status="unpaid").count()
        ctx["chip_overdue"] = base.filter(overdue_status="overdue").count()
        ctx["chip_paid"] = base.filter(payment_status="paid").count()
        ctx["chip_cancelled"] = base.filter(order_status="cancelled").count()

        # Active filters indicator
        params = self.request.GET
        ctx["active_filters"] = any(k in params for k in
            ["search", "customer", "payment_status", "overdue_status",
             "order_type", "supplier", "supplier_paid", "order_status",
             "start_date", "end_date"])

        # Customer list for filter dropdown
        ctx["customer_list"] = (
            ClothOrder.objects.values_list("customer", flat=True)
            .filter(customer__gt="")
            .distinct()
            .order_by("customer")
        )
        # Supplier list for filter dropdown
        ctx["supplier_list"] = (
            ClothOrder.objects.values_list("finished_product_supplier", flat=True)
            .filter(finished_product_supplier__gt="")
            .distinct()
            .order_by("finished_product_supplier")
        )

        return ctx






class OrderDetailView(LoginRequiredMixin, DetailView):
    model = ClothOrder
    template_name = "order/order_detail.html"
    context_object_name = "order"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        order = self.object
        ctx["edit_mode"] = self.request.GET.get("edit") == "1"
        from .forms import ORDER_FORM_SECTIONS, ClothOrderForm, SupplierPriceForm, ShipmentForm
        ctx["form_sections"] = ORDER_FORM_SECTIONS
        ctx["form"] = ClothOrderForm(instance=order)
        ctx["price_form"] = SupplierPriceForm(instance=order)
        ctx["shipment_form"] = ShipmentForm()
        ctx["computed_total_amount"] = order.computed_total_amount
        ctx["total_value"] = order.calculate_total_value()
        ctx["profit"] = order.get_profit()
        ctx["profit_margin"] = order.get_profit_margin()
        shipments_qs = order.shipments.all().order_by("batch_number")
        from django.db.models import F, Value, FloatField, ExpressionWrapper
        from django.db.models.functions import Coalesce
        cost_price = Coalesce("order__finished_product_cost_price", Value(0))
        qty_field = Coalesce("quantity", Value(0))
        ctx["shipments"] = shipments_qs.annotate(
            shipment_total=ExpressionWrapper(qty_field * cost_price, output_field=FloatField())
        )
        ctx["shipment_total_sum"] = order.shipments.filter(is_deleted=False).aggregate(
            total=models.Sum(F("quantity") * F("order__finished_product_cost_price"))
        )["total"] or 0
        return ctx

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        order = self.object
        form = ClothOrderForm(request.POST, instance=order)
        if form.is_valid():
            form.save()
            messages.success(request, f"订单 {order.serial_number} 已更�?)
            return redirect("order_detail", pk=order.pk)
        else:
            for field, errors in form.errors.items():
                for err in errors:
                    messages.error(request, f"{field}: {err}")
            return self.get(request, *args, **kwargs)


class OrderCreateView(LoginRequiredMixin, CreateView):
    model = ClothOrder
    form_class = ClothOrderForm
    template_name = "order/order_create.html"
    def get_success_url(self):
        return reverse("order_detail", kwargs={"pk": self.object.pk})

    def get_initial(self):
        initial = super().get_initial()
        for k, v in CREATE_DEFAULTS.items():
            if k not in initial:
                initial[k] = v
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_sections_primary"] = ORDER_FORM_SECTIONS[:ORDER_CREATE_PRIMARY_COUNT]
        ctx["form_sections_extra"] = ORDER_FORM_SECTIONS[ORDER_CREATE_PRIMARY_COUNT:]
        return ctx


@login_required
@require_POST
def bulk_orders_action(request):
    action = request.POST.get("action", "").strip()
    order_ids = request.POST.getlist("order_ids")
    next_url = request.POST.get("next", "order_list")
    from django.utils.http import url_has_allowed_host_and_scheme
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = "order_list"

    if not action or not order_ids:
        messages.warning(request, "请选择操作和订�?)
        return redirect(next_url)

    orders = ClothOrder.objects.filter(pk__in=order_ids)
    count = orders.count()

    bulk_actions = {
        "payment_paid": {"payment_status": "paid"},
        "payment_unpaid": {"payment_status": "unpaid"},
        "paid_yes": {"supplier_paid": True},
        "paid_no": {"supplier_paid": False},
        "overdue_yes": {"overdue_status": "overdue"},
        "overdue_no": {"overdue_status": "not_overdue"},
        "invoice_yes": {"invoice_status": "invoiced"},
        "invoice_no": {"invoice_status": "not_invoiced"},
    }

    if action in bulk_actions:
        orders.update(**bulk_actions[action])
        action_labels = {
            "payment_paid": "已付�?, "payment_unpaid": "未付�?,
            "paid_yes": "供应商已付款", "paid_no": "供应商未付款",
            "overdue_yes": "逾期", "overdue_no": "正常",
            "invoice_yes": "已开�?, "invoice_no": "未开�?,
        }
        messages.success(request, f"已将 {count} 条订单标记为「{action_labels.get(action, action)}�?)

    elif action == "delete":
        deleted = orders.delete()[0]
        messages.success(request, f"已删�?{deleted} 条订�?)
    elif action == "export":
        return redirect("orders_export")
    else:
        messages.warning(request, f"未知操作: {action}")

    return redirect(next_url)


@login_required
@login_required
@require_POST
def order_delete(request, pk):
    order = get_object_or_404(ClothOrder, pk=pk)
    serial = order.serial_number
    order.delete()
    messages.success(request, f"订单 {serial} 已删�?)
    return redirect("order_list")


@login_required
def orders_delete_all(request):
    if request.method == "POST":
        count = ClothOrder.objects.all().delete()[0]
        messages.success(request, f"已清空全�?{count} 条订单记�?)
        return redirect("order_list")
    order_count = ClothOrder.objects.count()
    return render(request, "order/delete_all_confirm.html", {"order_count": order_count})


@login_required
def orders_export(request):
    df = export_orders_dataframe()
    filename = f"orders_backup_{now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    with pd.ExcelWriter(response, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="订单数据", index=False)
    return response


@login_required
def orders_import_page(request):
    return render(request, "order/orders_import.html")


@login_required
@require_POST
@validate_file_upload
def orders_import_start(request):
    task_id = create_import_task(request.user.id)
    start_import_task(task_id, request.upload_file_bytes)
    return JsonResponse({"task_id": task_id})


@login_required
def orders_import_progress(request, task_id):
    from django.http import JsonResponse
    t = get_import_task(str(task_id), request.user.id)
    if not t:
        return JsonResponse({"status": "pending", "percent": 0, "message": "\u4efb\u52a1\u521d\u59cb\u5316\u4e2d", "current": 0, "total": 0, "result": None, "error": None})
    return JsonResponse({
        "status": t.get("status"),
        "percent": t.get("percent", 0),
        "message": t.get("message", ""),
        "current": t.get("current", 0),
        "total": t.get("total", 0),
        "result": t.get("result"),
        "error": t.get("error"),
    })



@login_required
@require_POST
def inventory_import_start(request):
    messages.error(request, "导入功能暂不可用")
    return redirect("inventory_import")


@login_required
def inventory_import_progress(request, task_id):
    from django.http import JsonResponse
    return JsonResponse({"status": "error", "message": "导入功能暂不可用"})


@login_required
def inventory_export(request):
    return redirect("inventory_list")


@login_required
@require_POST
def inventory_delete_all(request):
    count = InventoryItem.objects.all().delete()[0]
    messages.success(request, f"已清空全�?{count} 条库存记�?)
    return redirect("inventory_list")


class InventoryDetailView(LoginRequiredMixin, DetailView):
    model = InventoryItem
    template_name = "order/inventory_detail.html"
    context_object_name = "item"


class InventoryUpdateView(LoginRequiredMixin, UpdateView):
    model = InventoryItem
    form_class = InventoryItemForm
    template_name = "order/inventory_form.html"
    success_url = "/inventory/"


@login_required
@require_POST
def inventory_bulk_action(request):
    return redirect("inventory_list")


class InventoryLogListView(LoginRequiredMixin, ListView):
    model = InventoryLog
    template_name = "order/inventory_log_list.html"
    context_object_name = "items"
    paginate_by = 50


class ClothCatalogListView(LoginRequiredMixin, ListView):

    model = ClothCatalog

    template_name = "order/cloth_catalog_list.html"

    context_object_name = "items"

    paginate_by = 50


    def get_paginate_by(self, queryset):
        try:
            return int(self.request.GET.get('per_page', 50))
        except (ValueError, TypeError):
            return 50



    def get_queryset(self):

        qs = ClothCatalog.objects.all()

        q = self.request.GET.get("q", "").strip()

        if q:

            from django.db.models import Q

            qs = qs.filter(

                Q(cloth_code__icontains=q) | Q(cloth_name__icontains=q) |

                Q(cloth_type__icontains=q) | Q(customer__icontains=q)

            )

        return qs



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        ctx["total"] = ClothCatalog.objects.count()
        ctx["q"] = self.request.GET.get("q", "")
        ctx["per_page_options"] = [20, 50, 100]
        ctx["per_page"] = self.request.GET.get("per_page", 50)
        try:
            ctx["per_page"] = int(ctx["per_page"])
        except ValueError:
            ctx["per_page"] = 50

        return ctx





@admin_required

def cloth_catalog_import(request):

    return render(request, "order/cloth_catalog_import.html")





@login_required

@require_POST
@require_POST
@validate_file_upload
def cloth_catalog_import_start(request):
    task_id = create_catalog_task(request.user.id)
    logger.info(f"创建布种目录导入任务 task_id={task_id} user_id={request.user.id}")
    start_catalog_task(task_id, request.upload_file_bytes)
    return JsonResponse({"task_id": task_id})
@admin_required
@login_required
def cloth_catalog_import_progress(request, task_id):
    try:
        from .import_progress import get_catalog_task
        from django.http import JsonResponse
        t = get_catalog_task(str(task_id), request.user.id)
        if not t:
            return JsonResponse({'status': 'pending', 'percent': 0, 'message': '任务初始化中', 'phase': 'pending', 'current': 0, 'total': 0, 'result': None, 'error': None})
        return JsonResponse({'status': t.get('status'), 'percent': t.get('percent', 0), 'message': t.get('message',''), 'phase': t.get('phase',''), 'current': t.get('current',0), 'total': t.get('total',0), 'result': t.get('result'), 'error': t.get('error')})
    except Exception as e:
        import logging
        logger.error(f"Catalog progress error: {e}", exc_info=True)
        from django.http import JsonResponse
        return JsonResponse({'status': 'error', 'percent': 0, 'message': str(e), 'phase': 'error', 'current': 0, 'total': 0, 'result': None, 'error': str(e)})
class ClothCatalogDetailView(LoginRequiredMixin, DetailView):

    model = ClothCatalog

    template_name = "order/cloth_catalog_detail.html"

    context_object_name = "item"

class ClothCatalogCreateView(LoginRequiredMixin, CreateView):

    model = ClothCatalog

    fields = ["cloth_code", "cloth_name", "cloth_type", "customer", "composition_cn", "composition_en",

              "width", "weight", "specification", "density", "process_cn", "process_en", "remark",

              "supplier1", "supplier1_code", "supplier2"]

    template_name = "order/cloth_catalog_form.html"

    success_url = "/cloth-catalog/"



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        ctx["form_title"] = "新增布种"

        return ctx





@login_required

@require_POST

def cloth_catalog_delete(request, pk):

    obj = get_object_or_404(ClothCatalog, pk=pk)

    obj.delete()

    messages.success(request, f"布种 {obj.cloth_code} 已删�?)

    return redirect("cloth_catalog_list")





@admin_required



@login_required
@require_POST
def cloth_catalog_delete_all(request):
    count = ClothCatalog.objects.all().delete()[0]
    messages.success(request, f'已删除全�?{count} 条布种编号记�?)
    return redirect('cloth_catalog_list')

def cloth_catalog_autocomplete(request):

    q = request.GET.get("q", "").strip()

    if len(q) < 1:

        return JsonResponse([], safe=False)

    from django.db.models import Q

    qs = ClothCatalog.objects.filter(

        Q(cloth_code__icontains=q) | Q(cloth_name__icontains=q)

    ).values("id", "cloth_code", "cloth_name", "cloth_type", "composition_cn", 

             "composition_en", "width", "weight", "specification", "density",

             "process_cn", "process_en", "customer", "supplier1", "supplier1_code", "supplier2")[:20]

    return JsonResponse(list(qs), safe=False)





@login_required

@require_POST

def order_update_progress(request, pk):

    order = get_object_or_404(ClothOrder, pk=pk)

    try:

        progress = int(request.POST.get("progress_current", -1))

        stages = json.loads(order.progress_stages) if order.progress_stages else []

        if 0 <= progress < len(stages):

            order.progress_current = progress

            order.save(update_fields=["progress_current"])

            return JsonResponse({"status": "ok", "stage": order.current_stage_name})

        return JsonResponse({"error": "无效进度"}, status=400)

    except (ValueError, TypeError, json.JSONDecodeError) as e:

        return JsonResponse({"error": str(e)}, status=400)





@admin_required

def order_edit_redirect(request, pk):

    return redirect(f"{reverse('order_detail', kwargs={'pk': pk})}?edit=1")





from .statement import generate_order_statement



@admin_required

def order_statement(request, pk):

    order = get_object_or_404(ClothOrder, pk=pk)

    output = generate_order_statement(order)

    ts = now().strftime("%Y%m%d_%H%M%S")

    filename = f"statement_{order.serial_number or 'order'}_{ts}.xlsx"

    response = HttpResponse(

        output.read(),

        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    )

    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    return response





@admin_required

def order_statement_bulk(request):

    ids_raw = request.GET.get("ids", "").strip()

    if not ids_raw:

        messages.error(request, "请提示供订单单ID")

        return redirect("order_list")

    try:

        pk_list = [int(x) for x in ids_raw.split(",") if x.strip()]

    except ValueError:

        messages.error(request, "订单单ID格式无效")

        return redirect("order_list")

    if not pk_list:

        messages.error(request, "未找到有效订�?)

        return redirect("order_list")

    orders = ClothOrder.objects.filter(pk__in=pk_list).order_by("-order_date", "-serial_number")

    if not orders.exists():

        messages.error(request, "未找到对应订�?)

        return redirect("order_list")

    from .statement import generate_bulk_statement

    output = generate_bulk_statement(list(orders))

    ts = now().strftime("%Y%m%d_%H%M%S")

    filename = f"statement_bulk_{ts}.xlsx"

    response = HttpResponse(

        output.read(),

        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    )

    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    return response











@admin_required

@require_POST

def order_toggle_supplier_paid(request, pk):

    order = get_object_or_404(ClothOrder, pk=pk)

    order.supplier_paid = not order.supplier_paid

    order.save(update_fields=["supplier_paid"])

    status = "已付�? if order.supplier_paid else "未付�?

    messages.success(request, f"供应商付款状态已更新为：{status}")

    return redirect("order_detail", pk=pk)







@admin_required

@require_POST



@admin_required

@require_POST





@admin_required

@require_POST



@admin_required

@require_POST

def order_toggle_status(request, pk):

    order = get_object_or_404(ClothOrder, pk=pk)

    if order.order_status == "active":

        order.order_status = "cancelled"

    else:

        order.order_status = "active"

    order.save(update_fields=["order_status"])

    status = "正常" if order.order_status == "active" else "已取�?


    messages.success(request, f"订单状态已更新为：{status}")

    return redirect("order_detail", pk=pk)





def order_toggle_payment_status(request, pk):

    order = get_object_or_404(ClothOrder, pk=pk)

    if order.payment_status == "paid":

        order.payment_status = "unpaid"

    else:

        order.payment_status = "paid"

    order.save(update_fields=["payment_status"])

    status = "已付�? if order.payment_status == "paid" else "未付�?

    messages.success(request, f"订单付款状态已更新为：{status}")

    return redirect("order_detail", pk=pk)





def order_refresh_calculations(request, pk):

    """重新计算订单单的总金额、成品出货对账等字段"""

    order = get_object_or_404(ClothOrder, pk=pk)

    qty = float(order.total_shipment_from_shipments() or 0)

    cost_price = float(order.finished_product_cost_price or 0)

    sell_price = float(order.price or 0)

    if qty > 0 and cost_price > 0:

        order.total_amount = order.computed_total_amount

        order.finished_product_total_amount = order.computed_finished_product_total_amount

        if not order.shipment_quantity_unit and order.quantity_unit:
            order.shipment_quantity_unit = order.quantity_unit
            update_fields = ["total_amount", "finished_product_total_amount", "shipment_quantity_unit"]
        else:
            update_fields = ["total_amount", "finished_product_total_amount"]

        if not order.supplier_shipped:

            order.supplier_shipped = True

            update_fields.append("supplier_shipped")

        order.save(update_fields=update_fields)

        messages.success(request, f"订单 #{order.serial_number} 已更�?)

    else:

        messages.warning(request, "缺少成本价格或出货数量，无法计算")

    return redirect("order_detail", pk=pk)





# =========================================================================

# 供应商账号管理（超管用）

# =========================================================================





class SupplierManageListView(AdminRequiredMixin, LoginRequiredMixin, ListView):

    model = Supplier

    template_name = "order/supplier_manage_list.html"

    context_object_name = "suppliers"

    paginate_by = 50



    def get_queryset(self):

        qs = Supplier.objects.all().select_related("user")

        q = self.request.GET.get("q", "").strip()

        if q:

            from django.db.models import Q

            qs = qs.filter(Q(company_name__icontains=q) | Q(contact_person__icontains=q) | Q(user__username__icontains=q))

        return qs



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        ctx["q"] = self.request.GET.get("q", "")

        ctx["total"] = Supplier.objects.count()

        ctx["active_count"] = Supplier.objects.filter(is_active=True).count()

        return ctx





class SupplierManageCreateView(AdminRequiredMixin, LoginRequiredMixin, CreateView):

    model = Supplier

    form_class = SupplierManageForm

    template_name = "order/supplier_manage_form.html"

    success_url = "/supplier/manage/"



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        ctx["form_title"] = "新建供应商账�?

        ctx["submit_label"] = "创建账号"

        return ctx



    def form_valid(self, form):

        messages.success(self.request, f"供应商账号已创建")

        return super().form_valid(form)





class SupplierManageUpdateView(AdminRequiredMixin, LoginRequiredMixin, UpdateView):

    model = Supplier

    form_class = SupplierManageForm

    template_name = "order/supplier_manage_form.html"

    success_url = "/supplier/manage/"



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        ctx["form_title"] = f"编辑供应商：{self.object.company_name}"

        ctx["submit_label"] = "保存修改"

        return ctx



    def form_valid(self, form):

        messages.success(self.request, f"供应商账号已保存")

        return super().form_valid(form)







@admin_required

def supplier_manage_delete(request, pk):

    supplier = get_object_or_404(Supplier, pk=pk)

    if request.method == "POST":

        company = supplier.company_name

        user = supplier.user

        if hasattr(user, "supplier_profile"):

            supplier.delete()

            user.delete()

            messages.success(request, f"供应�?{company} 已删�?)

        else:

            messages.error(request, "删除失败：账号异�?)

        return redirect("supplier_manage_list")

    return render(request, "order/supplier_manage_confirm_delete.html", {"supplier": supplier})





@admin_required

def supplier_manage_reset_password(request, pk):

    supplier = get_object_or_404(Supplier, pk=pk)

    if request.method == "POST":

        new_password = request.POST.get("new_password", "").strip()

        if not new_password:

            messages.error(request, "密码不能为空")

        else:

            supplier.user.set_password(new_password)

            supplier.user.save()

            messages.success(request, f"供应�?{supplier.company_name} 密码已重�?)

        return redirect("supplier_manage_list")

    return render(request, "order/supplier_manage_reset_password.html", {"supplier": supplier})



# =========================================================================

# 供应商面�?

# =========================================================================





class SupplierRequiredMixin(LoginRequiredMixin):

    """只允许供应商账号访问"""



    def dispatch(self, request, *args, **kwargs):

        if not request.user.is_authenticated:

            return redirect("home")

        if not hasattr(request.user, "supplier_profile"):

            messages.error(request, "无权访问供应商面�?)

            return redirect("dashboard")

        return super().dispatch(request, *args, **kwargs)





class SupplierDashboardView(SupplierRequiredMixin, ListView):

    """供应商主面板 —查看分类配给自己的订单�?""

    model = ClothOrder

    template_name = "order/supplier_dashboard.html"

    context_object_name = "orders"

    paginate_by = 20



    def get_queryset(self):

        supplier = self.request.user.supplier_profile

        qs = ClothOrder.objects.filter(supplier=supplier)

        # Filters
        params = self.request.GET
        search = params.get("search", "").strip()
        if search:
            from django.db.models import Q
            qs = qs.filter(
                Q(customer__icontains=search) |
                Q(cloth_type__icontains=search) |
                Q(serial_number__icontains=search)
            )
        status = params.get("status", "").strip()
        if status == "pending":
            qs = qs.filter(finished_product_cost_price__isnull=True)
        elif status == "quoted":
            qs = qs.filter(finished_product_cost_price__isnull=False, supplier_shipped=False, has_active_shipments=False)
        elif status == "shipped":
            qs = qs.filter(Q(supplier_shipped=True) | Q(has_active_shipments=True))
        start_date = params.get("start_date", "").strip()
        if start_date:
            qs = qs.filter(order_date__gte=start_date)
        end_date = params.get("end_date", "").strip()
        if end_date:
            qs = qs.filter(order_date__lte=end_date)

        qs = qs.order_by("-order_date", "-serial_number")
        from django.db.models import Subquery, OuterRef, Sum, FloatField, Exists
        from django.db.models import Prefetch
        from .models import Shipment
        subq = Shipment.objects.filter(
            order=OuterRef('pk'), is_deleted=False
        ).values('order').annotate(total=Sum('quantity')).values('total')
        has_ship = Shipment.objects.filter(order=OuterRef('pk'), is_deleted=False)
        from django.db.models import ExpressionWrapper, F, FloatField
        qs = qs.prefetch_related(Prefetch('shipments', queryset=Shipment.objects.annotate(
            row_total=ExpressionWrapper(F('quantity') * F('order__finished_product_cost_price'), output_field=FloatField())
        ).order_by('batch_number')))
        return qs.annotate(
            _shipment_qty=Subquery(subq, output_field=FloatField()),
            has_active_shipments=Exists(has_ship)
        )



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        supplier = self.request.user.supplier_profile

        qs = self.get_queryset()

        

        ctx["supplier"] = supplier

        

        ctx["total_count"] = qs.count()

        ctx["quoted_count"] = qs.filter(finished_product_cost_price__isnull=False).count()

        ctx["pending_count"] = qs.filter(finished_product_cost_price__isnull=True).count()

        ctx["quoted_wait_ship_count"] = qs.filter(finished_product_cost_price__isnull=False, supplier_shipped=False, has_active_shipments=False).count()

        ctx["shipped_count"] = qs.filter(Q(supplier_shipped=True) | Q(has_active_shipments=True)).count()

        

        totals = {}

        for o in qs:

            price_val = float(o.finished_product_cost_price or 0)

            num_val = float(getattr(o, '_shipment_qty') or 0)

            totals[str(o.pk)] = price_val * num_val

        ctx["order_totals"] = totals
        ctx["filter_params"] = self.request.GET
        ctx["per_page_options"] = (10, 20, 50, 100)
        ctx["per_page"] = self.paginate_by



        return ctx





class SupplierOrderDetailView(SupplierRequiredMixin, DetailView):

    """供应商查看订单详情，填写价格、确认出�?""

    model = ClothOrder

    template_name = "order/supplier_order_detail.html"

    context_object_name = "order"



    def get_queryset(self):

        supplier = self.request.user.supplier_profile

        return ClothOrder.objects.filter(supplier=supplier)



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        order = self.object

        from .forms import ORDER_FORM_SECTIONS

        ctx["form_sections"] = ORDER_FORM_SECTIONS

        ctx["price_form"] = SupplierPriceForm(instance=order)
        from .forms import ShipmentForm
        ctx["shipment_form"] = ShipmentForm()
        shipments_qs = order.shipments.all().order_by("batch_number")
        from django.db.models import F, Value, FloatField, ExpressionWrapper
        from django.db.models.functions import Coalesce
        cost_price = Coalesce("order__finished_product_cost_price", Value(0))
        qty_field = Coalesce("quantity", Value(0))
        ctx["shipments"] = shipments_qs.annotate(
            shipment_total=ExpressionWrapper(qty_field * cost_price, output_field=FloatField())
        )
        ctx["shipment_total_sum"] = order.shipments.filter(is_deleted=False).aggregate(
            total=Sum(F("quantity") * F("order__finished_product_cost_price"))
        )["total"] or 0

        ctx["supplier"] = self.request.user.supplier_profile



        return ctx



    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        order = self.object

        form = SupplierPriceForm(request.POST, instance=order)
        if form.is_valid():
            order = form.save()

            # 创建出货记录（与价格表单一起提交）
            supplier_name = request.user.supplier_profile.company_name
            ship_date = request.POST.get("shipment_date", "").strip()
            ship_qty = request.POST.get("shipment_quantity", "").strip()
            if ship_date and ship_qty:
                try:
                    from .models import Notification, notify_user, notify_all_staff, Shipment
                    from django.utils.dateparse import parse_date
                    parsed_date = parse_date(ship_date)
                    qty = float(ship_qty)
                    max_batch = order.shipments.aggregate(m=models.Max("batch_number"))["m"] or 0
                    Shipment.objects.create(
                        order=order,
                        batch_number=max_batch + 1,
                        date=parsed_date,
                        quantity=qty,
                        created_by=request.user if request.user.is_authenticated else None,
                    )
                    from django.urls import reverse as _reverse
                    from .models import notify_all_staff as _notify
                    _notify(
                        title="供应商已出货",
                        message="供应�?" + supplier_name + " 已对订单 #" + str(order.serial_number) + " 出货 " + str(qty) + " " + str(order.quantity_unit or "") + "，请前往查看�?,
                        link=_reverse("order_detail", kwargs={"pk": order.pk}),
                    )
                except (ValueError, TypeError):
                    pass

            # 同步订单总数量和总金额（总是执行�?
            total_qty = float(order.total_shipment_from_shipments() or 0)
            order.total_shipment_quantity = total_qty
            cost = float(order.finished_product_cost_price or 0)
            order.total_amount = order.computed_total_amount
            price = float(order.price or 0)
            order.finished_product_total_amount = order.computed_finished_product_total_amount
            order.supplier_shipped = order.shipments.filter(is_deleted=False).exists()
            # 同步出货数量单位
            if not order.shipment_quantity_unit and order.quantity_unit:
                order.shipment_quantity_unit = order.quantity_unit
            order.save(update_fields=[
                "total_shipment_quantity", "total_amount",
                "finished_product_total_amount", "supplier_shipped",
                "shipment_quantity_unit",
            ])

            from django.urls import reverse
            from .models import notify_all_staff
            logger.info(f"[供应商报价] {supplier_name} 对订�?#{order.serial_number} 提交报价 {order.finished_product_cost_price}{order.cost_price_unit}")
            notify_all_staff(
                title="供应商已报价",
                message="供应�?" + supplier_name + " 已对订单 #" + str(order.serial_number) + " 提交报价，请前往处理�?,
                link=reverse("order_detail", kwargs={"pk": order.pk}),
            )

            messages.success(request, f"订单 #{order.serial_number} 已更�?)
            return redirect("supplier_order_detail", pk=order.pk)

        ctx = self.get_context_data(object=order)
        ctx["price_form"] = form
        return self.render_to_response(ctx)






@login_required

@login_required
@require_POST
def order_shipment_create(request, pk):
    """为订单新增出货记录（供应�?管理员均可使用）"""
    order = get_object_or_404(ClothOrder, pk=pk)
    from .forms import ShipmentForm
    form = ShipmentForm(request.POST)
    if form.is_valid():
        shipment = form.save(commit=False)
        shipment.order = order
        # Auto-assign batch number
        max_batch = order.shipments.aggregate(m=models.Max("batch_number"))["m"] or 0
        shipment.batch_number = max_batch + 1
        if request.user.is_authenticated:
            shipment.created_by = request.user
        shipment.save()
        order.supplier_shipped = True
        order.save(update_fields=["supplier_shipped"])
        logger.info(f"[新增出货] {request.user.username} 为订�?#{order.serial_number} 新增批次 {shipment.batch_number}，数�?{shipment.quantity}")
        messages.success(request, f"出货记录已添加（批次 {shipment.batch_number}�?)
    else:
        for field, errors in form.errors.items():
            for err in errors:
                messages.error(request, f"{field}: {err}")
    return redirect(request.META.get("HTTP_REFERER", reverse("supplier_order_detail", kwargs={"pk": pk})))

@login_required
@require_POST
def order_shipment_delete(request, pk, shipment_pk):
    """删除出货记录"""
    shipment = get_object_or_404(Shipment, pk=shipment_pk, order_id=pk)
    order = shipment.order
    batch = shipment.batch_number
    logger.info(f"[删除出货] {request.user.username} 将订�?#{order.serial_number} 批次 {batch} 标记为已删除")
    shipment.is_deleted = True
    shipment.save(update_fields=["is_deleted"])
    # Update supplier_shipped flag
    if not order.shipments.filter(is_deleted=False).exists():
        order.supplier_shipped = False
        order.save(update_fields=["supplier_shipped"])
    messages.success(request, f"出货记录已删除（批次 {batch}�?)
    return redirect(request.META.get("HTTP_REFERER", reverse("supplier_order_detail", kwargs={"pk": pk})))


@login_required
def order_shipment_edit(request, pk, shipment_pk):
    """编辑出货记录（GET返回JSON，POST保存�?""
    shipment = get_object_or_404(Shipment, pk=shipment_pk, order_id=pk)
    order = shipment.order
    from .forms import ShipmentForm
    
    if request.method == "POST":
        form = ShipmentForm(request.POST, instance=shipment)
        if form.is_valid():
            form.save()
            messages.success(request, f"出货记录已更新（批次 {shipment.batch_number}�?)
        else:
            for field, errors in form.errors.items():
                for err in errors:
                    messages.error(request, f"{field}: {err}")
        return redirect(request.META.get("HTTP_REFERER", reverse("supplier_order_detail", kwargs={"pk": pk})))
    
    # GET: return JSON for inline editing
    from django.http import JsonResponse
    return JsonResponse({
        "date": shipment.date.isoformat() if shipment.date else "",
        "quantity": float(shipment.quantity) if shipment.quantity else 0,
        "batch_number": shipment.batch_number,
    })


def supplier_logout_view(request):

    logout(request)

    return redirect("home")








@login_required
def order_list_updated_at(request):
    from .models import ClothOrder
    from django.http import JsonResponse
    latest = ClothOrder.objects.order_by("-updated_at").values("updated_at").first()
    ts = latest["updated_at"].isoformat() if latest and latest["updated_at"] else ""
    return JsonResponse({"updated_at": ts})


@login_required
def notification_list(request):
    notifications = request.user.notifications.all()
    page = request.GET.get("page", 1)
    from django.core.paginator import Paginator
    paginator = Paginator(notifications, 20)
    page_obj = paginator.get_page(page)
    return render(request, "order/notification_list.html", {
        "page_obj": page_obj,
        "unread_count": notifications.filter(is_read=False).count(),
    })


@login_required
def notification_unread_count(request):
    count = request.user.notifications.filter(is_read=False).count()
    from django.http import JsonResponse
    return JsonResponse({"count": count})


@login_required
@require_POST
def notification_mark_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.is_read = True
    notification.save(update_fields=["is_read"])
    return JsonResponse({"ok": True})


@login_required
@require_POST
def notification_mark_all_read(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return JsonResponse({"ok": True})


@login_required
def notification_unread_list(request):
    qs = request.user.notifications.filter(is_read=False)[:5]
    data = []
    for n in qs:
        data.append({
            "id": n.pk,
            "title": n.title,
            "message": n.message,
            "link": n.link,
            "created_at": n.created_at.strftime("%m-%d %H:%M"),
        })
    from django.http import JsonResponse
    return JsonResponse({"notifications": data})



from order.models import FabricQuotation

@require_GET
def quotation_lookup(request):
    """根据面料编号查询最新报�?""
    article_no = request.GET.get("q", "").strip()
    if not article_no:
        return JsonResponse({"error": "Need q parameter"}, status=400)

    qs = FabricQuotation.objects.filter(article_no__icontains=article_no).order_by("-date_sent")
    if not qs.exists():
        return JsonResponse({"found": False})

    q = qs.first()
    data = {
        "found": True,
        "id": q.id,
        "supplier_name": q.supplier_name,
        "article_no": q.article_no,
        "composition": q.composition,
        "weight": q.weight,
        "cuttable_width": q.cuttable_width,
        "price_200m_text": q.price_200m_text,
        "price_200m": str(q.price_200m) if q.price_200m else None,
        "price_200m_print": str(q.price_200m_print) if q.price_200m_print else None,
        "price_200m_unit": q.price_200m_unit,
        "regular_mcq": q.regular_mcq,
        "price_regular_text": q.price_regular_text,
        "price_regular": str(q.price_regular) if q.price_regular else None,
        "price_regular_print": str(q.price_regular_print) if q.price_regular_print else None,
        "price_regular_unit": q.price_regular_unit,
        "lead_time": q.lead_time,
        "remark": q.remark,
        "preferred_material": q.preferred_material,
        "quoted_to": q.quoted_to,
        "date_sent": str(q.date_sent) if q.date_sent else None,
    }
    return JsonResponse(data)




class QuotationListView(LoginRequiredMixin, ListView):
    model = FabricQuotation
    template_name = "order/quotation_list.html"
    context_object_name = "items"
    paginate_by = 50

    def get_queryset(self):
        qs = FabricQuotation.objects.all()
        q = self.request.GET.get("q", "").strip()
        if q:
            from django.db.models import Q
            qs = qs.filter(
                Q(article_no__icontains=q) |
                Q(composition__icontains=q) |
                Q(supplier_name__icontains=q)
            )
        return qs

    def get_paginate_by(self, queryset):
        try:
            return int(self.request.GET.get('per_page', 50))
        except (ValueError, TypeError):
            return 50

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["total"] = FabricQuotation.objects.count()
        ctx["per_page_options"] = [20, 50, 100]
        ctx["per_page"] = self.request.GET.get("per_page", 50)
        try:
            ctx["per_page"] = int(ctx["per_page"])
        except ValueError:
            ctx["per_page"] = 50
        return ctx


class QuotationDetailView(LoginRequiredMixin, DetailView):
    model = FabricQuotation
    template_name = "order/quotation_detail.html"
    context_object_name = "item"


class QuotationCreateView(LoginRequiredMixin, CreateView):
    model = FabricQuotation
    fields = [
        "article_no", "supplier_name", "supplier_contact", "date_sent",
        "gfg_dev_no", "preferred_material", "color_card",
        "composition", "weight", "cuttable_width", "yarn_count", "density_gauge",
        "lead_time", "price_validity",
        "price_200m_text", "price_200m", "price_200m_print", "price_200m_unit",
        "regular_mcq", "price_regular_text", "price_regular", "price_regular_print", "price_regular_unit",
        "remark", "quoted_to", "extra_remark",
    ]
    template_name = "order/quotation_form.html"
    success_url = "/quotation/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "新增面料报价"
        return ctx


@login_required
@require_POST
def quotation_delete(request, pk):
    obj = get_object_or_404(FabricQuotation, pk=pk)
    article = obj.article_no
    obj.delete()
    messages.success(request, f"面料报价 {article} 已删�?)
    return redirect("quotation_list")


@login_required
def quotation_import(request):
    import openpyxl, re
    from datetime import datetime
    if request.method == "POST":
        excel_file = request.FILES.get("excel_file")
        if not excel_file:
            messages.error(request, "请选择文件")
            return redirect("quotation_import")
        try:
            wb = openpyxl.load_workbook(excel_file, data_only=True)
            if "Summary" not in wb.sheetnames:
                messages.error(request, "Excel 文件中找不到 'Summary' 工作�?)
                return redirect("quotation_import")
            ws = wb["Summary"]
        except Exception as e:
            messages.error(request, f"无法读取 Excel 文件：{e}")
            return redirect("quotation_import")

        def parse_price(text):
            if not text or not isinstance(text, str):
                return None, None, "M"
            text = text.strip()
            if not text or text == "N/A":
                return None, None, "M"
            t = re.sub(r"^(RMB|RMB)\s*", "", text, flags=re.IGNORECASE).strip()
            unit = "KG" if "/kg" in t.lower() else "M"
            solid_val = None
            print_val = None
            parts = re.split(r"[,，]", t)
            for part in parts:
                part = part.strip()
                part = re.sub(r"^(RMB|RMB)\s*", "", part, flags=re.IGNORECASE).strip()
                is_print = bool(re.search(r"for\s+print", part, re.IGNORECASE))
                is_solid = bool(re.search(r"for\s+solid|for\s+soild", part, re.IGNORECASE)) or not is_print
                m = re.search(r"(\d+(?:\.\d+)?)", part)
                val = float(m.group(1)) if m else None
                if is_print and val is not None:
                    print_val = val
                if is_solid and val is not None:
                    solid_val = val
            if solid_val is None and print_val is None:
                m = re.search(r"(\d+(?:\.\d+)?)", t)
                if m:
                    solid_val = float(m.group(1))
            return solid_val, print_val, unit

        batch = []
        imported = 0
        skipped = 0
        from order.models import FabricQuotation
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True), 2):
            if row_idx == 3:
                continue
            article_no = str(row[7]).strip() if row[7] else ""
            if not article_no:
                skipped += 1
                continue
            date_sent = None
            if isinstance(row[1], datetime):
                date_sent = row[1].date()
            price_200m_text = str(row[11]).strip() if row[11] else ""
            p200m, p200m_print, p200m_unit = parse_price(price_200m_text)
            price_regular_text = str(row[13]).strip() if row[13] else ""
            preg, preg_print, preg_unit = parse_price(price_regular_text)
            extra = ""
            for ci in [20, 21, 22, 23, 24, 25, 26, 27, 28, 29]:
                if ci < len(row) and row[ci] is not None:
                    v = str(row[ci]).strip()
                    if v:
                        extra += v + " "
            extra = extra.strip()
            batch.append(FabricQuotation(
                row_index=int(row[0]) if isinstance(row[0], (int, float)) else None,
                date_sent=date_sent,
                supplier_name=str(row[2]).strip() if row[2] else "",
                supplier_contact=str(row[3]).strip() if row[3] else "",
                gfg_dev_no=str(row[4]).strip() if row[4] else "",
                preferred_material=str(row[5]).strip() if row[5] else "",
                color_card=str(row[6]).strip() if row[6] else "",
                article_no=article_no,
                composition=str(row[8]).strip() if row[8] else "",
                weight=str(row[9]).strip() if row[9] else "",
                cuttable_width=str(row[10]).strip() if row[10] else "",
                price_200m_text=price_200m_text,
                price_200m=p200m,
                price_200m_print=p200m_print,
                price_200m_unit=p200m_unit,
                regular_mcq=str(row[12]).strip() if row[12] else "",
                price_regular_text=price_regular_text,
                price_regular=preg,
                price_regular_print=preg_print,
                price_regular_unit=preg_unit,
                lead_time=str(row[14]).strip() if row[14] else "",
                price_validity=str(row[15]).strip() if row[15] else "",
                yarn_count=str(row[16]).strip() if row[16] else "",
                density_gauge=str(row[17]).strip() if row[17] else "",
                remark=str(row[18]).strip() if row[18] else "",
                quoted_to=str(row[19]).strip() if len(row) > 19 and row[19] else "",
                extra_remark=extra,
            ))
            imported += 1

        FabricQuotation.objects.all().delete()
        FabricQuotation.objects.bulk_create(batch)
        messages.success(request, f"导入完成！共导入 {imported} 条报价记录，跳过 {skipped} 行空数据")
        return redirect("quotation_list")

    return render(request, "order/quotation_import.html")


@login_required
@require_POST
def quotation_delete_all(request):
    from order.models import FabricQuotation
    count = FabricQuotation.objects.all().delete()[0]
    messages.success(request, f"已删除全�?{count} 条面料报价记�?)
    return redirect("quotation_list")

