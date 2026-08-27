"""视图模块：仪表板（由 views.py 拆分而来）。"""

import re
from datetime import date, datetime
from django.contrib.auth.decorators import login_required
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
from django.shortcuts import get_object_or_404, redirect, render
from .dashboard_stats import (
    build_current_month_finance,
    build_current_month_orders,
    build_month_compare,
    build_monthly_chart_data,
)
from .decorators import admin_required, validate_file_upload
from .i18n import t
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
