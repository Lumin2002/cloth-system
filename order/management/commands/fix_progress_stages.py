from django.core.management.base import BaseCommand
from order.models import ClothOrder
from order.forms import DEFAULT_STAGES_MAP

class Command(BaseCommand):
    help = "更新现有订单的进度阶段，匹配其订单类型"

    def handle(self, *args, **options):
        updated = 0
        for order in ClothOrder.objects.all():
            expected = DEFAULT_STAGES_MAP.get(order.order_type)
            if expected and order.progress_stages != expected:
                old_stages = order.progress_stages
                order.progress_stages = expected
                # 确保当前进度不超出新阶段列表
                import json
                new_len = len(json.loads(expected))
                if order.progress_current >= new_len:
                    order.progress_current = new_len - 1
                order.save(update_fields=["progress_stages", "progress_current"])
                updated += 1
                self.stdout.write(f"  #{order.pk} {order.order_type}: {old_stages[:30]}... \u2192 {expected[:30]}...")
        self.stdout.write(self.style.SUCCESS(f"\n已完成！共更新 {updated} 条订单"))