import json
import logging
import re
from datetime import date, datetime
from functools import wraps
from io import BytesIO

import pandas as pd
from django.contrib import messages
from django.core.cache import cache
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db import transaction
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
from django.db.models.functions import Coalesce, TruncMonth
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.timezone import now
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .dashboard_stats import (
    build_current_month_finance,
    build_current_month_orders,
    build_month_compare,
    build_monthly_chart_data,
)
from .decorators import admin_required, validate_file_upload
# 会话超时功能已注释停用，后续需要时恢复本行
# from .middleware import mark_session_activity
from .captcha import clear_captcha, render_captcha_svg, set_captcha, verify_captcha
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
from .order_excel import export_orders_dataframe
from .order_filters import filter_orders_queryset
from .statement import generate_bulk_statement, generate_order_statement

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------


class AdminRequiredMixin:
    """管理员权限 Mixin：供应商用户会被重定向到供应商仪表盘。"""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and hasattr(request.user, "supplier_profile") and not request.user.is_staff:
            messages.warning(request, t("auth.no_access"))
            return redirect("supplier_dashboard")
        return super().dispatch(request, *args, **kwargs)


class SupplierRequiredMixin(LoginRequiredMixin):
    """供应商权限 Mixin：只有供应商用户能访问。"""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not hasattr(request.user, "supplier_profile"):
            messages.error(request, t("auth.supplier_no_access"))
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------


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


@require_GET
def captcha_image(request):
    code = set_captcha(request)
    response = HttpResponse(
        render_captcha_svg(code),
        content_type="image/svg+xml; charset=utf-8",
    )
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    return response


@login_required
def logout_view(request):
    logger.info(t("log.logout", username=request.user.username))
    logout(request)
    return redirect("home")


# ---------------------------------------------------------------------------
# 仪表盘
# ---------------------------------------------------------------------------


@admin_required
@login_required
def dashboard_view(request):
    # ===== Financial totals: single Shipment join query (replaces N+1 Python loop) =====
    ship_base = Shipment.objects.filter(is_deleted=False, order__order_status="active")
    fin = ship_base.aggregate(
        total_revenue=Coalesce(
            Sum(F("quantity") * F("order__price")),
            Value(0.0), output_field=FloatField()
        ),
        total_cost=Coalesce(
            Sum(F("quantity") * F("order__finished_product_cost_price")),
            Value(0.0), output_field=FloatField()
        ),
    )
    total_revenue = round(float(fin["total_revenue"]), 2)
    total_cost = round(float(fin["total_cost"]), 2)
    total_profit = total_revenue - total_cost
    profit_margin = (total_profit / total_revenue * 100) if total_revenue else 0

    # ===== Aggregated counts (2 queries instead of 9) =====
    cnt = ClothOrder.objects.aggregate(
        total=Count("id"),
        cancelled=Count("id", filter=Q(order_status="cancelled")),
    )
    total_orders = cnt["total"]
    cancelled_orders = cnt["cancelled"]

    active_qs = ClothOrder.objects.filter(order_status="active")
    active_cnt = active_qs.aggregate(
        active_total=Count("id"),
        paid=Count("id", filter=Q(payment_status="paid")),
        unpaid=Count("id", filter=Q(payment_status="unpaid")),
        overdue=Count("id", filter=Q(overdue_status="overdue")),
        paid_no=Count("id", filter=Q(paid_amount="no")),
    )

    distinct_cnt = ClothOrder.objects.aggregate(
        total_customers=Count("customer", distinct=True, filter=~Q(customer="")),
        total_suppliers=Count("finished_product_supplier", distinct=True, filter=~Q(finished_product_supplier="")),
    )

    # ===== Top customers =====
    top_customers = list(
        active_qs.exclude(customer="")
        .values("customer")
        .annotate(order_count=Count("id"), total_amount=Sum("finished_product_total_amount"))
        .order_by("-total_amount")[:5]
    )
    for row in top_customers:
        amount = row["total_amount"] or 0
        count = row["order_count"] or 0
        row["avg_amount"] = amount / count if count else 0

    # ===== Order type stats =====
    order_type_stats = list(
        active_qs.exclude(order_type="")
        .values("order_type")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    type_labels = dict(ClothOrder.ORDER_TYPE_CHOICES)
    for row in order_type_stats:
        row["label"] = type_labels.get(row["order_type"], row["order_type"] or t("label.unclassified"))

    # ===== Monthly charts =====
    today = date.today()
    month_param = (request.GET.get("month") or "").strip()

    # 只列出有订单数据的月份
    month_rows = (
        active_qs.filter(order_date__isnull=False)
        .annotate(month_key=TruncMonth("order_date"))
        .values("month_key")
        .annotate(order_count=Count("id"))
        .order_by("-month_key")
    )
    month_options = []
    for row in month_rows:
        month_key = row["month_key"]
        if month_key:
            month_options.append({
                "value": month_key.strftime("%Y-%m"),
                "label": f"{month_key.year}年{month_key.month}月",
            })
    if not month_options:
        month_options.append({
            "value": today.strftime("%Y-%m"),
            "label": f"{today.year}年{today.month}月",
        })

    selected_value = month_options[0]["value"]
    month_match = re.match(r"^(\d{4})-(\d{2})$", month_param)
    if month_match:
        try:
            year = int(month_match.group(1))
            month = int(month_match.group(2))
            date(year, month, 1)
            candidate = f"{year:04d}-{month:02d}"
            if any(opt["value"] == candidate for opt in month_options):
                selected_value = candidate
        except ValueError:
            pass
    chart_year, chart_month = int(selected_value[:4]), int(selected_value[5:7])

    monthly_chart = build_monthly_chart_data(active_qs, months_count=12)
    month_chart_data = {}
    for opt in month_options:
        year = int(opt["value"][:4])
        month = int(opt["value"][5:7])
        month_chart_data[opt["value"]] = {
            "orders": build_current_month_orders(active_qs, year, month),
            "finance": build_current_month_finance(active_qs, year, month),
        }
    current_month_orders = month_chart_data[selected_value]["orders"]
    current_month_finance = month_chart_data[selected_value]["finance"]
    month_compare = build_month_compare(active_qs)

    # ===== TOP suppliers via Shipment join (single query, no Python loop) =====
    _top_suppliers = list(
        ship_base.exclude(order__finished_product_supplier="")
        .values("order__finished_product_supplier")
        .annotate(
            order_count=Count("order_id", distinct=True),
            total_amount=Coalesce(Sum(F("quantity") * F("order__finished_product_cost_price")), Value(0.0), output_field=FloatField()),
        )
        .order_by("-total_amount")[:5]
    )
    for s in _top_suppliers:
        s["finished_product_supplier"] = s.pop("order__finished_product_supplier")

    # ===== Recent orders & alerts =====
    all_orders = ClothOrder.objects.all()
    ship_subq = (
        Shipment.objects.filter(order=OuterRef("pk"), is_deleted=False)
        .values("order")
        .annotate(total=Sum("quantity"))
        .values("total")[:1]
    )
    recent_orders = (
        all_orders.annotate(
            _ship_qty=Coalesce(Subquery(ship_subq, output_field=FloatField()), Value(0.0), output_field=FloatField())
        )
        .order_by("-order_date", "-created_at")[:8]
    )
    ship_qty_annotation = Coalesce(Subquery(ship_subq, output_field=FloatField()), Value(0.0), output_field=FloatField())
    alert_unpaid = (
        active_qs.annotate(_ship_qty=ship_qty_annotation)
        .filter(payment_status="unpaid")
        .order_by("-order_date")[:5]
    )
    alert_supplier_unpaid = (
        active_qs.annotate(_ship_qty=ship_qty_annotation)
        .filter(supplier_paid=False)
        .order_by("-order_date")[:5]
    )

    context = {
        "total_orders": total_orders,
        "cancelled_orders": cancelled_orders,
        "active_orders_count": active_cnt["active_total"],
        "total_customers": distinct_cnt["total_customers"],
        "total_suppliers": distinct_cnt["total_suppliers"],
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "profit_margin": profit_margin,
        "paid_orders": active_cnt["paid"],
        "unpaid_orders": active_cnt["unpaid"],
        "overdue_orders": active_cnt["overdue"],
        "paid_amount_no": active_cnt["paid_no"],
        "recent_orders": recent_orders,
        "alert_unpaid": alert_unpaid,
        "alert_supplier_unpaid": alert_supplier_unpaid,
        "top_customers": top_customers,
        "top_suppliers": _top_suppliers,
        "order_type_stats": order_type_stats,
        "monthly_chart": monthly_chart,
        "monthly_rows": monthly_chart["rows"],
        "current_month_orders": current_month_orders,
        "current_month_finance": current_month_finance,
        "month_chart_data": month_chart_data,
        "month_options": month_options,
        "selected_month": selected_value,
        "month_compare": month_compare,
    }
    return render(request, "order/dashboard.html", context)


# ---------------------------------------------------------------------------
# 订单列表 / 详情 / 创建
# ---------------------------------------------------------------------------


class OrderListView(LoginRequiredMixin, ListView):
    model = ClothOrder
    template_name = "order/order_list.html"
    context_object_name = "orders"
    paginate_by = 20
    per_page_options = (10, 20, 50, 100)
    SORTABLE_FIELDS = {
        "serial": "_sort_serial",
        "date": "_sort_date",
        "qty": "_sort_qty",
        "price": "_sort_price",
        "balance": "_sort_balance_amount",
        "cost": "_sort_total_cost",
    }

    def get_paginate_by(self, queryset):
        try:
            return int(self.request.GET.get("per_page", 50))
        except (ValueError, TypeError):
            return 50

    def get_queryset(self):
        qs = ClothOrder.objects.all()
        qs = filter_orders_queryset(self.request.GET, qs)
        # Annotate shipment qty so computed_* properties avoid N+1 queries
        ship_subq = Shipment.objects.filter(
            order=OuterRef("pk"), is_deleted=False
        ).values("order").annotate(
            total=Sum("quantity")
        ).values("total")[:1]
        qs = qs.annotate(
            _ship_qty=Coalesce(Subquery(ship_subq, output_field=FloatField()), Value(0.0), output_field=FloatField())
        ).annotate(
            _sort_serial=Coalesce(F("serial_number"), Value(0), output_field=IntegerField()),
            _sort_date=Coalesce(F("order_date"), Value(date(1970, 1, 1)), output_field=DateField()),
            _sort_qty=Coalesce(F("order_quantity"), Value(0), output_field=DecimalField(max_digits=15, decimal_places=2)),
            _sort_price=Coalesce(F("price"), Value(0), output_field=DecimalField(max_digits=15, decimal_places=2)),
            _sort_balance_amount=ExpressionWrapper(
                Coalesce(F("price"), Value(0)) * F("_ship_qty"),
                output_field=FloatField(),
            ),
            _sort_total_cost=ExpressionWrapper(
                Coalesce(F("finished_product_cost_price"), Value(0)) * F("_ship_qty"),
                output_field=FloatField(),
            ),
        )
        # 表头排序：白名单字段，升/降序
        sort_key = self.request.GET.get("sort", "")
        direction = str(self.request.GET.get("dir", "desc")).lower()
        if sort_key in self.SORTABLE_FIELDS:
            field = self.SORTABLE_FIELDS[sort_key]
            if direction == "asc":
                qs = qs.order_by(field, "-order_date", "-serial_number")
            else:
                qs = qs.order_by("-" + field, "-order_date", "-serial_number")
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["per_page_options"] = self.per_page_options
        ctx["per_page"] = self.request.GET.get("per_page", self.paginate_by)
        try:
            ctx["per_page"] = int(ctx["per_page"])
        except ValueError:
            ctx["per_page"] = self.paginate_by

        # 筛选后的统计数据（用 Shipment JOIN 自带的字段）
        filtered = self.get_queryset()
        active_ids = list(filtered.filter(order_status="active").values_list("pk", flat=True))
        if active_ids:
            from .models import Shipment as _Ship
            fin = _Ship.objects.filter(is_deleted=False, order_id__in=active_ids).aggregate(
                rev=Coalesce(Sum(F("quantity") * F("order__price")), Value(0.0), output_field=FloatField()),
                cost=Coalesce(Sum(F("quantity") * F("order__finished_product_cost_price")), Value(0.0), output_field=FloatField()),
            )
            total_revenue = round(float(fin["rev"]), 2)
            total_cost = round(float(fin["cost"]), 2)
        else:
            total_revenue = 0.0
            total_cost = 0.0
        total_profit = total_revenue - total_cost
        profit_margin = (total_profit / total_revenue * 100) if total_revenue else 0

        ctx["total_count"] = len(active_ids)
        ctx["total_revenue"] = total_revenue
        ctx["total_cost"] = total_cost
        ctx["total_profit"] = total_profit
        ctx["filter_profit_margin"] = profit_margin

        # Chip 计数（全量）—— 合并为一次聚合
        chip = ClothOrder.objects.aggregate(
            unpaid=Count("id", filter=Q(payment_status="unpaid")),
            overdue=Count("id", filter=Q(overdue_status="overdue")),
            paid=Count("id", filter=Q(payment_status="paid")),
            cancelled=Count("id", filter=Q(order_status="cancelled")),
        )
        ctx["chip_unpaid"] = chip["unpaid"]
        ctx["chip_overdue"] = chip["overdue"]
        ctx["chip_paid"] = chip["paid"]
        ctx["chip_cancelled"] = chip["cancelled"]

        # 是否有激活的筛选条件
        params = self.request.GET
        ctx["active_filters"] = any(
            k in params
            for k in [
                "search",
                "serial",
                "customer",
                "cloth",
                "payment_status",
                "overdue_status",
                "order_type",
                "supplier",
                "supplier_paid",
                "order_status",
                "start_date",
                "end_date",
            ]
        )

        # 筛选面板是否自动展开（仅搜索关键词时保持折叠）
        ctx["filter_panel_open"] = any(
            k in params
            for k in [
                "order_type",
                "payment_status",
                "overdue_status",
                "supplier_paid",
                "order_status",
                "start_date",
                "end_date",
                "sort",
                "dir",
            ]
        )

        # 当前筛选内容（显示在列表表头上方）
        def _choice_label(choices, value):
            for v, label in choices:
                if v == value:
                    return label
            return value

        badges = []
        if search := params.get("search", "").strip():
            badges.append(("搜索", search))
        if serial := params.get("serial", "").strip():
            badges.append(("序号", serial))
        if customer := params.get("customer", "").strip():
            badges.append(("客户/跟单员", customer))
        if cloth := params.get("cloth", "").strip():
            badges.append(("布种/颜色", cloth))
        if supplier := params.get("supplier", "").strip():
            badges.append(("供应商/档口", supplier))
        if order_type := params.get("order_type", "").strip():
            badges.append(("类型", _choice_label(ClothOrder.ORDER_TYPE_CHOICES, order_type)))
        if payment := params.get("payment_status", "").strip():
            badges.append(("客户付款", _choice_label(ClothOrder.PAYMENT_STATUS_CHOICES, payment)))
        if overdue := params.get("overdue_status", "").strip():
            badges.append(("逾期", _choice_label(ClothOrder.OVERDUE_STATUS_CHOICES, overdue)))
        if paid := params.get("supplier_paid", "").strip():
            paid_label = "已付款" if paid in ("True", "true", "yes", "1") else (
                "未付款" if paid in ("False", "false", "no", "0") else paid
            )
            badges.append(("供应商付款", paid_label))
        if status := params.get("order_status", "").strip():
            badges.append(("订单状态", _choice_label(ClothOrder.ORDER_STATUS_CHOICES, status)))
        if start := params.get("start_date", "").strip():
            end = params.get("end_date", "").strip()
            badges.append(("下单日期", f"{start} ~ {end or '今'}"))
        elif end := params.get("end_date", "").strip():
            badges.append(("下单日期", f"起 ~ {end}"))
        if sort_key := params.get("sort", "").strip():
            sort_names = {
                "serial": "序号",
                "date": "下单日期",
                "qty": "数量",
                "price": "价格",
                "balance": "对账金额",
                "cost": "总成本",
            }
            dir_label = "升序" if params.get("dir", "desc") == "asc" else "降序"
            badges.append(("排序", f"{sort_names.get(sort_key, sort_key)} {dir_label}"))
        ctx["active_filter_badges"] = badges

        # 表头筛选下拉的选项
        ctx["order_status_choices"] = ClothOrder.ORDER_STATUS_CHOICES
        ctx["order_type_choices"] = ClothOrder.ORDER_TYPE_CHOICES
        ctx["payment_status_choices"] = ClothOrder.PAYMENT_STATUS_CHOICES
        ctx["overdue_status_choices"] = ClothOrder.OVERDUE_STATUS_CHOICES
        ctx["supplier_paid_choices"] = [
            ("True", "已付款"),
            ("False", "未付款"),
        ]
        ctx["sort_options"] = [
            ("serial", "asc", "序号 升序"),
            ("serial", "desc", "序号 降序"),
            ("date", "asc", "下单日期 升序"),
            ("date", "desc", "下单日期 降序"),
            ("qty", "asc", "数量 升序"),
            ("qty", "desc", "数量 降序"),
            ("price", "asc", "价格 升序"),
            ("price", "desc", "价格 降序"),
            ("balance", "asc", "对账金额 升序"),
            ("balance", "desc", "对账金额 降序"),
            ("cost", "asc", "总成本 升序"),
            ("cost", "desc", "总成本 降序"),
        ]

        return ctx


class OrderDetailView(LoginRequiredMixin, DetailView):
    model = ClothOrder
    template_name = "order/order_detail.html"
    context_object_name = "order"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        order = self.object
        ctx["edit_mode"] = self.request.GET.get("edit") == "1"
        ctx["form_sections"] = ORDER_FORM_SECTIONS
        ctx["form"] = ClothOrderForm(instance=order)
        ctx["price_form"] = SupplierPriceForm(instance=order)
        ctx["shipment_form"] = ShipmentForm()
        ctx["computed_total_amount"] = order.computed_total_amount
        ctx["total_value"] = order.calculate_total_value()
        ctx["profit"] = order.get_profit()
        ctx["profit_margin"] = order.get_profit_margin()

        shipments_qs = order.shipments.all().order_by("batch_number")
        cost_price = Coalesce("order__finished_product_cost_price", Value(0))
        qty_field = Coalesce("quantity", Value(0))
        ctx["shipments"] = shipments_qs.annotate(
            shipment_total=ExpressionWrapper(qty_field * cost_price, output_field=FloatField())
        )
        ctx["shipment_total_sum"] = order.shipments.filter(is_deleted=False).aggregate(
            total=Sum(F("quantity") * F("order__finished_product_cost_price"))
        )["total"] or 0

        return ctx

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        order = self.object
        form = ClothOrderForm(request.POST, instance=order)
        if form.is_valid():
            form.save()
            logger.info(t("log.order_update", username=request.user.username, serial=order.serial_number))
            messages.success(request, t("msg.order_updated", serial=order.serial_number))
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

    def form_valid(self, form):
        response = super().form_valid(form)
        order = self.object
        logger.info(
            t(
                "log.order_create",
                username=self.request.user.username,
                serial=order.serial_number,
                customer=order.customer or "-",
                cloth=order.cloth_type or "-",
            )
        )
        return response


# ---------------------------------------------------------------------------
# 订单批量操作
# ---------------------------------------------------------------------------


@login_required
@require_POST
def bulk_orders_action(request):
    action = request.POST.get("action", "").strip()
    order_ids = request.POST.getlist("order_ids")
    next_url = request.POST.get("next", "order_list")

    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = "order_list"

    if not action or not order_ids:
        messages.warning(request, t("msg.select_action_order"))
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
            "payment_paid": t("label.payment_paid"),
            "payment_unpaid": t("label.payment_unpaid"),
            "paid_yes": t("label.supplier_paid"),
            "paid_no": t("label.supplier_unpaid"),
            "overdue_yes": t("label.overdue_yes"),
            "overdue_no": t("label.overdue_no"),
            "invoice_yes": t("label.invoice_yes"),
            "invoice_no": t("label.invoice_no"),
        }
        logger.info(
            t(
                "log.bulk_action",
                username=request.user.username,
                label=action_labels.get(action, action),
                count=count,
                ids=",".join(order_ids),
            )
        )
        messages.success(request, t("msg.action_done", count=count, label=action_labels.get(action, action)))

    elif action == "delete":
        deleted = orders.delete()[0]
        logger.info(
            t(
                "log.bulk_action",
                username=request.user.username,
                label="删除",
                count=deleted,
                ids=",".join(order_ids),
            )
        )
        messages.success(request, t("msg.deleted_count", count=deleted))

    elif action == "export":
        return redirect("orders_export")

    else:
        messages.warning(request, t("msg.unknown_action", action=action))

    return redirect(next_url)


@admin_required
@login_required
@require_POST
def order_delete(request, pk):
    order = get_object_or_404(ClothOrder, pk=pk)
    serial = order.serial_number
    order.delete()
    logger.info(t("log.order_delete", username=request.user.username, serial=serial))
    messages.success(request, t("msg.order_deleted", serial=serial))
    return redirect("order_list")


@admin_required
@login_required
def orders_delete_all(request):
    if request.method == "POST":
        count = ClothOrder.objects.all().delete()[0]
        logger.info(t("log.orders_delete_all", username=request.user.username, count=count))
        messages.success(request, t("msg.all_orders_cleared", count=count))
        return redirect("order_list")
    order_count = ClothOrder.objects.count()
    return render(request, "order/delete_all_confirm.html", {"order_count": order_count})


# ---------------------------------------------------------------------------
# 订单导出 / 导入
# ---------------------------------------------------------------------------


@login_required
def orders_export(request):
    df = export_orders_dataframe()
    filename = f"orders_backup_{now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    logger.info(t("log.export_orders", username=request.user.username))
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    with pd.ExcelWriter(response, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=t("label.order_sheet"), index=False)
    return response


@login_required
def orders_import_page(request):
    return render(request, "order/orders_import.html")


@login_required
@require_POST
@validate_file_upload
def orders_import_start(request):
    task_id = create_import_task(request.user.id)
    logger.info(t("log.import_orders_start", username=request.user.username))
    start_import_task(task_id, request.upload_file_bytes)
    return JsonResponse({"task_id": task_id})


@login_required
def orders_import_progress(request, task_id):
    task = get_import_task(str(task_id), request.user.id)
    if not task:
        return JsonResponse(
            {
                "status": "pending",
                "percent": 0,
                "message": t("page.task_initializing"),
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
            "current": task.get("current", 0),
            "total": task.get("total", 0),
            "result": task.get("result"),
            "error": task.get("error"),
        }
    )


# ---------------------------------------------------------------------------
# 库存
# ---------------------------------------------------------------------------


@login_required
def inventory_import_page(request):
    return render(request, "order/inventory_import.html")

@login_required
@require_POST
@validate_file_upload
def inventory_import_start(request):
    task_id = create_inventory_task(request.user.id)
    logger.info(t("log.import_inventory_start", username=request.user.username))
    start_inventory_task(task_id, request.upload_file_bytes)
    return JsonResponse({"task_id": task_id})


@login_required
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


@login_required
def inventory_export(request):
    return redirect("inventory_list")


@admin_required
@login_required
@require_POST
def inventory_delete_all(request):
    count = InventoryItem.objects.all().delete()[0]
    logger.info(t("log.inventory_delete_all", username=request.user.username, count=count))
    messages.success(request, t("msg.all_inventory_cleared", count=count))
    return redirect("inventory_list")


class InventoryListView(LoginRequiredMixin, ListView):
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



class InventoryDetailView(LoginRequiredMixin, DetailView):
    model = InventoryItem
    template_name = "order/inventory_detail.html"
    context_object_name = "item"


class InventoryUpdateView(LoginRequiredMixin, UpdateView):
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


@login_required
@require_POST
def inventory_bulk_action(request):
    return redirect("inventory_list")

class InventoryLogListView(LoginRequiredMixin, ListView):
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

# ---------------------------------------------------------------------------
# 库存日志导出 / 清空
# ---------------------------------------------------------------------------


@login_required
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
@login_required
@require_POST
def inventory_log_delete_all(request):
    """清空全部库存日志"""
    count = InventoryLog.objects.all().delete()[0]
    logger.info(t("log.inventory_log_delete_all", username=request.user.username, count=count))
    messages.success(request, f"已清空全部 {count} 条库存日志")
    return redirect("inventory_log_list")

# ---------------------------------------------------------------------------
# 面料目录
# ---------------------------------------------------------------------------


class ClothCatalogListView(LoginRequiredMixin, ListView):
    model = ClothCatalog
    template_name = "order/cloth_catalog_list.html"
    context_object_name = "items"
    paginate_by = 50

    def get_paginate_by(self, queryset):
        try:
            return int(self.request.GET.get("per_page", 50))
        except (ValueError, TypeError):
            return 50

    def get_queryset(self):
        qs = ClothCatalog.objects.all()
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(cloth_code__icontains=q)
                | Q(cloth_name__icontains=q)
                | Q(cloth_type__icontains=q)
                | Q(customer__icontains=q)
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


class ClothCatalogDetailView(LoginRequiredMixin, DetailView):
    model = ClothCatalog
    template_name = "order/cloth_catalog_detail.html"
    context_object_name = "item"


class ClothCatalogCreateView(LoginRequiredMixin, CreateView):
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
@login_required
def cloth_catalog_import(request):
    return render(request, "order/cloth_catalog_import.html")


@admin_required
@login_required
@require_POST
@validate_file_upload
def cloth_catalog_import_start(request):
    task_id = create_catalog_task(request.user.id)
    logger.info(t("log.catalog_task", task_id=task_id, user_id=request.user.id))
    start_catalog_task(task_id, request.upload_file_bytes)
    return JsonResponse({"task_id": task_id})


@admin_required
@login_required
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
@login_required
@require_POST
def cloth_catalog_delete(request, pk):
    obj = get_object_or_404(ClothCatalog, pk=pk)
    code = obj.cloth_code
    obj.delete()
    logger.info(t("log.catalog_delete", username=request.user.username, code=code))
    messages.success(request, t("msg.catalog_deleted", code=obj.cloth_code))
    return redirect("cloth_catalog_list")


@admin_required
@login_required
@require_POST
def cloth_catalog_delete_all(request):
    count = ClothCatalog.objects.all().delete()[0]
    logger.info(t("log.catalog_delete_all", username=request.user.username, count=count))
    messages.success(request, t("msg.all_catalog_cleared", count=count))
    return redirect("cloth_catalog_list")


@login_required
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


# ---------------------------------------------------------------------------
# 订单进度 / 编辑 / 对账单
# ---------------------------------------------------------------------------


@admin_required
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
            logger.info(
                t(
                    "log.progress_update",
                    username=request.user.username,
                    serial=order.serial_number,
                    stage=order.current_stage_name,
                )
            )
            return JsonResponse({"status": "ok", "stage": order.current_stage_name})
        return JsonResponse({"error": t("msg.invalid_progress")}, status=400)
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return JsonResponse({"error": str(e)}, status=400)


@admin_required
@login_required
def order_edit_redirect(request, pk):
    return redirect(f"{reverse('order_detail', kwargs={'pk': pk})}?edit=1")


@admin_required
@login_required
def order_statement(request, pk):
    order = get_object_or_404(ClothOrder, pk=pk)
    output = generate_order_statement(order)
    logger.info(t("log.statement", username=request.user.username, serial=order.serial_number))
    ts = now().strftime("%Y%m%d_%H%M%S")
    filename = f"statement_{order.serial_number or 'order'}_{ts}.xlsx"
    response = HttpResponse(
        output.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@admin_required
@login_required
def order_statement_bulk(request):
    ids_raw = request.GET.get("ids", "").strip()
    if not ids_raw:
        messages.error(request, t("msg.order_id_required"))
        return redirect("order_list")
    try:
        pk_list = [int(x) for x in ids_raw.split(",") if x.strip()]
    except ValueError:
        messages.error(request, t("msg.order_id_invalid"))
        return redirect("order_list")
    if not pk_list:
        messages.error(request, t("msg.order_not_found"))
        return redirect("order_list")

    orders = ClothOrder.objects.filter(pk__in=pk_list).order_by("-order_date", "-serial_number")
    if not orders.exists():
        messages.error(request, t("msg.order_not_matched"))
        return redirect("order_list")

    output = generate_bulk_statement(list(orders))
    logger.info(
        t(
            "log.statement_bulk",
            username=request.user.username,
            count=orders.count(),
        )
    )
    ts = now().strftime("%Y%m%d_%H%M%S")
    filename = f"statement_bulk_{ts}.xlsx"
    response = HttpResponse(
        output.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# 订单状态切换
# ---------------------------------------------------------------------------


@admin_required
@login_required
@require_POST
def order_toggle_supplier_paid(request, pk):
    order = get_object_or_404(ClothOrder, pk=pk)
    order.supplier_paid = not order.supplier_paid
    order.save(update_fields=["supplier_paid"])
    status = t("label.payment_paid") if order.supplier_paid else t("label.payment_unpaid")
    logger.info(
        t(
            "log.toggle_supplier_paid",
            username=request.user.username,
            serial=order.serial_number,
            status=status,
        )
    )
    messages.success(request, t("msg.supplier_paid_updated", status=status))
    return redirect("order_detail", pk=pk)


@admin_required
@login_required
@require_POST
def order_toggle_status(request, pk):
    order = get_object_or_404(ClothOrder, pk=pk)
    if order.order_status == "active":
        order.order_status = "cancelled"
    else:
        order.order_status = "active"
    order.save(update_fields=["order_status"])
    # 注意：原代码用的是 t("label.overdue_no")，明显是复制粘贴错误
    # 这里改为用状态值，如需 i18n 请确认 label.order_active 是否存在
    status = t("label.order_active") if order.order_status == "active" else t("label.order_cancelled")
    logger.info(
        t(
            "log.toggle_order_status",
            username=request.user.username,
            serial=order.serial_number,
            status=status,
        )
    )
    messages.success(request, t("msg.order_status_updated", status=status))
    return redirect("order_detail", pk=pk)


@admin_required
@login_required
@require_POST
def order_toggle_payment_status(request, pk):
    order = get_object_or_404(ClothOrder, pk=pk)
    if order.payment_status == "paid":
        order.payment_status = "unpaid"
    else:
        order.payment_status = "paid"
    order.save(update_fields=["payment_status"])
    status = t("label.payment_paid") if order.payment_status == "paid" else t("label.payment_unpaid")
    logger.info(
        t(
            "log.toggle_payment_status",
            username=request.user.username,
            serial=order.serial_number,
            status=status,
        )
    )
    messages.success(request, t("msg.payment_status_updated", status=status))
    return redirect("order_detail", pk=pk)


@admin_required
@login_required
@require_POST
def order_refresh_calculations(request, pk):
    order = get_object_or_404(ClothOrder, pk=pk)
    qty = float(order.total_shipment_from_shipments() or 0)
    cost_price = float(order.finished_product_cost_price or 0)
    sell_price = float(order.price or 0)

    if qty > 0 and cost_price > 0:
        order.total_amount = order.computed_total_amount
        order.finished_product_total_amount = order.computed_finished_product_total_amount

        update_fields = ["total_amount", "finished_product_total_amount"]

        if not order.shipment_quantity_unit and order.quantity_unit:
            order.shipment_quantity_unit = order.quantity_unit
            update_fields.append("shipment_quantity_unit")

        if not order.supplier_shipped:
            order.supplier_shipped = True
            update_fields.append("supplier_shipped")

        order.save(update_fields=update_fields)
        logger.info(
            t(
                "log.refresh_calculations",
                username=request.user.username,
                serial=order.serial_number,
                balance=order.finished_product_total_amount,
                cost=order.total_amount,
            )
        )
        messages.success(request, t("msg.order_updated_hash", serial=order.serial_number))
    else:
        messages.warning(request, t("msg.missing_cost_data"))

    return redirect("order_detail", pk=pk)


# ---------------------------------------------------------------------------
# 供应商管理
# ---------------------------------------------------------------------------


class SupplierManageListView(AdminRequiredMixin, LoginRequiredMixin, ListView):
    model = Supplier
    template_name = "order/supplier_manage_list.html"
    context_object_name = "suppliers"
    paginate_by = 50

    def get_queryset(self):
        qs = Supplier.objects.all().select_related("user")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(company_name__icontains=q)
                | Q(contact_person__icontains=q)
                | Q(user__username__icontains=q)
            )
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
        ctx["form_title"] = t("label.account_create_title")
        ctx["submit_label"] = t("label.account_create")
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        logger.info(
            t(
                "log.supplier_create",
                username=self.request.user.username,
                company=self.object.company_name,
            )
        )
        messages.success(self.request, t("msg.supplier_account_created"))
        return response


class SupplierManageUpdateView(AdminRequiredMixin, LoginRequiredMixin, UpdateView):
    model = Supplier
    form_class = SupplierManageForm
    template_name = "order/supplier_manage_form.html"
    success_url = "/supplier/manage/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = t("label.account_edit", company=self.object.company_name)
        ctx["submit_label"] = t("label.account_save")
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        logger.info(
            t(
                "log.supplier_update",
                username=self.request.user.username,
                company=self.object.company_name,
            )
        )
        messages.success(self.request, t("msg.supplier_account_saved"))
        return response


@admin_required
@login_required
def supplier_manage_delete(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == "POST":
        company = supplier.company_name
        user = supplier.user
        if hasattr(user, "supplier_profile"):
            supplier.delete()
            user.delete()
            logger.info(t("log.supplier_delete", username=request.user.username, company=company))
            messages.success(request, t("msg.supplier_deleted", company=company))
        else:
            messages.error(request, t("msg.delete_failed"))
        return redirect("supplier_manage_list")
    return render(request, "order/supplier_manage_confirm_delete.html", {"supplier": supplier})


@admin_required
@login_required
def supplier_manage_reset_password(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == "POST":
        new_password = request.POST.get("new_password", "").strip()
        if not new_password:
            messages.error(request, t("msg.password_required"))
        else:
            supplier.user.set_password(new_password)
            supplier.user.save()
            logger.info(
                t(
                    "log.supplier_reset_password",
                    username=request.user.username,
                    company=supplier.company_name,
                )
            )
            messages.success(request, t("msg.supplier_password_reset", name=supplier.company_name))
        return redirect("supplier_manage_list")
    return render(request, "order/supplier_manage_reset_password.html", {"supplier": supplier})


# ---------------------------------------------------------------------------
# 供应商端
# ---------------------------------------------------------------------------


class SupplierDashboardView(SupplierRequiredMixin, ListView):
    model = ClothOrder
    template_name = "order/supplier_dashboard.html"
    context_object_name = "orders"
    paginate_by = 20

    def get_queryset(self):
        supplier = self.request.user.supplier_profile
        qs = ClothOrder.objects.filter(supplier=supplier)

        # 筛选
        params = self.request.GET
        search = params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(customer__icontains=search)
                | Q(cloth_type__icontains=search)
                | Q(serial_number__icontains=search)
            )

        status = params.get("status", "").strip()
        if status == "pending":
            qs = qs.filter(finished_product_cost_price__isnull=True)
        elif status == "quoted":
            qs = qs.filter(
                finished_product_cost_price__isnull=False,
                supplier_shipped=False,
                has_active_shipments=False,
            )
        elif status == "shipped":
            qs = qs.filter(Q(supplier_shipped=True) | Q(has_active_shipments=True))

        start_date = params.get("start_date", "").strip()
        if start_date:
            qs = qs.filter(order_date__gte=start_date)

        end_date = params.get("end_date", "").strip()
        if end_date:
            qs = qs.filter(order_date__lte=end_date)

        qs = qs.order_by("-order_date", "-serial_number")

        subq = (
            Shipment.objects.filter(order=OuterRef("pk"), is_deleted=False)
            .values("order")
            .annotate(total=Sum("quantity"))
            .values("total")
        )
        has_ship = Shipment.objects.filter(order=OuterRef("pk"), is_deleted=False)

        qs = qs.prefetch_related(
            Prefetch(
                "shipments",
                queryset=Shipment.objects.annotate(
                    row_total=ExpressionWrapper(
                        F("quantity") * F("order__finished_product_cost_price"),
                        output_field=FloatField(),
                    )
                ).order_by("batch_number"),
            )
        )

        return qs.annotate(
            _ship_qty=Subquery(subq, output_field=FloatField()),
            has_active_shipments=Exists(has_ship),
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        supplier = self.request.user.supplier_profile
        # Avoid calling get_queryset() twice - use the paginated object_list
        qs = self.get_queryset()

        ctx["supplier"] = supplier

        # 合并计数为一次聚合
        cnt = qs.aggregate(
            total=Count("id"),
            quoted=Count("id", filter=Q(finished_product_cost_price__isnull=False)),
            pending=Count("id", filter=Q(finished_product_cost_price__isnull=True)),
            wait_ship=Count("id", filter=Q(
                finished_product_cost_price__isnull=False,
                supplier_shipped=False,
                has_active_shipments=False,
            )),
            shipped=Count("id", filter=Q(
                Q(supplier_shipped=True) | Q(has_active_shipments=True)
            )),
        )
        ctx["total_count"] = cnt["total"]
        ctx["quoted_count"] = cnt["quoted"]
        ctx["pending_count"] = cnt["pending"]
        ctx["quoted_wait_ship_count"] = cnt["wait_ship"]
        ctx["shipped_count"] = cnt["shipped"]

        # 计算每条订单的小计（使用已注解的 _shipment_qty，不触发额外查询）
        totals = {}
        for o in qs:
            price_val = float(o.finished_product_cost_price or 0)
            num_val = float(getattr(o, "_ship_qty") or 0)
            totals[str(o.pk)] = price_val * num_val
        ctx["order_totals"] = totals

        ctx["filter_params"] = self.request.GET
        ctx["per_page_options"] = (10, 20, 50, 100)
        ctx["per_page"] = self.paginate_by
        return ctx


class SupplierOrderDetailView(SupplierRequiredMixin, DetailView):
    model = ClothOrder
    template_name = "order/supplier_order_detail.html"
    context_object_name = "order"

    def get_queryset(self):
        supplier = self.request.user.supplier_profile
        return ClothOrder.objects.filter(supplier=supplier)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        order = self.object
        ctx["form_sections"] = ORDER_FORM_SECTIONS
        ctx["price_form"] = SupplierPriceForm(instance=order)
        ctx["shipment_form"] = ShipmentForm()

        shipments_qs = order.shipments.all().order_by("batch_number")
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
            supplier_name = request.user.supplier_profile.company_name

            ship_date = request.POST.get("shipment_date", "").strip()
            ship_qty = request.POST.get("shipment_quantity", "").strip()

            if ship_date and ship_qty:
                try:
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
                    notify_all_staff(
                        title=t("label.supplier_shipped"),
                        message=t(
                            "msg.supplier_shipped_notify",
                            name=supplier_name,
                            serial=order.serial_number,
                            qty=qty,
                            unit=order.quantity_unit or "",
                        ),
                        link=reverse("order_detail", kwargs={"pk": order.pk}),
                    )
                except (ValueError, TypeError) as e:
                    messages.warning(request, t("msg.shipment_data_invalid"))

            total_qty = float(order.total_shipment_from_shipments() or 0)
            order.total_shipment_quantity = total_qty
            cost = float(order.finished_product_cost_price or 0)
            order.total_amount = order.computed_total_amount
            price = float(order.price or 0)
            order.finished_product_total_amount = order.computed_finished_product_total_amount
            order.supplier_shipped = order.shipments.filter(is_deleted=False).exists()

            if not order.shipment_quantity_unit and order.quantity_unit:
                order.shipment_quantity_unit = order.quantity_unit

            order.save(
                update_fields=[
                    "total_shipment_quantity",
                    "total_amount",
                    "finished_product_total_amount",
                    "supplier_shipped",
                    "shipment_quantity_unit",
                ]
            )

            logger.info(
                t(
                    "log.supplier_quote",
                    name=supplier_name,
                    serial=order.serial_number,
                    price=order.finished_product_cost_price,
                    unit=order.cost_price_unit,
                )
            )
            notify_all_staff(
                title=t("label.supplier_quoted"),
                message=t("msg.supplier_quoted_notify", name=supplier_name, serial=order.serial_number),
                link=reverse("order_detail", kwargs={"pk": order.pk}),
            )

            messages.success(request, t("msg.order_updated_hash", serial=order.serial_number))
            return redirect("supplier_order_detail", pk=order.pk)

        ctx = self.get_context_data(object=order)
        ctx["price_form"] = form
        return self.render_to_response(ctx)


# ---------------------------------------------------------------------------
# 发货管理
# ---------------------------------------------------------------------------


@login_required
@require_POST
def order_shipment_create(request, pk):
    order = get_object_or_404(ClothOrder, pk=pk)
    # Permission check: admin always allowed, supplier only if assigned
    if not request.user.is_staff:
        if not hasattr(request.user, "supplier_profile") or not order.supplier_id or order.supplier.user_id != request.user.id:
            from django.contrib import messages
            messages.error(request, "无权访问此订单的出货记录")
            if hasattr(request.user, "supplier_profile"):
                from django.shortcuts import redirect
                return redirect("supplier_dashboard")
            return redirect("dashboard")

    form = ShipmentForm(request.POST)
    if form.is_valid():
        shipment = form.save(commit=False)
        shipment.order = order

        # 自动分配批次号
        max_batch = order.shipments.aggregate(m=models.Max("batch_number"))["m"] or 0
        shipment.batch_number = max_batch + 1

        if request.user.is_authenticated:
            shipment.created_by = request.user

        shipment.save()
        order.supplier_shipped = True
        order.save(update_fields=["supplier_shipped"])

        logger.info(
            t(
                "log.shipment_add",
                username=request.user.username,
                serial=order.serial_number,
                batch=shipment.batch_number,
                quantity=shipment.quantity,
            )
        )
        messages.success(request, t("msg.shipment_added", batch=shipment.batch_number))
    else:
        for field, errors in form.errors.items():
            for err in errors:
                messages.error(request, f"{field}: {err}")

    # 根据用户角色返回不同页面
    if hasattr(request.user, "supplier_profile"):
        fallback = reverse("supplier_order_detail", kwargs={"pk": pk})
    else:
        fallback = reverse("order_detail", kwargs={"pk": pk})
    return redirect(request.META.get("HTTP_REFERER", fallback))


@login_required
@require_POST
def order_shipment_delete(request, pk, shipment_pk):
    shipment = get_object_or_404(Shipment, pk=shipment_pk, order_id=pk)
    order = shipment.order
    # Permission check: admin always allowed, supplier only if assigned
    if not request.user.is_staff:
        if not hasattr(request.user, "supplier_profile") or not order.supplier_id or order.supplier.user_id != request.user.id:
            from django.contrib import messages
            messages.error(request, "无权访问此订单的出货记录")
            if hasattr(request.user, "supplier_profile"):
                from django.shortcuts import redirect
                return redirect("supplier_dashboard")
            return redirect("dashboard")

    batch = shipment.batch_number

    logger.info(
        t(
            "log.shipment_del",
            username=request.user.username,
            serial=order.serial_number,
            batch=batch,
        )
    )
    shipment.is_deleted = True
    shipment.save(update_fields=["is_deleted"])

    # 更新供应商发货标记
    if not order.shipments.filter(is_deleted=False).exists():
        order.supplier_shipped = False
        order.save(update_fields=["supplier_shipped"])

    messages.success(request, t("msg.shipment_deleted", batch=batch))

    if hasattr(request.user, "supplier_profile"):
        fallback = reverse("supplier_order_detail", kwargs={"pk": pk})
    else:
        fallback = reverse("order_detail", kwargs={"pk": pk})
    return redirect(request.META.get("HTTP_REFERER", fallback))


@login_required
def order_shipment_edit(request, pk, shipment_pk):
    shipment = get_object_or_404(Shipment, pk=shipment_pk, order_id=pk)
    order = shipment.order
    # Permission check: admin always allowed, supplier only if assigned
    if not request.user.is_staff:
        if not hasattr(request.user, "supplier_profile") or not order.supplier_id or order.supplier.user_id != request.user.id:
            from django.contrib import messages
            messages.error(request, "无权访问此订单的出货记录")
            if hasattr(request.user, "supplier_profile"):
                from django.shortcuts import redirect
                return redirect("supplier_dashboard")
            return redirect("dashboard")


    if request.method == "POST":
        form = ShipmentForm(request.POST, instance=shipment)
        if form.is_valid():
            form.save()
            logger.info(
                t(
                    "log.shipment_edit",
                    username=request.user.username,
                    serial=order.serial_number,
                    batch=shipment.batch_number,
                    quantity=shipment.quantity,
                )
            )
            messages.success(request, t("msg.shipment_updated", batch=shipment.batch_number))
        else:
            for field, errors in form.errors.items():
                for err in errors:
                    messages.error(request, f"{field}: {err}")

        if hasattr(request.user, "supplier_profile"):
            fallback = reverse("supplier_order_detail", kwargs={"pk": pk})
        else:
            fallback = reverse("order_detail", kwargs={"pk": pk})
        return redirect(request.META.get("HTTP_REFERER", fallback))

    # GET: 返回 JSON 用于内联编辑
    return JsonResponse(
        {
            "date": shipment.date.isoformat() if shipment.date else "",
            "quantity": float(shipment.quantity) if shipment.quantity else 0,
            "batch_number": shipment.batch_number,
        }
    )


@login_required
def supplier_logout_view(request):
    logout(request)
    return redirect("home")


# ---------------------------------------------------------------------------
# 杂项 API
# ---------------------------------------------------------------------------


@login_required
def order_list_updated_at(request):
    latest = ClothOrder.objects.order_by("-updated_at").values("updated_at").first()
    ts = latest["updated_at"].isoformat() if latest and latest["updated_at"] else ""
    return JsonResponse({"updated_at": ts})


# ---------------------------------------------------------------------------
# 通知
# ---------------------------------------------------------------------------


@login_required
def notification_list(request):
    notifications = request.user.notifications.all()
    page = request.GET.get("page", 1)
    paginator = Paginator(notifications, 20)
    page_obj = paginator.get_page(page)
    return render(
        request,
        "order/notification_list.html",
        {
            "page_obj": page_obj,
            "unread_count": notifications.filter(is_read=False).count(),
        },
    )


@login_required
def notification_unread_count(request):
    count = request.user.notifications.filter(is_read=False).count()
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
        data.append(
            {
                "id": n.pk,
                "title": n.title,
                "message": n.message,
                "link": n.link,
                "created_at": n.created_at.strftime("%m-%d %H:%M"),
            }
        )
    return JsonResponse({"notifications": data})


# ---------------------------------------------------------------------------
# 报价
# ---------------------------------------------------------------------------


@login_required
@require_GET
def quotation_lookup(request):
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
            qs = qs.filter(
                Q(article_no__icontains=q)
                | Q(composition__icontains=q)
                | Q(supplier_name__icontains=q)
            )
        return qs

    def get_paginate_by(self, queryset):
        try:
            return int(self.request.GET.get("per_page", 50))
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
        "article_no",
        "supplier_name",
        "supplier_contact",
        "date_sent",
        "gfg_dev_no",
        "preferred_material",
        "color_card",
        "composition",
        "weight",
        "cuttable_width",
        "yarn_count",
        "density_gauge",
        "lead_time",
        "price_validity",
        "price_200m_text",
        "price_200m",
        "price_200m_print",
        "price_200m_unit",
        "regular_mcq",
        "price_regular_text",
        "price_regular",
        "price_regular_print",
        "price_regular_unit",
        "remark",
        "quoted_to",
        "extra_remark",
    ]
    template_name = "order/quotation_form.html"
    success_url = "/quotation/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = t("label.quotation_add")
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        logger.info(
            t(
                "log.quotation_create",
                username=self.request.user.username,
                article=self.object.article_no,
            )
        )
        return response


@admin_required
@login_required
@require_POST
def quotation_delete(request, pk):
    obj = get_object_or_404(FabricQuotation, pk=pk)
    article = obj.article_no
    obj.delete()
    logger.info(t("log.quotation_delete", username=request.user.username, article=article))
    messages.success(request, t("msg.quotation_deleted", article=article))
    return redirect("quotation_list")


def _parse_price(text):
    """解析价格文本，返回 (素色价格, 印花价格, 单位)"""
    if not text or not isinstance(text, str):
        return None, None, "M"
    text = text.strip()
    if not text or text == "N/A":
        return None, None, "M"

    cleaned = re.sub(r"^(RMB|RMB)\s*", "", text, flags=re.IGNORECASE).strip()
    unit = "KG" if "/kg" in cleaned.lower() else "M"

    solid_val = None
    print_val = None

    parts = re.split(r"[,，]", cleaned)
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
        m = re.search(r"(\d+(?:\.\d+)?)", cleaned)
        if m:
            solid_val = float(m.group(1))

    return solid_val, print_val, unit


@admin_required
@login_required
def quotation_import(request):
    if request.method == "POST":
        excel_file = request.FILES.get("excel_file")
        if not excel_file:
            messages.error(request, t("msg.select_file"))
            return redirect("quotation_import")

        try:
            import openpyxl

            wb = openpyxl.load_workbook(excel_file, data_only=True)
            if "Summary" not in wb.sheetnames:
                messages.error(request, t("msg.excel_summary_missing"))
                return redirect("quotation_import")
            ws = wb["Summary"]
        except Exception as e:
            messages.error(request, t("msg.read_excel_error", e=e))
            return redirect("quotation_import")

        batch = []
        imported = 0
        skipped = 0

        try:
            with transaction.atomic():
                for row_idx, row in enumerate(
                    ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True), 2
                ):
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
                    p200m, p200m_print, p200m_unit = _parse_price(price_200m_text)

                    price_regular_text = str(row[13]).strip() if row[13] else ""
                    preg, preg_print, preg_unit = _parse_price(price_regular_text)

                    extra = ""
                    for ci in [20, 21, 22, 23, 24, 25, 26, 27, 28, 29]:
                        if ci < len(row) and row[ci] is not None:
                            v = str(row[ci]).strip()
                            if v:
                                extra += v + " "
                    extra = extra.strip()

                    batch.append(
                        FabricQuotation(
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
                        )
                    )
                    imported += 1

                # 事务内先删后插，失败自动回滚
                FabricQuotation.objects.all().delete()
                FabricQuotation.objects.bulk_create(batch)

            messages.success(request, t("msg.import_complete", imported=imported, skipped=skipped))
            logger.info(
                t(
                    "log.quotation_import_done",
                    username=request.user.username,
                    imported=imported,
                    skipped=skipped,
                )
            )
            return redirect("quotation_list")

        except Exception as e:
            messages.error(request, t("msg.import_failed", error=str(e)))
            logger.error(f"Quotation import failed: {e}", exc_info=True)
            return redirect("quotation_import")

    return render(request, "order/quotation_import.html")


@admin_required
@login_required
@require_POST
def quotation_delete_all(request):
    count = FabricQuotation.objects.all().delete()[0]
    logger.info(t("log.quotation_delete_all", username=request.user.username, count=count))
    messages.success(request, t("msg.all_quotation_cleared", count=count))
    return redirect("quotation_list")
