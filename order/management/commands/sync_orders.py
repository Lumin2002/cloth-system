import json

from django.core.management.base import BaseCommand

from order.orders_sync import (
    export_sync_file,
    get_sync_file_path,
    import_sync_file_if_changed,
    _read_state,
)


class Command(BaseCommand):
    help = "管理本地 Excel 与订单数据库的双向同步"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--export-now",
            action="store_true",
            help="立即把数据库订单全量导出到同步 Excel",
        )
        group.add_argument(
            "--import-now",
            action="store_true",
            help="立即把同步 Excel 的内容导入数据库（覆盖数据库当前值）",
        )
        group.add_argument(
            "--status",
            action="store_true",
            help="查看同步文件和最近同步状态",
        )

    def handle(self, *args, **options):
        path = get_sync_file_path()
        if options["export_now"]:
            export_sync_file()
            self.stdout.write(self.style.SUCCESS(f"已导出到：{path}"))
        elif options["import_now"]:
            result = import_sync_file_if_changed(force=True)
            if result is None:
                self.stdout.write(self.style.WARNING(f"同步文件不存在：{path}"))
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"导入完成：新增 {result.imported} 条，"
                        f"更新 {result.updated} 条，失败 {result.errors} 条"
                    )
                )
        elif options["status"]:
            state = _read_state()
            self.stdout.write(f"同步文件：{path}")
            self.stdout.write(f"状态：{json.dumps(state, ensure_ascii=False, indent=2)}")
        else:
            self.print_help("manage.py", "sync_orders")
