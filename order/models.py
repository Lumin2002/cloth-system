import re

from django.db import models
from django.core.validators import MinValueValidator

from .mixins import ClothCatalogMixin, ProgressStageMixin


class Supplier(models.Model):
    """供应商账号 —— 绑定 User，登录后可查看分配的订单并填写价格"""
    user = models.OneToOneField(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="supplier_profile",
        verbose_name="用户账号",
    )
    company_name = models.CharField(
        max_length=200,
        unique=True,
        verbose_name="公司名称",
        db_index=True,
    )
    contact_person = models.CharField(max_length=100, verbose_name="联系人", blank=True)
    phone = models.CharField(max_length=50, verbose_name="联系电话", blank=True)
    is_active = models.BooleanField(default=True, verbose_name="启用")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "供应商账号"
        verbose_name_plural = "供应商账号"
        ordering = ["company_name"]

    def __str__(self):
        return self.company_name


class ClothOrder(ClothCatalogMixin, ProgressStageMixin, models.Model):
    """服装订单模型 - 根据Excel表格结构设计"""
    
    ORDER_TYPE_CHOICES = [
        ('bulk', '大货订单'),
        ('bulk_print', '大货-印花'),
        ('sample', '样品订单'),
        ('other', '其他'),
    ]
    
    PAYMENT_METHOD_CHOICES = [
        ('before_delivery', '货前'),
        ('after_delivery', '货后'),
        ('partial', '部分支付'),
    ]
    
    PAYMENT_STATUS_CHOICES = [
        ('paid', '已付款'),
        ('unpaid', '未付款'),
        ('partial', '部分支付'),
    ]
    
    OVERDUE_STATUS_CHOICES = [
        ('overdue', '逾期'),
        ('not_overdue', '未逾期'),
    ]
    
    INVOICE_STATUS_CHOICES = [
        ('invoiced', '已开票'),
        ('not_invoiced', '未开票'),
    ]

    YES_NO_CHOICES = [
        ('yes', '是'),
        ('no', '否'),
    ]
    
    CERTIFICATE_TYPE_CHOICES = [
        ('grs', 'GRS证书'),
        ('oeko', 'OEKO-TEX证书'),
        ('other', '其他证书'),
        ('none', '无证书'),
    ]

    ORDER_STATUS_CHOICES = [
        ('active', '正常'),
        ('cancelled', '已取消'),
    ]
    
    TEXTILE_TYPE_CHOICES = [
        ("woven", "梭织布"),
        ("knit", "针织布"),
    ]
    
    # 基本信息
    order_status = models.CharField(
        max_length=20,
        choices=ORDER_STATUS_CHOICES,
        default='active',
        verbose_name='订单状态',
        db_index=True,
    )
    progress_stages = models.TextField(
        verbose_name="进度阶段",
        blank=True,
        default='["客户下单","通知供应商","大货样","批色","查布","发货","待收款","已完成"]',
    )
    progress_current = models.IntegerField(verbose_name="当前进度", default=0)
    serial_number = models.IntegerField(verbose_name='序号', unique=True, null=True, blank=True)
    order_date = models.DateField(verbose_name='下单日期', null=True, blank=True)
    customer = models.CharField(max_length=100, verbose_name='客户', blank=True, db_index=True)
    order_follower = models.CharField(max_length=50, verbose_name='跟单员', blank=True)
    order_type = models.CharField(max_length=20, choices=ORDER_TYPE_CHOICES, verbose_name='订单类型', blank=True)
    order_number = models.CharField(max_length=200, verbose_name='订单号', blank=True)
    style_number = models.TextField(verbose_name='款号', blank=True)
    specification = models.CharField(max_length=100, verbose_name='规格', blank=True)
    
    # 布料信息
    cloth_type = models.CharField(max_length=100, verbose_name='布种', blank=True)
    textile_type = models.CharField(max_length=50, choices=TEXTILE_TYPE_CHOICES, verbose_name='纺织类型', blank=True, default="woven")
    color = models.CharField(max_length=50, verbose_name='颜色', blank=True)
    color_code = models.CharField(max_length=50, verbose_name='色号', blank=True)
    composition = models.CharField(max_length=100, verbose_name='成份', blank=True)
    width = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='门幅', null=True, blank=True)
    weight = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='克重', null=True, blank=True)
    processing_type = models.CharField(max_length=50, verbose_name='加工别', blank=True)
    
    # 订单数量与价格
    order_quantity = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='订单数量', null=True, blank=True)
    quantity_unit = models.CharField(max_length=20, verbose_name='单位', blank=True)
    price = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='价格', null=True, blank=True)
    price_unit = models.CharField(max_length=20, verbose_name='价格单位', blank=True)
    small_vat_fee = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='小缸费', null=True, blank=True)
    
    # 交期与进度
    customer_delivery_date = models.DateField(verbose_name='客人要求交期', null=True, blank=True)
    bulk_progress_tracking = models.TextField(verbose_name='大货进度跟踪', blank=True)
    
    # 财务信息 - 成品出货对账
    finished_product_total_amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='成品出货对账总金额', null=True, blank=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, verbose_name='付款方式', blank=True)
    payment_period = models.IntegerField(verbose_name='账期/天', null=True, blank=True)
    reconciliation_date = models.DateField(verbose_name='对账时间', null=True, blank=True)
    payment_date = models.DateField(verbose_name='货款支付时间', null=True, blank=True)
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, verbose_name='是否付款', blank=True)
    overdue_status = models.CharField(max_length=20, choices=OVERDUE_STATUS_CHOICES, verbose_name='是否逾期', blank=True)
    invoice_status = models.CharField(max_length=20, choices=INVOICE_STATUS_CHOICES, verbose_name='是否开票', blank=True)
    certificate_status = models.CharField(max_length=20, choices=INVOICE_STATUS_CHOICES, verbose_name='是否开证', blank=True)
    certificate_type = models.CharField(max_length=20, choices=CERTIFICATE_TYPE_CHOICES, verbose_name='证书类型', blank=True)
    
    # 证书时间
    latest_certificate_date = models.DateField(verbose_name='最迟开证时间', null=True, blank=True)
    actual_operation_date = models.DateField(verbose_name='实际操作时间', null=True, blank=True)
    actual_certificate_date = models.DateField(verbose_name='实际开证时间', null=True, blank=True)
    
    # 供应商信息
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="分配供应商",
    )
    finished_product_supplier = models.CharField(max_length=100, verbose_name='成品供应商', blank=True, db_index=True)
    booth = models.CharField(max_length=100, verbose_name='档口', blank=True)
    supplier_code = models.CharField(max_length=50, verbose_name='编号', blank=True)
    contract_number = models.CharField(max_length=100, verbose_name='合同号', blank=True)
    
    # 供应商财务信息
    supplier_invoice_status = models.CharField(max_length=20, choices=INVOICE_STATUS_CHOICES, verbose_name='供应商是否开票', blank=True)
    supplier_certificate_status = models.CharField(max_length=20, choices=INVOICE_STATUS_CHOICES, verbose_name='供应商是否开证书', blank=True)
    supplier_payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, verbose_name='供应商付款方式', blank=True)
    supplier_reconciliation_date = models.DateField(verbose_name='供应商对账时间', null=True, blank=True)
    supplier_payment_date = models.DateField(verbose_name='供应商货款支付时间', null=True, blank=True)
    
    # 成本与付款（*已付款：汇总表填 是/否，非金额）
    paid_amount = models.CharField(
        max_length=10,
        choices=YES_NO_CHOICES,
        verbose_name='已付款',
        blank=True,
        default='',
    )
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='总金额', null=True, blank=True)
    finished_product_cost_price = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='成品成本价格', null=True, blank=True)
    cost_price_unit = models.CharField(max_length=20, verbose_name='成本价格单位', blank=True)
    
    # 出货信息
    total_shipment_quantity = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='出货总数量', null=True, blank=True)
    shipment_quantity_unit = models.CharField(max_length=20, verbose_name='出货数量单位', blank=True)
    
    # 成品出货时间与数量（多批次）
    finished_product_shipment_date_1 = models.DateField(verbose_name='成品出货时间1', null=True, blank=True)
    finished_product_shipment_quantity_1 = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='成品出货数量1', null=True, blank=True)
    finished_product_shipment_date_2 = models.DateField(verbose_name='成品出货时间2', null=True, blank=True)
    finished_product_shipment_quantity_2 = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='成品出货数量2', null=True, blank=True)
    finished_product_shipment_date_3 = models.DateField(verbose_name='成品出货时间3', null=True, blank=True)
    finished_product_shipment_quantity_3 = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='成品出货数量3', null=True, blank=True)
    finished_product_shipment_date_4 = models.DateField(verbose_name='成品出货时间4', null=True, blank=True)
    finished_product_shipment_quantity_4 = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='成品出货数量4', null=True, blank=True)
    finished_product_shipment_date_5 = models.DateField(verbose_name='成品出货时间5', null=True, blank=True)
    finished_product_shipment_quantity_5 = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='成品出货数量5', null=True, blank=True)
    
    # 备注
    address = models.TextField(verbose_name='地址', blank=True, default='')
    supplier_paid = models.BooleanField(verbose_name='已付款给供应商', default=False)
    supplier_shipped = models.BooleanField(verbose_name='供应商已出货', default=False)
    remark = models.TextField(verbose_name='备注', blank=True, default='')
    supplier_remark = models.TextField(verbose_name='备注给供应商', blank=True, default='')

    # 时间戳
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    
    
    class Meta:
        verbose_name = '服装订单'
        verbose_name_plural = '服装订单'
        ordering = ['-order_date', '-created_at']
        indexes = [
            models.Index(fields=['serial_number']),
            models.Index(fields=['customer']),
            models.Index(fields=['order_date']),
            models.Index(fields=['payment_status']),
        ]
    
    def __str__(self):
        return f"{self.serial_number} - {self.customer} - {self.cloth_type}"
    
    def calculate_total_value(self):
        """计算订单总价值"""
        if self.order_quantity and self.price:
            return self.order_quantity * self.price
        return 0
    
    def get_profit(self):
        """计算利润（从 Shipment 实时计算）"""
        rev = self.computed_finished_product_total_amount
        cost = self.computed_total_amount
        if rev and cost:
            return rev - cost
        return 0
    
    def get_profit_margin(self):
        """计算利润率（从 Shipment 实时计算）"""
        revenue = float(self.computed_finished_product_total_amount or 0)
        if revenue > 0:
            profit = float(self.get_profit() or 0)
            return round((profit / revenue) * 100, 1)
        return 0
    


    @property
    def progress_percent(self):
        import json
        try:
            stages = json.loads(self.progress_stages) if self.progress_stages else []
            total = len(stages)
            if total > 0:
                cur = min(self.progress_current, total - 1)
                return int((cur + 1) / total * 100)
        except Exception:
            pass
        return 0
    @property
    def stage_list(self):
        import json
        try:
            return json.loads(self.progress_stages) if self.progress_stages else []
        except Exception:
            return []

    def set_progress_stage(self, stage_name, save=True):
        """将订单进度调整为指定阶段（仅当该阶段存在于进度阶段列表中时生效）"""
        import json
        try:
            stages = json.loads(self.progress_stages) if self.progress_stages else []
            if stage_name in stages:
                index = stages.index(stage_name)
                if self.progress_current != index:
                    self.progress_current = index
                    if save:
                        self.save(update_fields=["progress_current"])
                    return True
        except Exception:
            pass
        return False

    @property
    def is_overdue(self):
        """检查是否逾期（货款支付时间空且出货超过账期）"""
        if self.payment_date:
            return False
        ship_date = self.finished_product_shipment_date_1
        period = self.payment_period
        if ship_date and period:
            from datetime import date
            days_past = (date.today() - ship_date).days
            if days_past > period:
                return True
        return False
    
    @property
    def is_paid(self):
        """检查是否已付款"""
        return self.payment_status == 'paid'
    
    @property
    def has_certificate(self):
        """检查是否有证书"""
        return self.certificate_status == 'invoiced'


# ---------------------------------------------------------------------------
# 库存

    def total_shipment_from_shipments(self):
        """从 Shipment 记录汇总出货总数量"""
        return self.shipments.filter(is_deleted=False).aggregate(total=models.Sum('quantity'))['total'] or 0

    def has_shipments(self):
        """是否有出货记录"""
        return self.shipments.exists()

    @property
    def computed_total_amount(self):
        """从出货记录实时计算总金额，不依赖 total_amount 字段"""
        cost = float(self.finished_product_cost_price or 0)
        # 如果 queryset 已注解 _ship_qty，直接使用，避免额外查询
        if hasattr(self, "_ship_qty") and self._ship_qty is not None:
            qty = float(self._ship_qty)
        else:
            qty = float(self.total_shipment_from_shipments() or 0)
        return round(cost * qty, 2)

    @property
    def computed_finished_product_total_amount(self):
        """从出货记录实时计算成品出货对账总金额"""
        price = float(self.price or 0)
        if hasattr(self, "_ship_qty") and self._ship_qty is not None:
            qty = float(self._ship_qty)
        else:
            qty = float(self.total_shipment_from_shipments() or 0)
        return round(price * qty, 2)

# ---------------------------------------------------------------------------

class InventoryItem(ClothCatalogMixin, models.Model):
    """布料库存 —— 字段对应库存 Excel"""

    # 编号
    serial_no      = models.IntegerField(verbose_name='序号', null=True, blank=True)
    cloth_type_id  = models.IntegerField(verbose_name='布种类型ID', null=True, blank=True)
    unique_id      = models.CharField(max_length=200, verbose_name='唯一标识', blank=True, db_index=True)

    # 布料信息
    cloth_name     = models.CharField(max_length=200, verbose_name='布种/加工别', blank=True, db_index=True)
    color          = models.CharField(max_length=100, verbose_name='颜色/COLOR', blank=True)
    composition_en = models.CharField(max_length=200, verbose_name='COMPOSITION', blank=True)
    composition_cn = models.CharField(max_length=200, verbose_name='成份', blank=True)
    specification  = models.CharField(max_length=200, verbose_name='规格/SPECIFICATION', blank=True)
    finishing_en   = models.CharField(max_length=200, verbose_name='FINISHING', blank=True)
    finishing_cn   = models.CharField(max_length=200, verbose_name='整理', blank=True)
    width          = models.DecimalField(max_digits=8, decimal_places=2, verbose_name='幅宽(英寸)', null=True, blank=True)
    weight         = models.DecimalField(max_digits=8, decimal_places=2, verbose_name='克重(g/㎡)', null=True, blank=True)
    sides          = models.CharField(max_length=50,  verbose_name='正反面', blank=True)

    # 客户/用途
    customer       = models.CharField(max_length=100, verbose_name='客户/CLIENT', blank=True, db_index=True)
    usage          = models.CharField(max_length=100, verbose_name='用途/USE', blank=True)

    # 生产信息
    bath_no        = models.CharField(max_length=100, verbose_name='生产缸号', blank=True)
    position       = models.CharField(max_length=100, verbose_name='存放位置', blank=True)
    remark         = models.TextField(verbose_name='备注/REMARK', blank=True)

    # 胚布信息
    grey_fabric_no    = models.CharField(max_length=100, verbose_name='胚布编号', blank=True)
    grey_fabric_price = models.CharField(max_length=50,  verbose_name='胚布价格', blank=True)
    grey_fabric_source = models.CharField(max_length=100, verbose_name='胚布来源', blank=True)
    grey_fabric_date = models.CharField(max_length=50,  verbose_name='调胚时间', blank=True)

    # 价格
    finished_price = models.CharField(max_length=50, verbose_name='成品价格', blank=True)

    # 库存数量
    quantity       = models.IntegerField(verbose_name='库存数量', default=0,
                                         validators=[MinValueValidator(0)])

    # 时间戳
    created_at     = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at     = models.DateTimeField(auto_now=True,     verbose_name='更新时间')
    
    
    class Meta:
        verbose_name        = '库存'
        verbose_name_plural = '库存'
        ordering            = ['serial_no']
        indexes             = [
            models.Index(fields=['cloth_name']),
            models.Index(fields=['customer']),
            models.Index(fields=['quantity']),
        ]

    def __str__(self):
        return f"{self.cloth_name} {self.color} ({self.quantity}码)"


class InventoryLog(ClothCatalogMixin, models.Model):
    """库存变动记录"""

    LOG_TYPE_CHOICES = [
        ('in',     '入库'),
        ('out',    '出库'),
        ('adjust', '调整'),
    ]

    item = models.ForeignKey(
    InventoryItem, 
    on_delete=models.SET_NULL,
    related_name='logs',
    null=True, 
    blank=True
    )
    log_type  = models.CharField(max_length=10, choices=LOG_TYPE_CHOICES, verbose_name='变动类型')
    quantity  = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='变动数量')
    remark    = models.TextField(verbose_name='备注', blank=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='记录时间')
    created_by = models.CharField(max_length=50, verbose_name='操作人', blank=True)
    class Meta:
        verbose_name        = '库存记录'
        verbose_name_plural = '库存记录'
        ordering            = ['-created_at']

    def __str__(self):
        return f"{self.item} {self.get_log_type_display()} {self.quantity}"

class ClothCatalog(models.Model):
    """布种编号表 - 从 Excel 导入的布种目录"""
    cloth_code = models.CharField(max_length=50, verbose_name="布种编号", db_index=True, blank=True)
    cloth_name = models.CharField(max_length=200, verbose_name="材料名称", db_index=True, blank=True)
    cloth_type = models.CharField(max_length=100, verbose_name="布种类型", blank=True)
    customer = models.CharField(max_length=100, verbose_name="客户", blank=True)
    composition_cn = models.CharField(max_length=200, verbose_name="成份（中文）", blank=True)
    composition_en = models.CharField(max_length=200, verbose_name="成份（英文）", blank=True)
    width = models.CharField(max_length=50, verbose_name="可裁门幅", blank=True)
    weight = models.CharField(max_length=50, verbose_name="克重GSM", blank=True)
    specification = models.CharField(max_length=200, verbose_name="规格", blank=True)
    density = models.CharField(max_length=200, verbose_name="密度", blank=True)
    process_cn = models.TextField(verbose_name="加工工艺（中文）", blank=True)
    process_en = models.TextField(verbose_name="加工工艺（英文）", blank=True)
    remark = models.TextField(verbose_name="备注", blank=True)
    supplier1 = models.CharField(max_length=100, verbose_name="一级供应商", blank=True)
    supplier1_code = models.CharField(max_length=100, verbose_name="供应商编号", blank=True)
    supplier2 = models.CharField(max_length=100, verbose_name="二级供应商", blank=True)

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    
    class Meta:
        verbose_name = "布种编号"
        verbose_name_plural = "布种编号"
        ordering = ["cloth_code"]
        indexes = [
            models.Index(fields=["cloth_code"]),
            models.Index(fields=["cloth_name"]),
            models.Index(fields=["cloth_type"]),
        ]

    def __str__(self):
        return f"{self.cloth_code} - {self.cloth_name}"





class Notification(models.Model):
    """站内消息通知"""
    recipient = models.ForeignKey(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name="接收用户",
    )
    title = models.CharField(max_length=200, verbose_name="标题")
    message = models.TextField(verbose_name="消息内容", blank=True)
    link = models.CharField(max_length=500, verbose_name="链接", blank=True)
    is_read = models.BooleanField(default=False, verbose_name="是否已读")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    class Meta:
        ordering = ["-created_at"]
        verbose_name = "消息通知"
        verbose_name_plural = "消息通知"

    def __str__(self):
        return self.title

    def link_for(self, user):
        """供应商查看时，将管理端订单链接改写为供应商端订单链接，避免越权进入管理页"""
        if self.link and hasattr(user, "supplier_profile") and not user.is_staff:
            m = re.match(r"^/orders/(\d+)/", self.link)
            if m:
                return f"/supplier/orders/{m.group(1)}/"
        return self.link


def notify_user(recipient, title, message="", link=""):
    """为单个用户创建通知"""
    return Notification.objects.create(
        recipient=recipient,
        title=title,
        message=message,
        link=link,
    )


def notify_all_staff(title, message="", link=""):
    """为所有活跃管理员创建通知"""
    from django.contrib.auth.models import User
    staff_users = User.objects.filter(is_staff=True, is_active=True)
    notifications = []
    for user in staff_users:
        notifications.append(Notification(
            recipient=user,
            title=title,
            message=message,
            link=link,
        ))
    if notifications:
        Notification.objects.bulk_create(notifications)


class Shipment(models.Model):
    """出货记录 — 替代 ClothOrder 上硬编码的 5 个批次字段"""
    order = models.ForeignKey(
        ClothOrder,
        on_delete=models.CASCADE,
        related_name='shipments',
        verbose_name='所属订单',
    )
    batch_number = models.IntegerField(verbose_name='批次号', default=1)
    date = models.DateField(verbose_name='出货日期', null=True, blank=True)
    quantity = models.DecimalField(
        max_digits=15, decimal_places=2, verbose_name='出货数量',
        null=True, blank=True,
    )
    created_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='登记人',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    is_deleted = models.BooleanField(default=False, verbose_name='已删除')

    class Meta:
        ordering = ['order', 'batch_number']
        verbose_name = '出货记录'
        verbose_name_plural = '出货记录'
        indexes = [
            models.Index(fields=['order', 'batch_number']),
        ]

    def __str__(self):
        return f'{self.order.serial_number} - 批次{self.batch_number}'


import re


class FabricQuotation(models.Model):
    """报价表"""

    row_index = models.IntegerField(verbose_name='Excel行号', null=True, blank=True)
    date_sent = models.DateField(verbose_name='导入时间', null=True, blank=True)
    supplier_name = models.CharField(max_length=200, verbose_name='供应商', blank=True, db_index=True)
    supplier_contact = models.TextField(verbose_name='联系人/邮箱', blank=True)
    gfg_dev_no = models.CharField(max_length=100, verbose_name='GFG开发号', blank=True)
    preferred_material = models.CharField(max_length=100, verbose_name='附加备注', blank=True)
    color_card = models.CharField(max_length=50, verbose_name='备注', blank=True)
    article_no = models.CharField(max_length=200, verbose_name='面料编号', blank=True, db_index=True)
    composition = models.TextField(verbose_name='备注', blank=True)
    weight = models.CharField(max_length=50, verbose_name='备注', blank=True)
    cuttable_width = models.CharField(max_length=50, verbose_name='备注', blank=True)
    price_200m_text = models.TextField(verbose_name='200米价格原文', blank=True)
    price_200m = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='200米价格-素色', null=True, blank=True)
    price_200m_print = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='200米价格-印花', null=True, blank=True)
    price_200m_unit = models.CharField(max_length=10, verbose_name='价格单位', blank=True, default='M')
    regular_mcq = models.CharField(max_length=100, verbose_name='报价有效期', blank=True)
    price_regular_text = models.TextField(verbose_name='常规价格原文', blank=True)
    price_regular = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='常规价格-素色', null=True, blank=True)
    price_regular_print = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='常规价格-印花', null=True, blank=True)
    price_regular_unit = models.CharField(max_length=10, verbose_name='常规价格单位', blank=True, default='M')
    lead_time = models.CharField(max_length=100, verbose_name='备注', blank=True)
    price_validity = models.CharField(max_length=100, verbose_name='报价有效期', blank=True)
    yarn_count = models.CharField(max_length=100, verbose_name='备注', blank=True)
    density_gauge = models.CharField(max_length=100, verbose_name='密度/针数', blank=True)
    remark = models.TextField(verbose_name='备注', blank=True)
    quoted_to = models.CharField(max_length=200, verbose_name='报价给', blank=True)
    extra_remark = models.TextField(verbose_name='附加备注', blank=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='导入时间')

    class Meta:
        verbose_name = '面料报价'
        verbose_name_plural = '面料报价'
        ordering = ['-date_sent', 'article_no']
        indexes = [
            models.Index(fields=['article_no']),
            models.Index(fields=['supplier_name']),
        ]

    def __str__(self):
        return f'{self.article_no or "?"} - {self.supplier_name} ({self.date_sent or "?"})'


