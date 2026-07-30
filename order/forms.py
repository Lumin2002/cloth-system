from django import forms
from django.utils.safestring import mark_safe
from .constants import DEFAULT_STAGES_MAP
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from datetime import date

import html
import json
from .models import ClothOrder, InventoryItem, Supplier, Shipment


# 统一控件样式常量
FORM_CONTROL = {"class": "form-control"}
FORM_SELECT = {"class": "form-select"}
FORM_TEXTAREA = {"class": "form-control", "rows": 3}
# 日期输入框统一常量（type=date原生日期选择器）
DATE_INPUT = {"class": "form-control", "type": "date"}


class ProgressStepperWidget(forms.Widget):
    """进度阶段选择器 - 以可点击标签形式展示"""

    def __init__(self, stages=None, attrs=None):
        super().__init__(attrs)
        self.stages = stages or []

    def render(self, name, value, attrs=None, renderer=None):
        value = int(value) if value else 0
        input_id = attrs.get("id", "")
        html = f'<input type="hidden" name="{name}" value="{value}" id="{input_id}">'
        html += '<div class="progress-stepper d-flex flex-wrap align-items-center gap-1">'
        for i, stage in enumerate(self.stages):
            cls = 'btn btn-sm rounded-pill fw-bold px-3' + (' btn-primary' if i == value else ' btn-outline-primary')
            html += f'<button type="button" class="{cls} stage-btn" data-index="{i}">{html.escape(stage)}</button>'
            if i < len(self.stages) - 1:
                html += '<div class="text-muted" style="font-size:.7rem;">\u2192</div>'
        html += '''
        <script>
        (function(){
            var container = document.currentScript.parentElement;
            var hiddenInput = container.querySelector('input[type="hidden"]');
            container.querySelectorAll('.stage-btn').forEach(function(btn){
                btn.addEventListener('click', function(){
                    container.querySelectorAll('.stage-btn').forEach(function(item){
                        item.classList.remove('btn-primary');
                        item.classList.add('btn-outline-primary');
                    });
                    this.classList.remove('btn-outline-primary');
                    this.classList.add('btn-primary');
                    hiddenInput.value = this.dataset.index;
                })
            })
        })();
        </script>
        '''
        return mark_safe(html)


class ClothOrderForm(forms.ModelForm):
    class Meta:
        model = ClothOrder
        exclude = ("created_at", "updated_at", "progress_current", "progress_stages", "finished_product_cost_price", "cost_price_unit", "total_shipment_quantity", "shipment_quantity_unit", "address", "remark")
        widgets = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field_attrs = {
            "order_status": FORM_SELECT,
            "serial_number": {**FORM_CONTROL, 'placeholder': '留空则自动生成'},
            "order_date": DATE_INPUT,
            "customer": FORM_CONTROL,
            "order_follower": FORM_CONTROL,
            "order_type": FORM_SELECT,
            "order_number": FORM_CONTROL,
            "style_number": FORM_CONTROL,
            "specification": FORM_CONTROL,
            "cloth_type": FORM_CONTROL,
            "textile_type": FORM_SELECT,
            "color": FORM_CONTROL,
            "color_code": FORM_CONTROL,
            "composition": FORM_CONTROL,
            "width": {**FORM_CONTROL, 'step': '0.01'},
            "weight": {**FORM_CONTROL, 'step': '0.01'},
            "processing_type": FORM_CONTROL,
            "order_quantity": {**FORM_CONTROL, 'step': '0.01'},
            "quantity_unit": FORM_SELECT,
            "price": {**FORM_CONTROL, 'step': '0.01'},
            "price_unit": FORM_CONTROL,
            "small_vat_fee": {**FORM_CONTROL, 'step': '0.01'},
            "customer_delivery_date": DATE_INPUT,
            "bulk_progress_tracking": FORM_TEXTAREA,
            "supplier_remark": FORM_TEXTAREA,
            "finished_product_total_amount": {**FORM_CONTROL, 'step': '0.01'},
            "payment_method": FORM_SELECT,
            "payment_period": FORM_CONTROL,
            "reconciliation_date": DATE_INPUT,
            "payment_date": DATE_INPUT,
            "payment_status": FORM_SELECT,
            "overdue_status": FORM_SELECT,
            "invoice_status": FORM_SELECT,
            "certificate_status": FORM_SELECT,
            "certificate_type": FORM_SELECT,
            "latest_certificate_date": DATE_INPUT,
            "actual_operation_date": DATE_INPUT,
            "actual_certificate_date": DATE_INPUT,
            "finished_product_supplier": FORM_CONTROL,
            "booth": FORM_CONTROL,
            "supplier_code": FORM_CONTROL,
            "contract_number": FORM_CONTROL,
            "supplier_invoice_status": FORM_SELECT,
            "supplier_certificate_status": FORM_SELECT,
            "supplier_payment_method": FORM_SELECT,
            "supplier_reconciliation_date": DATE_INPUT,
            "supplier_payment_date": DATE_INPUT,
            "paid_amount": FORM_SELECT,
            "total_amount": {**FORM_CONTROL, 'step': '0.01'},
            "finished_product_cost_price": {**FORM_CONTROL, 'step': '0.01'},
            "cost_price_unit": FORM_CONTROL,
            "total_shipment_quantity": {**FORM_CONTROL, 'step': '0.01'},
            "shipment_quantity_unit": FORM_CONTROL,
            "finished_product_shipment_date_1": DATE_INPUT,
            "finished_product_shipment_quantity_1": {**FORM_CONTROL, 'step': '0.01'},
            "finished_product_shipment_date_2": DATE_INPUT,
            "finished_product_shipment_quantity_2": {**FORM_CONTROL, 'step': '0.01'},
            "finished_product_shipment_date_3": DATE_INPUT,
            "finished_product_shipment_quantity_3": {**FORM_CONTROL, 'step': '0.01'},
            "finished_product_shipment_date_4": DATE_INPUT,
            "finished_product_shipment_quantity_4": {**FORM_CONTROL, 'step': '0.01'},
            "finished_product_shipment_date_5": DATE_INPUT,
            "finished_product_shipment_quantity_5": {**FORM_CONTROL, 'step': '0.01'},
        }

        # 创建订单时必填字段
        required_fields = [
            "order_date", "customer", "order_type", "cloth_type",
            "quantity_unit", "composition", "width", "weight",
            "order_quantity", "price",
        ]
        
        for fname in required_fields:
            if fname in self.fields:
                self.fields[fname].required = True

        # 供应商下拉框单独设置 widget
        if "supplier" in self.fields:
            self.fields["supplier"].widget.attrs.update({"class": "form-select"})
        # 1. 先批量更新所有字段样式
        for field_name, attrs in field_attrs.items():
            if field_name in self.fields:
                self.fields[field_name].widget.attrs.update(attrs)
        
        # 2. 强制覆盖所有日期字段，替换为DateInput（生成type="date"日历输入框）
        DATE_FIELD_LIST = [
            "order_date",
            "customer_delivery_date",
            "reconciliation_date",
            "payment_date",
            "latest_certificate_date",
            "actual_operation_date",
            "actual_certificate_date",
            "supplier_reconciliation_date",
            "supplier_payment_date",
            "finished_product_shipment_date_1",
            "finished_product_shipment_date_2",
            "finished_product_shipment_date_3",
            "finished_product_shipment_date_4",
            "finished_product_shipment_date_5",
        ]
        # 将单位字段切换为下拉框，支持米/码
        if "quantity_unit" in self.fields:
            self.fields["quantity_unit"].widget = forms.Select(
                attrs=FORM_SELECT,
                choices=[("", "---------"), ("米", "米"), ("码", "码")]
            )
        if "price_unit" in self.fields:
            self.fields["price_unit"].widget.attrs.update({"readonly": True, "style": "background:#f8f9fa"})
        for fname in DATE_FIELD_LIST:
            if fname in self.fields:
                self.fields[fname].widget = forms.DateInput(attrs=DATE_INPUT, format="%Y-%m-%d")
        # TextField 款号强制使用单行输入
        if "style_number" in self.fields:
            self.fields["style_number"].widget = forms.TextInput(attrs=FORM_CONTROL)
            
    def clean_serial_number(self):
        serial = self.cleaned_data.get("serial_number")
        if serial is None:
            return serial
        qs = ClothOrder.objects.filter(serial_number=serial)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(f"序号 {serial} 已存在，请更换")
        return serial

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.serial_number:
            last = ClothOrder.objects.order_by("-serial_number").first()
            instance.serial_number = (last.serial_number + 1) if last and last.serial_number else 1
        # 自动填充 finished_product_supplier
        if instance.supplier_id and not instance.finished_product_supplier:
            instance.finished_product_supplier = instance.supplier.company_name
        if instance.order_type in DEFAULT_STAGES_MAP:
            if not instance.pk:
                # 新订单：直接设置默认阶段
                instance.progress_stages = DEFAULT_STAGES_MAP[instance.order_type]
            else:
                # 已有订单：检查 order_type 是否变更
                try:
                    old = ClothOrder.objects.get(pk=instance.pk)
                    if old.order_type != instance.order_type:
                        instance.progress_stages = DEFAULT_STAGES_MAP[instance.order_type]
                except ClothOrder.DoesNotExist:
                    pass
        if commit:
            instance.save()
        return instance


ORDER_FORM_SECTIONS = [
    {"title": "基本信息", "icon": "bi-info-circle", "header": "bg-primary text-white",
     "fields": ("order_status", "serial_number", "order_date", "customer", "order_follower", "order_type",
                "order_number", "style_number", "specification", "supplier")},

    {"title": "布料信息", "icon": "bi-palette", "header": "bg-info text-white",
     "fields": ("cloth_type", "textile_type", "color", "color_code", "composition", "width", "weight", "processing_type")},
    {"title": "数量与价格", "icon": "bi-currency-yen", "header": "bg-success text-white",
     "fields": ("order_quantity", "quantity_unit", "price", "price_unit", "small_vat_fee",
                "customer_delivery_date", "bulk_progress_tracking")},
    {"title": "财务与对账", "icon": "bi-cash-stack", "header": "bg-warning text-dark",
     "fields": ("payment_method", "payment_period",
                "reconciliation_date", "payment_date", "payment_status", "overdue_status",
                "invoice_status", "certificate_status", "certificate_type",
                "latest_certificate_date", "actual_operation_date", "actual_certificate_date")},
    {"title": "供应商信息", "icon": "bi-building", "header": "bg-secondary text-white",
     "fields": ("finished_product_supplier", "booth", "supplier_code", "contract_number",
                "supplier_invoice_status", "supplier_certificate_status", "supplier_payment_method",
                "supplier_reconciliation_date", "supplier_payment_date", "supplier_remark")},

]

CREATE_DEFAULTS = {
    "order_type": "bulk",
    "textile_type": "woven",
    "payment_status": "unpaid",
    "overdue_status": "not_overdue",
    "invoice_status": "not_invoiced",
    "certificate_status": "not_invoiced",
    "payment_method": "before_delivery",
    "certificate_type": "none",
    "supplier_payment_method": "before_delivery",
    "order_date": date.today(),
    "quantity_unit": "米",
}

ORDER_CREATE_PRIMARY_COUNT = 4


class InventoryItemForm(forms.ModelForm):
    class Meta:
        model = InventoryItem
        exclude = ("created_at", "updated_at")
        widgets = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        text = forms.TextInput(attrs=FORM_CONTROL)
        number = forms.NumberInput(attrs=FORM_CONTROL)
        number_min0 = forms.NumberInput(attrs=dict(FORM_CONTROL, min="0"))
        textarea = forms.Textarea(attrs=FORM_TEXTAREA)
        number_step = lambda s: forms.NumberInput(attrs={**FORM_CONTROL, "step": s})

        self.fields["serial_no"].widget = number
        self.fields["cloth_type_id"].widget = number
        self.fields["unique_id"].widget = text
        self.fields["cloth_name"].widget = text
        self.fields["color"].widget = text
        self.fields["composition_en"].widget = text
        self.fields["composition_cn"].widget = text
        self.fields["specification"].widget = text
        self.fields["finishing_en"].widget = text
        self.fields["finishing_cn"].widget = text
        self.fields["width"].widget = number_step("0.01")
        self.fields["weight"].widget = number_step("0.01")
        self.fields["sides"].widget = text
        self.fields["customer"].widget = text
        self.fields["usage"].widget = text
        self.fields["bath_no"].widget = text
        self.fields["position"].widget = text
        self.fields["grey_fabric_no"].widget = text
        self.fields["grey_fabric_price"].widget = text
        self.fields["grey_fabric_source"].widget = text
        # 库存表单日期字段也绑定原生日期选择器
        self.fields["grey_fabric_date"].widget = forms.DateInput(attrs=DATE_INPUT, format="%Y-%m-%d")
        self.fields["finished_price"].widget = text
        self.fields["quantity"].widget = number_min0
        self.fields["remark"].widget = textarea

        # 进度阶段处理（移到__init__内部，修复之前代码错位bug）
        if "progress_stages" in self.fields:
            self.fields["progress_stages"].widget = forms.Textarea(attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": '["客户下单","通知供应商","大货样","批色","查布","发货","待收款","已完成"]'
            })

        if "progress_current" in self.fields:
            try:
                init_val = self.fields["progress_stages"].initial if self.fields["progress_stages"].initial else "[]"
                if self.instance and self.instance.pk:
                    init_val = self.instance.progress_stages
                stages = json.loads(init_val)
                choices = [(i, s) for i, s in enumerate(stages)]
                self.fields["progress_current"] = forms.ChoiceField(
                    choices=choices,
                    required=False,
                    initial=self.instance.progress_current if self.instance.pk else 0,
                    widget=ProgressStepperWidget(stages=[s for _, s in choices]),
                    label="当前进度",
                )
            except Exception:
                pass

    def clean_quantity(self):
        qty = self.cleaned_data.get("quantity")
        if qty is not None and qty < 0:
            raise ValidationError("库存数量不能为负数")
        return qty



class SupplierManageForm(forms.ModelForm):
    """管理员创建/编辑供应商账号的表单"""
    username = forms.CharField(max_length=150, label="登录名",
        widget=forms.TextInput(attrs=FORM_CONTROL),
        help_text="用于供应商登录的账号")
    password = forms.CharField(max_length=128, label="密码",
        widget=forms.PasswordInput(attrs=FORM_CONTROL),
        required=False,
        help_text="留空则不修改密码")

    class Meta:
        model = Supplier
        fields = ["company_name", "contact_person", "phone", "is_active"]
        widgets = {
            "company_name": forms.TextInput(attrs=FORM_CONTROL),
            "contact_person": forms.TextInput(attrs=FORM_CONTROL),
            "phone": forms.TextInput(attrs=FORM_CONTROL),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.user_id:
            self.fields["username"].initial = self.instance.user.username
            self.fields["username"].help_text = "修改登录名后供应商将用新账号登录"
        self.fields["company_name"].widget.attrs.update({"class": "form-select"})

    def clean_username(self):
        username = self.cleaned_data["username"]
        from django.contrib.auth.models import User
        qs = User.objects.filter(username=username)
        if self.instance and self.instance.pk and self.instance.user_id:
            qs = qs.exclude(pk=self.instance.user_id)
        if qs.exists():
            from django.core.exceptions import ValidationError
            raise ValidationError(f"用户名 {username} 已被使用")
        return username

    def save(self, commit=True):
        supplier = super().save(commit=False)
        from django.contrib.auth.models import User
        username = self.cleaned_data["username"]
        password = self.cleaned_data.get("password") or None
        if supplier.pk and supplier.user_id:
            user = supplier.user
            if user.username != username:
                user.username = username
            if password:
                user.set_password(password)
            user.save()
        else:
            from django.contrib.auth.models import User
            pwd = password or username
            user = User.objects.create_user(username=username, password=pwd)
            supplier.user = user
        if commit:
            supplier.save()
        return supplier


class SupplierPriceForm(forms.ModelForm):
    """供应商填写价格、出货、地址、备注用表单"""

    class Meta:
        model = ClothOrder
        fields = [
            "finished_product_cost_price", "cost_price_unit",
            "address",
            "remark",
        ]
        widgets = {
            "finished_product_cost_price": forms.NumberInput(attrs={**FORM_CONTROL, "step": "0.01"}),
            "cost_price_unit": forms.TextInput(attrs={**FORM_CONTROL, "readonly": True}),
            "address": forms.Textarea(attrs=FORM_TEXTAREA),
            "remark": forms.Textarea(attrs=FORM_TEXTAREA),
            "supplier_remark": forms.Textarea(attrs=FORM_TEXTAREA),
        }
        labels = {
            "finished_product_cost_price": "成品成本价格",
            "cost_price_unit": "成本价格单位",
            "address": "地址",
            "remark": "备注",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        required_fields = [
            "finished_product_cost_price",
            "cost_price_unit",
        ]
        for field_name in required_fields:
            self.fields[field_name].required = True

        self.fields["address"].required = False
        self.fields["remark"].required = False

        # 成本价格单位自动同步订单的价格单位
        if self.instance and self.instance.pk:
            if not self.instance.cost_price_unit and self.instance.price_unit:
                self.initial["cost_price_unit"] = self.instance.price_unit
                self.instance.cost_price_unit = self.instance.price_unit

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
        return instance



class ShipmentForm(forms.ModelForm):
    """新增出货记录表单"""
    class Meta:
        model = Shipment
        fields = ['date', 'quantity']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '出货数量'}),
        }
        labels = {
            'date': '出货日期',
            'quantity': '出货数量',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['date'].required = True
        self.fields['quantity'].required = True
