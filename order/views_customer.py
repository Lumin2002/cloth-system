"""视图模块：内部客户资料管理。"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import CreateView, ListView, UpdateView

from .decorators import admin_required
from .forms import CustomerForm
from .models import Customer
from .views_common import AdminRequiredMixin, logger


class CustomerListView(AdminRequiredMixin, LoginRequiredMixin, ListView):
    model = Customer
    template_name = "order/customer_list.html"
    context_object_name = "customers"
    paginate_by = None

    def get_queryset(self):
        return Customer.objects.all()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        customers = list(Customer.objects.all().order_by("name"))
        ctx["total"] = len(customers)
        ctx["active_count"] = sum(1 for c in customers if c.is_active)
        ctx["customers_json"] = [
            {
                "id": customer.pk,
                "name": customer.name,
                "code": customer.code or "",
                "contact_person": customer.contact_person or "",
                "phone": customer.phone or "",
                "email": customer.email or "",
                "address": customer.address or "",
                "remark": customer.remark or "",
                "is_active": customer.is_active,
                "edit_url": reverse("customer_edit", kwargs={"pk": customer.pk}),
                "delete_url": reverse("customer_delete", kwargs={"pk": customer.pk}),
            }
            for customer in customers
        ]
        return ctx


class CustomerCreateView(AdminRequiredMixin, LoginRequiredMixin, CreateView):
    model = Customer
    form_class = CustomerForm
    template_name = "order/customer_form.html"
    success_url = "/customers/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "新增客户"
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        logger.info(
            "[新增客户] %s 新增客户 %s",
            self.request.user.username,
            self.object.name,
        )
        messages.success(self.request, f"客户「{self.object.name}」已创建")
        return response


class CustomerUpdateView(AdminRequiredMixin, LoginRequiredMixin, UpdateView):
    model = Customer
    form_class = CustomerForm
    template_name = "order/customer_form.html"
    success_url = "/customers/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = f"编辑客户：{self.object.name}"
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        logger.info(
            "[修改客户] %s 修改客户 %s",
            self.request.user.username,
            self.object.name,
        )
        messages.success(self.request, f"客户「{self.object.name}」已保存")
        return response


@admin_required
def customer_options(request):
    """订单表单中客户输入框的已有客户建议。"""
    names = list(
        Customer.objects.filter(is_active=True)
        .order_by("name")
        .values_list("name", flat=True)
    )
    return JsonResponse({"customers": names})


@admin_required
def customer_delete(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == "POST":
        name = customer.name
        customer.delete()
        logger.info("[删除客户] %s 删除客户 %s", request.user.username, name)
        messages.success(request, f"客户「{name}」已删除")
        return redirect("customer_list")
    return redirect("customer_list")
