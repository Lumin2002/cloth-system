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
