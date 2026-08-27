"""视图模块：订单（由 views.py 拆分而来）。"""

import json
from datetime import date, datetime
import pandas as pd
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
from django.db.models.functions import Coalesce, TruncMonth
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
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
from .order_excel import export_orders_dataframe
from .order_filters import filter_orders_queryset
from .statement import generate_bulk_statement, generate_order_statement
from .views_common import AdminRequiredMixin, logger


class OrderListView(AdminRequiredMixin, LoginRequiredMixin, ListView):
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


class OrderDetailView(AdminRequiredMixin, LoginRequiredMixin, DetailView):
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


class OrderCreateView(AdminRequiredMixin, LoginRequiredMixin, CreateView):
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


@admin_required
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
def order_delete(request, pk):
    order = get_object_or_404(ClothOrder, pk=pk)
    serial = order.serial_number
    order.delete()
    logger.info(t("log.order_delete", username=request.user.username, serial=serial))
    messages.success(request, t("msg.order_deleted", serial=serial))
    return redirect("order_list")


@admin_required
def orders_delete_all(request):
    if request.method == "POST":
        count = ClothOrder.objects.all().delete()[0]
        logger.info(t("log.orders_delete_all", username=request.user.username, count=count))
        messages.success(request, t("msg.all_orders_cleared", count=count))
        return redirect("order_list")
    order_count = ClothOrder.objects.count()
    return render(request, "order/delete_all_confirm.html", {"order_count": order_count})


@admin_required
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


@admin_required
def orders_import_page(request):
    return render(request, "order/orders_import.html")


@admin_required
def orders_import_start(request):
    task_id = create_import_task(request.user.id)
    logger.info(t("log.import_orders_start", username=request.user.username))
    start_import_task(task_id, request.upload_file_bytes)
    return JsonResponse({"task_id": task_id})


@admin_required
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


@admin_required
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
def order_edit_redirect(request, pk):
    return redirect(f"{reverse('order_detail', kwargs={'pk': pk})}?edit=1")


@admin_required
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


@admin_required
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
            if order.set_progress_stage("剪版寄出", save=False):
                update_fields.append("progress_current")

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


@admin_required
def order_list_updated_at(request):
    latest = ClothOrder.objects.order_by("-updated_at").values("updated_at").first()
    ts = latest["updated_at"].isoformat() if latest and latest["updated_at"] else ""
    return JsonResponse({"updated_at": ts})
