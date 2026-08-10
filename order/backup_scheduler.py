"""数据库自动备份定时任务（轻量线程实现，无第三方依赖）。"""
import logging
import os
import socket
import threading
import time

logger = logging.getLogger(__name__)


def _try_bind_lock():
    """通过绑定本地端口实现多进程互斥：同一容器内只有第一个 worker 能绑定成功。

    进程退出后端口自动释放，无需清理锁文件，避免残留锁。
    """
    try:
        port = int(os.environ.get("DB_BACKUP_LOCK_PORT", "8765"))
    except ValueError:
        port = 8765
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        return sock
    except OSError:
        try:
            sock.close()
        except Exception:
            pass
        return None


def _run_backup():
    from .db_backup import create_backup, prune_backups

    try:
        path, method = create_backup()
        pruned = prune_backups()
        logger.info(
            f"[自动备份] 备份完成：{path.name}（方式：{method}）"
            + (f"，自动清理旧备份 {pruned} 份" if pruned else "")
        )
    except Exception as exc:
        logger.error(f"[自动备份] 备份失败：{exc}", exc_info=True)


def _loop():
    # 启动后稍等，避免与容器启动流程（迁移等）竞争
    time.sleep(30)
    lock_sock = _try_bind_lock()
    if lock_sock is None:
        logger.info("[自动备份] 其他进程已在运行调度任务，跳过")
        return
    try:
        while True:
            from .db_backup import get_backup_config

            config = get_backup_config()
            if config["mode"] == "off":
                logger.info("[自动备份] 当前模式为关闭，跳过本次备份（每小时复查一次模式）")
                time.sleep(3600)
                continue
            _run_backup()
            hours = config["interval_hours"]
            time.sleep(max(hours, 0.1) * 3600)
    finally:
        try:
            lock_sock.close()
        except Exception:
            pass


def start_scheduler():
    """应用启动时调用；设置 DB_BACKUP_DISABLED=1 可关闭自动备份。"""
    if os.environ.get("DB_BACKUP_DISABLED") == "1":
        return None
    thread = threading.Thread(target=_loop, daemon=True, name="db-backup-scheduler")
    thread.start()
    return thread
