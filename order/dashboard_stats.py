"""仪表板按月统计。"""
from datetime import date, timedelta

from django.db.models import Count, Sum, F
from django.db.models.functions import TruncMonth


def _last_month_starts(count: int = 12) -> list[date]:
    """最近 count 个自然月（每月 1 日），从旧到新。"""
    today = date.today()
    y, m = today.year, today.month
    months: list[date] = []
    for _ in range(count):
        months.append(date(y, m, 1))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()
    return months


def _float(value) -> float:
    if value is None:
        return 0.0
    return float(value)



def _annotate_computed_totals(queryset):
    """用 Shipment 表注解 computed_total 和 computed_revenue，替代 total_amount 字段"""
    from .models import Shipment
    from django.db.models import Subquery, OuterRef, Value
    from django.db.models.functions import Coalesce
    
    ship_total = Shipment.objects.filter(order=OuterRef("pk")).annotate(
        batch_total=Sum(F("quantity") * Coalesce(F("order__finished_product_cost_price"), Value(0)), output_field=FloatField())
    ).values("batch_total")[:1]
    
    ship_revenue = Shipment.objects.filter(order=OuterRef("pk")).annotate(
        batch_rev=Sum(F("quantity") * Coalesce(F("order__price"), Value(0)), output_field=FloatField())
    ).values("batch_rev")[:1]
    
    return queryset.annotate(
        _computed_total=Coalesce(Subquery(ship_total, output_field=FloatField()), Value(0)),
        _computed_revenue=Coalesce(Subquery(ship_revenue, output_field=FloatField()), Value(0)),
    )


def build_monthly_chart_data(queryset, months_count: int = 12) -> dict:
    """生成最近 N 个月的图表与表格数据。"""
    month_starts = _last_month_starts(months_count)
    if not month_starts:
        return {
            'labels': [],
            'orders': [],
            'revenue': [],
            'cost': [],
            'profit': [],
            'rows': [],
            'chart_months': 0,
        }

    range_start = month_starts[0]
    aggregated = (
        queryset.filter(order_date__gte=range_start, order_date__isnull=False)
        .annotate(month=TruncMonth('order_date'))
        .values('month')
        .annotate(
            order_count=Count('id'),
            revenue=Sum('finished_product_total_amount'),
            cost=Sum('total_amount'),
        )
    )
    by_key = {}
    for row in aggregated:
        if row['month']:
            by_key[row['month'].strftime('%Y-%m')] = row

    labels, orders, revenue, cost, profit, rows = [], [], [], [], [], []
    for month_start in month_starts:
        key = month_start.strftime('%Y-%m')
        row = by_key.get(key, {})
        rev = _float(row.get('revenue'))
        cst = _float(row.get('cost'))
        cnt = row.get('order_count') or 0
        prf = rev - cst

        labels.append(f'{month_start.year}年{month_start.month}月')
        orders.append(cnt)
        revenue.append(rev)
        cost.append(cst)
        profit.append(prf)
        rows.append({
            'label': labels[-1],
            'key': key,
            'order_count': cnt,
            'revenue': rev,
            'cost': cst,
            'profit': prf,
        })

    return {
        'labels': labels,
        'orders': orders,
        'revenue': revenue,
        'cost': cost,
        'profit': profit,
        'rows': rows,
        'chart_months': months_count,
    }


def build_month_compare(queryset) -> dict:
    """本月 vs 上月 对比。"""
    today = date.today()
    this_start = today.replace(day=1)
    last_end = this_start - timedelta(days=1)
    last_start = last_end.replace(day=1)

    def agg(start: date, end: date):
        qs = queryset.filter(order_date__gte=start, order_date__lte=end)
        data = qs.aggregate(
            order_count=Count('id'),
            revenue=Sum('finished_product_total_amount'),
            cost=Sum('total_amount'),
        )
        rev = _float(data['revenue'])
        cst = _float(data['cost'])
        return {
            'order_count': data['order_count'] or 0,
            'revenue': rev,
            'cost': cst,
            'profit': rev - cst,
        }

    this_month = agg(this_start, today)
    last_month = agg(last_start, last_end)

    def delta(cur, prev):
        if prev == 0:
            return None if cur == 0 else 100.0
        return (cur - prev) / prev * 100

    return {
        'this_label': f'{this_start.year}年{this_start.month}月',
        'last_label': f'{last_start.year}年{last_start.month}月',
        'this': this_month,
        'last': last_month,
        'order_delta': delta(this_month['order_count'], last_month['order_count']),
        'revenue_delta': delta(this_month['revenue'], last_month['revenue']),
        'profit_delta': delta(this_month['profit'], last_month['profit']),
    }
