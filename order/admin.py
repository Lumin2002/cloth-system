from django.contrib import admin
from django.utils.html import format_html
from .models import ClothOrder, Customer
from .models import InventoryLog


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "code",
        "contact_person",
        "phone",
        "email",
        "is_active",
        "created_at",
    ]
    list_filter = ["is_active"]
    search_fields = ["name", "code", "contact_person", "phone", "email"]
    ordering = ["name"]
    list_per_page = 50

@admin.register(ClothOrder)
class ClothOrderAdmin(admin.ModelAdmin):
    """服装订单管理"""
    
    list_display = [
        'serial_number', 'customer', 'cloth_type', 'order_date', 
        'order_quantity', 'price', 'finished_product_total_amount',
        'payment_status_display', 'overdue_status_display', 
        'invoice_status_display', 'custom_actions'
    ]
    
    list_filter = [
        'customer', 'order_type', 'payment_status', 'overdue_status',
        'invoice_status', 'certificate_status', 'order_date',
        'finished_product_supplier'
    ]
    
    search_fields = [
        'serial_number', 'customer', 'cloth_type', 'order_number',
        'style_number', 'finished_product_supplier', 'contract_number'
    ]
    
    date_hierarchy = 'order_date'
    ordering = ['-order_date', '-created_at']
    
    fieldsets = (
        ('基本信息', {
            'fields': (
                'serial_number', 'order_date', 'customer', 'order_follower',
                'order_type', 'order_number', 'style_number', 'specification'
            )
        }),
        ('布料信息', {
            'fields': (
                'composition', 'width', 'weight', 'processing_type'
            )
        }),
        ('订单数量与价格', {
            'fields': (
                'order_quantity', 'quantity_unit', 'price', 'price_unit',
                'small_vat_fee', 'customer_delivery_date', 'bulk_progress_tracking'
            )
        }),
        ('财务信息 - 成品出货对账', {
            'fields': (
                'finished_product_total_amount', 'payment_method', 'payment_period',
                'reconciliation_date', 'payment_date', 'payment_status',
                'overdue_status', 'invoice_status', 'certificate_status',
                'certificate_type'
            )
        }),
        ('证书时间', {
            'fields': (
                'latest_certificate_date', 'actual_operation_date',
                'actual_certificate_date'
            )
        }),
        ('供应商信息', {
            'fields': (
                'finished_product_supplier', 'booth', 'supplier_code',
                'contract_number', 'supplier_invoice_status',
                'supplier_certificate_status', 'supplier_payment_method',
                'supplier_reconciliation_date', 'supplier_payment_date'
            )
        }),
        ('成本与付款', {
            'fields': (
                'paid_amount', 'total_amount', 'finished_product_cost_price',
                'cost_price_unit'
            )
        }),
        ('出货信息', {
            'fields': (
                'total_shipment_quantity', 'shipment_quantity_unit',
            )
        }),
        ('成品出货批次', {
            'fields': (
                ('finished_product_shipment_date_1', 'finished_product_shipment_quantity_1'),
                ('finished_product_shipment_date_2', 'finished_product_shipment_quantity_2'),
                ('finished_product_shipment_date_3', 'finished_product_shipment_quantity_3'),
                ('finished_product_shipment_date_4', 'finished_product_shipment_quantity_4'),
                ('finished_product_shipment_date_5', 'finished_product_shipment_quantity_5'),
            ),
            'classes': ('collapse',)
        }),
        ('时间戳', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ['created_at', 'updated_at']
    
    def payment_status_display(self, obj):
        """支付状态显示"""
        colors = {
            'paid': 'success',
            'unpaid': 'danger',
            'partial': 'warning'
        }
        return format_html(
            '<span class="badge bg-{}">{}</span>',
            colors.get(obj.payment_status, 'secondary'),
            obj.get_payment_status_display()
        )
    payment_status_display.short_description = '支付状态'
    
    def overdue_status_display(self, obj):
        """逾期状态显示"""
        colors = {
            'overdue': 'danger',
            'not_overdue': 'success'
        }
        return format_html(
            '<span class="badge bg-{}">{}</span>',
            colors.get(obj.overdue_status, 'secondary'),
            obj.get_overdue_status_display()
        )
    overdue_status_display.short_description = '逾期状态'
    
    def invoice_status_display(self, obj):
        """开票状态显示"""
        colors = {
            'invoiced': 'success',
            'not_invoiced': 'warning'
        }
        return format_html(
            '<span class="badge bg-{}">{}</span>',
            colors.get(obj.invoice_status, 'secondary'),
            obj.get_invoice_status_display()
        )
    invoice_status_display.short_description = '开票状态'
    
    def custom_actions(self, obj):
        """操作按钮"""
        return format_html(
            '<a href="/admin/order/clothorder/{}/change/" class="btn btn-sm btn-warning">编辑</a>',
            obj.id
        )
    custom_actions.short_description = '操作'
    
    def get_queryset(self, request):
        """优化查询"""
        return super().get_queryset(request).select_related()
    
    def save_model(self, request, obj, form, change):
        """保存模型前的处理"""
        # 自动计算一些字段
        if not obj.serial_number:
            # 自动生成序号
            last_order = ClothOrder.objects.order_by('-serial_number').first()
            obj.serial_number = (last_order.serial_number + 1) if last_order else 1
        
        super().save_model(request, obj, form, change)
    
    class Media:
        css = {
            'all': ('admin/css/custom.css',)
        }


from .models import InventoryItem, ClothCatalog, Supplier


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = [
        'serial_no', 'cloth_name', 'color', 'specification',
        'width', 'weight', 'customer', 'position', 'quantity',
    ]
    list_filter = ['customer', 'position']
    search_fields = [
        'cloth_name', 'color', 'color_code', 'unique_id',
        'customer', 'composition_cn', 'specification',
    ]
    ordering = ['serial_no']
    list_per_page = 50

@admin.register(InventoryLog)
class InventoryLogAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'item', 'log_type', 'quantity', 'created_by', 
        'remark', 'created_at'
    ]
    list_filter = ['log_type', 'created_at']
    search_fields = [
        'item__cloth_name', 'created_by', 'remark', 
        'item__serial_no'
    ]
    readonly_fields = ['created_at']
    ordering = ['-created_at']
    list_per_page = 50

@admin.register(ClothCatalog)
class ClothCatalogAdmin(admin.ModelAdmin):
    list_display = ['cloth_code', 'cloth_name', 'cloth_type', 'customer', 'supplier1', 'created_at']
    list_filter = ['cloth_type', 'customer']
    search_fields = ['cloth_code', 'cloth_name', 'cloth_type', 'customer']
    ordering = ['cloth_code']
    list_per_page = 50

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ['company_name', 'contact_person', 'phone', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['company_name', 'contact_person']
    ordering = ['company_name']


from order.models import FabricQuotation

@admin.register(FabricQuotation)
class FabricQuotationAdmin(admin.ModelAdmin):
    list_display = ['article_no', 'supplier_name', 'date_sent', 'price_200m', 'price_regular', 'preferred_material']
    list_filter = ['supplier_name', 'preferred_material', 'color_card']
    search_fields = ['article_no', 'composition', 'supplier_name']
    ordering = ['-date_sent', 'article_no']
