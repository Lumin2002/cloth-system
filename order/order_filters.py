"""订单列表筛选逻辑（列表页与批量操作共用）。"""
from datetime import datetime

from django.db.models import Q

from .models import ClothOrder


def filter_orders_queryset(params, queryset=None):
    """根据 GET/POST 参数字典筛选订单。"""
    qs = queryset if queryset is not None else ClothOrder.objects.all()

    search = (params.get('search') or '').strip()
    if search:
        filters = (
            Q(customer__icontains=search)
            | Q(cloth_type__icontains=search)
            | Q(order_number__icontains=search)
            | Q(style_number__icontains=search)
            | Q(finished_product_supplier__icontains=search)
        )
        try:
            filters |= Q(serial_number=int(search))
        except ValueError:
            pass
        qs = qs.filter(filters)

    if customer := (params.get('customer') or '').strip():
        qs = qs.filter(customer=customer)
    if payment := (params.get('payment_status') or '').strip():
        qs = qs.filter(payment_status=payment)
    if overdue := (params.get('overdue_status') or '').strip():
        qs = qs.filter(overdue_status=overdue)
    if order_type := (params.get('order_type') or '').strip():
        qs = qs.filter(order_type=order_type)
    if supplier := (params.get('supplier') or '').strip():
        qs = qs.filter(finished_product_supplier=supplier)
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
