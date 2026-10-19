from django.core.management.base import BaseCommand

from order.orders_sync import export_sync_file, get_sync_file_path


class Command(BaseCommand):
    help = "立即把全部订单写入本地 Excel（单向同步，只写不读）"

    def handle(self, *args, **options):
        self.stdout.write(f"目标文件：{get_sync_file_path()}")
        path = export_sync_file()
        self.stdout.write(self.style.SUCCESS(f"已同步到：{path}"))
