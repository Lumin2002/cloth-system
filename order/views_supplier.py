"""视图模块：供应商管理/供应商端/出货（由 views.py 拆分而来）。"""

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
from django.db.models.functions import Coalesce, TruncMonth
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
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
from .views_common import logger
from .views_common import AdminRequiredMixin, SupplierRequiredMixin


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
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if form.is_valid():
            order = form.save()
            supplier_name = request.user.supplier_profile.company_name

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

            if is_ajax:
                return JsonResponse(
                    {
                        "status": "ok",
                        "message": t("msg.order_updated_hash", serial=order.serial_number),
                        "serial": order.serial_number,
                        "cost_price": str(order.finished_product_cost_price or ""),
                        "cost_price_unit": order.cost_price_unit or "",
                        "address": order.address or "",
                        "remark": order.remark or "",
                        "supplier_shipped": order.supplier_shipped,
                    }
                )

            messages.success(request, t("msg.order_updated_hash", serial=order.serial_number))
            return redirect("supplier_order_detail", pk=order.pk)

        if is_ajax:
            errors = {field: [str(err) for err in errs] for field, errs in form.errors.items()}
            return JsonResponse(
                {"status": "error", "message": t("msg.order_save_failed"), "errors": errors},
                status=400,
            )

        ctx = self.get_context_data(object=order)
        ctx["price_form"] = form
        return self.render_to_response(ctx)


def order_shipment_create(request, pk):
    order = get_object_or_404(ClothOrder, pk=pk)
    # Permission check: admin always allowed, supplier only if assigned
    if not request.user.is_staff:
        if not hasattr(request.user, "supplier_profile") or not order.supplier_id or order.supplier.user_id != request.user.id:
            messages.error(request, "无权访问此订单的出货记录")
            if hasattr(request.user, "supplier_profile"):
                return redirect("supplier_dashboard")
            return redirect("dashboard")
        # 供应商出货前必须已提交有效的成品成本价格
        if not order.finished_product_cost_price or float(order.finished_product_cost_price) <= 0:
            messages.error(request, t("msg.need_cost_price_first"))
            return redirect(reverse("supplier_order_detail", kwargs={"pk": pk}))

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
        total_qty = float(order.total_shipment_from_shipments() or 0)
        order.total_shipment_quantity = total_qty
        order.total_amount = order.computed_total_amount
        order.finished_product_total_amount = order.computed_finished_product_total_amount
        if not order.shipment_quantity_unit and order.quantity_unit:
            order.shipment_quantity_unit = order.quantity_unit
        progress_changed = order.set_progress_stage("剪版寄出", save=False)
        order.save(
            update_fields=[
                "supplier_shipped",
                "total_shipment_quantity",
                "total_amount",
                "finished_product_total_amount",
                "shipment_quantity_unit",
                "progress_current",
            ]
        )

        if order.supplier:
            supplier_name = order.supplier.company_name
        elif hasattr(request.user, "supplier_profile"):
            supplier_name = request.user.supplier_profile.company_name
        else:
            supplier_name = "管理员"
        notify_all_staff(
            title=t("label.supplier_shipped"),
            message=t(
                "msg.supplier_shipped_notify",
                name=supplier_name,
                serial=order.serial_number,
                qty=shipment.quantity,
                unit=order.quantity_unit or "",
            ),
            link=reverse("order_detail", kwargs={"pk": order.pk}),
        )

        if progress_changed:
            logger.info(
                t(
                    "log.progress_update",
                    username=request.user.username,
                    serial=order.serial_number,
                    stage=order.current_stage_name,
                )
            )

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


def order_shipment_delete(request, pk, shipment_pk):
    shipment = get_object_or_404(Shipment, pk=shipment_pk, order_id=pk)
    order = shipment.order
    # Permission check: admin always allowed, supplier only if assigned
    if not request.user.is_staff:
        if not hasattr(request.user, "supplier_profile") or not order.supplier_id or order.supplier.user_id != request.user.id:
            messages.error(request, "无权访问此订单的出货记录")
            if hasattr(request.user, "supplier_profile"):
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


def order_shipment_edit(request, pk, shipment_pk):
    shipment = get_object_or_404(Shipment, pk=shipment_pk, order_id=pk)
    order = shipment.order
    # Permission check: admin always allowed, supplier only if assigned
    if not request.user.is_staff:
        if not hasattr(request.user, "supplier_profile") or not order.supplier_id or order.supplier.user_id != request.user.id:
            messages.error(request, "无权访问此订单的出货记录")
            if hasattr(request.user, "supplier_profile"):
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
