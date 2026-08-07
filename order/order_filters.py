"""订单列表筛选逻辑（列表页与批量操作共用，表头搜索模式）。"""
from datetime import datetime

from django.db.models import Q

from .models import ClothOrder


def filter_orders_queryset(params, queryset=None):
    """根据 GET/POST 参数字典筛选订单（各列独立模糊搜索）。"""
    qs = queryset if queryset is not None else ClothOrder.objects.all()

    # 全局关键字搜索（兼容旧链接）
    search = (params.get('search') or '').strip()
    if search:
        filters = (
            Q(customer__icontains=search)
            | Q(order_follower__icontains=search)
            | Q(cloth_type__icontains=search)
            | Q(color__icontains=search)
            | Q(order_number__icontains=search)
            | Q(style_number__icontains=search)
            | Q(finished_product_supplier__icontains=search)
            | Q(booth__icontains=search)
        )
        if search.isdigit():
            filters |= Q(serial_number=int(search))
        qs = qs.filter(filters)

    # 序号 / 订单号 / 款号
    serial = (params.get('serial') or '').strip()
    if serial:
        filters = Q(order_number__icontains=serial) | Q(style_number__icontains=serial)
        if serial.isdigit():
            filters |= Q(serial_number=int(serial))
        qs = qs.filter(filters)

    # 客户 / 跟单员（模糊搜索）
    if customer := (params.get('customer') or '').strip():
        qs = qs.filter(Q(customer__icontains=customer) | Q(order_follower__icontains=customer))

    # 布种 / 颜色（模糊搜索）
    if cloth := (params.get('cloth') or '').strip():
        qs = qs.filter(Q(cloth_type__icontains=cloth) | Q(color__icontains=cloth))

    # 供应商 / 档口（模糊搜索）
    if supplier := (params.get('supplier') or '').strip():
        qs = qs.filter(
            Q(finished_product_supplier__icontains=supplier)
            | Q(booth__icontains=supplier)
        )

    if payment := (params.get('payment_status') or '').strip():
        qs = qs.filter(payment_status=payment)
    if overdue := (params.get('overdue_status') or '').strip():
        qs = qs.filter(overdue_status=overdue)
    if order_type := (params.get('order_type') or '').strip():
        qs = qs.filter(order_type=order_type)
    if paid := (params.get('supplier_paid') or '').strip():
        if paid in ('yes', 'True', 'true', '1'):
            qs = qs.filter(supplier_paid=True)
        elif paid in ('no', 'False', 'false', '0'):
            qs = qs.filter(supplier_paid=False)

    if status := (params.get('order_status') or '').strip():
        if status in ('active', 'cancelled'):
            qs = qs.filter(order_status=status)

    for param, lookup in (('start_date', 'order_date__gte'), ('end_date', 'order_date__lte')):
        raw = (params.get(param) or '').strip()
        if raw:
            try:
                qs = qs.filter(**{lookup: datetime.strptime(raw, '%Y-%m-%d').date()})
            except ValueError:
                pass

    return qs.order_by('-order_date', '-serial_number', '-created_at')
