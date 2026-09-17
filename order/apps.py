from django.apps import AppConfig


class OrderConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'order'

    def ready(self):
        # 数据库自动备份定时任务（轻量线程，多进程下由文件锁保证只跑一份）
        try:
            from .backup_scheduler import start_scheduler

            start_scheduler()
        except Exception:
            # 启动阶段不应因调度器异常阻断应用
            import logging

            logging.getLogger(__name__).exception("自动备份调度器启动失败")

        # 本地 Excel 双向同步
        try:
            from django.db.models.signals import post_delete, post_save

            from . import orders_sync
            from .models import ClothOrder, Shipment

            orders_sync.start_sync_watcher()
            post_save.connect(
                orders_sync.on_order_changed,
                sender=ClothOrder,
                dispatch_uid="order_sync_clothorder_post_save",
            )
            post_delete.connect(
                orders_sync.on_order_changed,
                sender=ClothOrder,
                dispatch_uid="order_sync_clothorder_post_delete",
            )
            post_save.connect(
                orders_sync.on_order_changed,
                sender=Shipment,
                dispatch_uid="order_sync_shipment_post_save",
            )
            post_delete.connect(
                orders_sync.on_order_changed,
                sender=Shipment,
                dispatch_uid="order_sync_shipment_post_delete",
            )
        except Exception:
            import logging

            logging.getLogger(__name__).exception("本地 Excel 同步启动失败")
