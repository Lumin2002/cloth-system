from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Exists, OuterRef

from order.models import ClothOrder, Shipment


class Command(BaseCommand):
    help = "将已有出货记录但进度尚未推进的有效订单，按订单类型推进到对应已出货阶段"

    @transaction.atomic
    def handle(self, *args, **options):
        has_ship = Shipment.objects.filter(
            order=OuterRef("pk"),
            is_deleted=False,
        )
        orders = (
            ClothOrder.objects.filter(order_status="active")
            .annotate(has_ship=Exists(has_ship))
            .filter(has_ship=True)
        )

        changed = 0
        for order in orders.iterator():
            before = order.progress_current
            if order.advance_to_shipped_stage(save=False) and order.progress_current != before:
                order.save(update_fields=["progress_current"])
                changed += 1

        self.stdout.write(self.style.SUCCESS(f"已推进 {changed} 条订单进度"))
