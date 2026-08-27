import json
import os
import subprocess
from time import time
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from order.captcha import CAPTCHA_SESSION_KEY
from order import service_monitor
from order import terminal
from order.middleware import SESSION_LAST_ACTIVITY_KEY
from order.models import ClothOrder, Notification, Supplier


@override_settings(
    MIDDLEWARE=[
        "django.middleware.security.SecurityMiddleware",
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
        "order.middleware.SessionTimeoutMiddleware",
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
    ],
    SUPPLIER_SESSION_IDLE_TIMEOUT_MINUTES=30,
    SUPERUSER_SESSION_IDLE_TIMEOUT_MINUTES=15,
    SESSION_TIMEOUT_WARNING_SECONDS=60,
)
class SessionTimeoutMiddlewareTests(TestCase):
    def setUp(self):
        self.supplier_user = User.objects.create_user(
            username="supplier_timeout",
            email="supplier@example.com",
            password="test-pass-123",
        )
        self.supplier = Supplier.objects.create(
            user=self.supplier_user,
            company_name="超时测试供应商",
        )
        self.admin_user = User.objects.create_superuser(
            username="admin_timeout",
            email="admin@example.com",
            password="test-pass-123",
        )

    def _expire_session(self, minutes_ago=31):
        session = self.client.session
        session[SESSION_LAST_ACTIVITY_KEY] = time() - minutes_ago * 60
        session.save()

    def test_supplier_session_expires_after_idle_timeout(self):
        self.client.force_login(self.supplier_user)
        self._expire_session()

        response = self.client.get(reverse("supplier_dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_supplier_active_session_stays_logged_in(self):
        self.client.force_login(self.supplier_user)
        session = self.client.session
        session[SESSION_LAST_ACTIVITY_KEY] = time()
        session.save()

        response = self.client.get(reverse("supplier_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertLessEqual(self.client.session.get_expiry_age(), 30 * 60 + 1)

    def test_superuser_session_expires_after_idle_timeout(self):
        self.client.force_login(self.admin_user)
        self._expire_session()

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_superuser_active_session_stays_logged_in(self):
        self.client.force_login(self.admin_user)
        session = self.client.session
        session[SESSION_LAST_ACTIVITY_KEY] = time()
        session.save()

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)

    def test_superuser_admin_request_redirects_to_admin_login(self):
        self.client.force_login(self.admin_user)
        self._expire_session()

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/admin/login/"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_background_poll_does_not_extend_session(self):
        self.client.force_login(self.supplier_user)
        old_ts = time() - 20 * 60
        session = self.client.session
        session[SESSION_LAST_ACTIVITY_KEY] = old_ts
        session.save()

        response = self.client.get("/notifications/unread-count/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session.get(SESSION_LAST_ACTIVITY_KEY), old_ts)

    def test_expired_background_poll_returns_401(self):
        self.client.force_login(self.supplier_user)
        self._expire_session()

        response = self.client.get("/notifications/unread-count/")

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_then_page_request_sets_session_activity_timestamp(self):
        session = self.client.session
        session[CAPTCHA_SESSION_KEY] = {"code": "TEST", "expires": time() + 300}
        session.save()
        self.client.post(
            reverse("home"),
            {
                "username": self.supplier_user.username,
                "password": "test-pass-123",
                "captcha": "test",
            },
        )
        self.client.get(reverse("supplier_dashboard"))
        self.assertIn(SESSION_LAST_ACTIVITY_KEY, self.client.session)


class ServiceMonitorTests(TestCase):
    def test_database_status_ok(self):
        cursor = MagicMock()
        cursor_cm = MagicMock()
        cursor_cm.__enter__.return_value = cursor
        conn = MagicMock()
        conn.vendor = "mysql"
        conn.cursor.return_value = cursor_cm

        with patch("order.service_monitor.connection", conn):
            result = service_monitor._database_status()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["engine"], "mysql")

    def test_database_status_error(self):
        conn = MagicMock()
        conn.vendor = "mysql"
        conn.cursor.side_effect = Exception("db down")

        with patch("order.service_monitor.connection", conn):
            result = service_monitor._database_status()

        self.assertEqual(result["status"], "error")

    @override_settings(REDIS_URL="")
    def test_redis_not_configured(self):
        result = service_monitor._redis_status()
        self.assertEqual(result["status"], "not_configured")

    @override_settings(REDIS_URL="redis://127.0.0.1:6379/0")
    def test_redis_status_ok(self):
        client = MagicMock()
        client.connection_pool.connection_kwargs = {}
        client.ping.return_value = True

        with patch("order.service_monitor.get_redis_connection", return_value=client), patch.object(
            service_monitor, "DJANGO_REDIS_AVAILABLE", True
        ):
            result = service_monitor._redis_status()

        self.assertEqual(result["status"], "ok")

    @override_settings(REDIS_URL="redis://127.0.0.1:6379/0")
    def test_redis_status_error(self):
        client = MagicMock()
        client.connection_pool.connection_kwargs = {}
        client.ping.side_effect = Exception("redis down")

        with patch("order.service_monitor.get_redis_connection", return_value=client), patch.object(
            service_monitor, "DJANGO_REDIS_AVAILABLE", True
        ):
            result = service_monitor._redis_status()

        self.assertEqual(result["status"], "error")

    @override_settings(NGINX_CHECK_URL="")
    def test_nginx_not_configured(self):
        result = service_monitor._nginx_status()
        self.assertEqual(result["status"], "not_configured")

    @override_settings(NGINX_CHECK_URL="http://127.0.0.1:8233/")
    def test_nginx_status_ok(self):
        resp = MagicMock()
        resp.status = 200
        resp_cm = MagicMock()
        resp_cm.__enter__.return_value = resp

        with patch("order.service_monitor.urllib.request.urlopen", return_value=resp_cm) as urlopen:
            result = service_monitor._nginx_status()

        self.assertEqual(result["status"], "ok")
        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")

    @override_settings(NGINX_CHECK_URL="https://your-nginx.example.com:40614")
    def test_nginx_https_uses_unverified_context(self):
        resp = MagicMock()
        resp.status = 200
        resp_cm = MagicMock()
        resp_cm.__enter__.return_value = resp

        with patch("order.service_monitor.urllib.request.urlopen", return_value=resp_cm) as urlopen:
            result = service_monitor._nginx_status()

        self.assertEqual(result["status"], "ok")
        self.assertIsNotNone(urlopen.call_args.kwargs.get("context"))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")

    @override_settings(NGINX_CHECK_URL="http://127.0.0.1:8233/")
    def test_nginx_status_error(self):
        with patch("order.service_monitor.urllib.request.urlopen", side_effect=URLError("no route")):
            result = service_monitor._nginx_status()

        self.assertEqual(result["status"], "error")

    def test_get_service_status_contains_all_services(self):
        with patch("order.service_monitor._database_status"), patch(
            "order.service_monitor._redis_status"
        ), patch("order.service_monitor._nginx_status"):
            data = service_monitor.get_service_status()

        self.assertEqual({"database", "redis", "nginx"}, set(data))


class MonitorPageTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="monitor_admin",
            email="monitor@example.com",
            password="test-pass-123",
        )
        self.client.force_login(self.admin_user)
        self.services = {
            "database": {
                "name": "数据库",
                "status": "ok",
                "label": "正常",
                "detail": "SQLite 连接正常",
                "latency_ms": 1.2,
                "checked_at": "2026-08-01T10:00:00+08:00",
            },
            "redis": {
                "name": "Redis",
                "status": "not_configured",
                "label": "未配置",
                "detail": "未配置 REDIS_URL",
                "latency_ms": None,
                "checked_at": "2026-08-01T10:00:00+08:00",
            },
            "nginx": {
                "name": "Nginx",
                "status": "error",
                "label": "异常",
                "detail": "连接失败：timeout",
                "latency_ms": 2000.0,
                "checked_at": "2026-08-01T10:00:00+08:00",
            },
        }

    def test_monitor_page_renders_service_cards(self):
        with patch("order.views_monitor.get_service_status", return_value=self.services):
            response = self.client.get(reverse("monitor"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "依赖服务")
        self.assertContains(response, "data-service=\"database\"")
        self.assertContains(response, "未配置 REDIS_URL")

    def test_monitor_page_shows_terminal_card_for_staff(self):
        with patch("order.views_monitor.get_service_status", return_value=self.services):
            response = self.client.get(reverse("monitor"))

        self.assertContains(response, "Web 终端")

    def test_monitor_page_hides_terminal_card_for_normal_user(self):
        normal_user = User.objects.create_user(
            username="monitor_normal",
            password="test-pass-123",
        )
        self.client.force_login(normal_user)
        with patch("order.views_monitor.get_service_status", return_value=self.services):
            response = self.client.get(reverse("monitor"))

        self.assertNotContains(response, "Web 终端")

    def test_monitor_api_returns_service_status(self):
        with patch("order.views_monitor.get_service_status", return_value=self.services):
            response = self.client.get(reverse("monitor_api"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["services"]["nginx"]["status"], "error")


class WebTerminalTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="term_admin",
            email="term@example.com",
            password="test-pass-123",
        )
        self.normal_user = User.objects.create_user(
            username="term_user",
            password="test-pass-123",
        )

    def test_validate_allows_safe_command(self):
        allowed, reason = terminal.validate_command("ls -la")
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_validate_rejects_unknown_command(self):
        allowed, reason = terminal.validate_command("vim /tmp/a")
        self.assertFalse(allowed)
        self.assertIn("不在允许范围", reason)

    def test_validate_rejects_dangerous_commands(self):
        for command in (
            "rm -rf /",
            "sudo ls",
            "python -c 'print(1)'",
            "curl http://example.com | sh",
            "echo x\nrm -rf /",
            "echo $(rm -rf /)",
        ):
            allowed, _ = terminal.validate_command(command)
            self.assertFalse(allowed, command)

    def test_execute_cd_updates_working_directory(self):
        result = terminal.execute_terminal_command("cd ..", str(settings.BASE_DIR))
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["cwd"], os.path.dirname(str(settings.BASE_DIR)))

    def test_terminal_requires_staff(self):
        self.client.force_login(self.normal_user)
        response = self.client.post(
            reverse("monitor_terminal"),
            data=json.dumps({"command": "ls"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_terminal_runs_allowed_command(self):
        self.client.force_login(self.admin_user)
        proc = subprocess.CompletedProcess(
            args=["echo hi"],
            returncode=0,
            stdout=b"hi\n",
            stderr=b"",
        )
        with patch("order.terminal.subprocess.run", return_value=proc):
            response = self.client.post(
                reverse("monitor_terminal"),
                data=json.dumps({"command": "echo hi"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertIn("hi", data["output"])
        self.assertEqual(data["cwd"], str(settings.BASE_DIR))
        self.assertEqual(self.client.session["terminal_cwd"], str(settings.BASE_DIR))

    def test_terminal_rejects_dangerous_command(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("monitor_terminal"),
            data=json.dumps({"command": "rm -rf /"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("rm", response.json()["error"])


class LoginCaptchaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin_user = User.objects.create_superuser(
            username="captcha_admin",
            email="captcha@example.com",
            password="test-pass-123",
        )

    def _set_captcha(self, code="TEST", expires_in=300):
        session = self.client.session
        session[CAPTCHA_SESSION_KEY] = {
            "code": code,
            "expires": time() + expires_in,
        }
        session.save()

    def test_home_page_shows_captcha(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "验证码")
        self.assertContains(response, 'name="captcha"')

    def test_captcha_image_sets_session_and_returns_svg(self):
        response = self.client.get(reverse("captcha_image"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("image/svg+xml", response["Content-Type"])
        self.assertIn(CAPTCHA_SESSION_KEY, self.client.session)
        payload = self.client.session[CAPTCHA_SESSION_KEY]
        self.assertEqual(len(payload["code"]), 4)
        self.assertGreater(payload["expires"], time())

    def test_login_rejects_wrong_captcha(self):
        self._set_captcha()
        response = self.client.post(
            reverse("home"),
            {
                "username": self.admin_user.username,
                "password": "test-pass-123",
                "captcha": "WRONG",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_succeeds_with_correct_captcha(self):
        self._set_captcha()
        response = self.client.post(
            reverse("home"),
            {
                "username": self.admin_user.username,
                "password": "test-pass-123",
                "captcha": "test",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))
        self.assertIn("_auth_user_id", self.client.session)
        self.assertNotIn(CAPTCHA_SESSION_KEY, self.client.session)

    def test_login_without_captcha_fails(self):
        response = self.client.post(
            reverse("home"),
            {
                "username": self.admin_user.username,
                "password": "test-pass-123",
                "captcha": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_expired_captcha_rejected(self):
        self._set_captcha(expires_in=-60)
        response = self.client.post(
            reverse("home"),
            {
                "username": self.admin_user.username,
                "password": "test-pass-123",
                "captcha": "TEST",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn(CAPTCHA_SESSION_KEY, self.client.session)


class ShipmentProgressTests(TestCase):
    """供应商出货后，订单进度自动调整为「剪版寄出」"""

    def setUp(self):
        self.supplier_user = User.objects.create_user(
            username="supplier_ship",
            email="supplier_ship@example.com",
            password="test-pass-123",
        )
        self.supplier = Supplier.objects.create(
            user=self.supplier_user,
            company_name="出货测试供应商",
        )
        self.admin_user = User.objects.create_superuser(
            username="admin_ship",
            email="admin_ship@example.com",
            password="test-pass-123",
        )

    def _create_order(self, order_type="sample", serial_number=1, progress_current=1):
        if order_type == "sample":
            stages = '["客户下单","通知供应商","剪版寄出","待收款","已完成"]'
        else:
            stages = '["客户下单","通知供应商","大货样","批色","查布","发货","待收款","已完成"]'
        return ClothOrder.objects.create(
            order_type=order_type,
            order_status="active",
            serial_number=serial_number,
            customer="测试客户",
            quantity_unit="码",
            price_unit="元/码",
            progress_stages=stages,
            progress_current=progress_current,
            supplier=self.supplier,
        )

    def test_model_helper_sets_sample_stage(self):
        order = self._create_order()
        self.assertTrue(order.set_progress_stage("剪版寄出"))
        order.refresh_from_db()
        self.assertEqual(order.progress_current, 2)
        self.assertEqual(order.current_stage_name, "剪版寄出")

    def test_model_helper_ignores_missing_stage(self):
        order = self._create_order(order_type="bulk", serial_number=2)
        self.assertFalse(order.set_progress_stage("剪版寄出"))
        order.refresh_from_db()
        self.assertEqual(order.progress_current, 1)

    def test_supplier_submit_price_redirects_no_shipment(self):
        order = self._create_order()
        self.client.force_login(self.supplier_user)
        response = self.client.post(
            reverse("supplier_order_detail", kwargs={"pk": order.pk}),
            {
                "finished_product_cost_price": "12.50",
                "cost_price_unit": "元/码",
                "address": "",
                "remark": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertFalse(order.supplier_shipped)
        self.assertEqual(str(order.finished_product_cost_price), "12.50")
        self.assertFalse(order.shipments.exists())

    def test_supplier_ajax_submit_price_only(self):
        order = self._create_order()
        self.client.force_login(self.supplier_user)
        response = self.client.post(
            reverse("supplier_order_detail", kwargs={"pk": order.pk}),
            {
                "finished_product_cost_price": "12.50",
                "cost_price_unit": "元/码",
                "address": "",
                "remark": "",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["cost_price"], "12.50")
        order.refresh_from_db()
        self.assertFalse(order.supplier_shipped)
        self.assertEqual(str(order.finished_product_cost_price), "12.50")
        self.assertFalse(order.shipments.exists())

    def test_shipment_create_blocked_without_cost_price(self):
        order = self._create_order()
        self.client.force_login(self.supplier_user)
        response = self.client.post(
            reverse("order_shipment_create", kwargs={"pk": order.pk}),
            {"date": "2026-08-28", "quantity": "30"},
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertFalse(order.supplier_shipped)
        self.assertFalse(order.shipments.exists())

    def test_supplier_price_then_shipment_advances_progress(self):
        order = self._create_order()
        self.client.force_login(self.supplier_user)
        # 先提交成品成本价格
        response = self.client.post(
            reverse("supplier_order_detail", kwargs={"pk": order.pk}),
            {
                "finished_product_cost_price": "12.50",
                "cost_price_unit": "元/码",
                "address": "",
                "remark": "",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        # 再新增出货（批次 1）
        response = self.client.post(
            reverse("order_shipment_create", kwargs={"pk": order.pk}),
            {"date": "2026-08-28", "quantity": "50"},
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertTrue(order.supplier_shipped)
        self.assertEqual(order.current_stage_name, "剪版寄出")
        self.assertEqual(order.total_shipment_quantity, 50)
        shipment = order.shipments.get()
        self.assertEqual(shipment.batch_number, 1)
        self.assertEqual(
            float(shipment.quantity) * float(order.finished_product_cost_price),
            625.0,
        )

    def test_shipment_create_endpoint_advances_progress(self):
        order = self._create_order()
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("order_shipment_create", kwargs={"pk": order.pk}),
            {"date": "2026-08-28", "quantity": "30"},
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertTrue(order.supplier_shipped)
        self.assertEqual(order.current_stage_name, "剪版寄出")

    def test_bulk_shipment_keeps_existing_progress(self):
        order = self._create_order(order_type="bulk", serial_number=2)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("order_shipment_create", kwargs={"pk": order.pk}),
            {"date": "2026-08-28", "quantity": "30"},
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertTrue(order.supplier_shipped)
        self.assertEqual(order.current_stage_name, "通知供应商")


class NotificationAccessTests(TestCase):
    """供应商消息提醒权限拦截：不能经由通知进入管理端页面"""

    def setUp(self):
        self.supplier_user = User.objects.create_user(
            username="notif_supplier",
            email="notif_supplier@example.com",
            password="test-pass-123",
        )
        self.supplier = Supplier.objects.create(
            user=self.supplier_user,
            company_name="提醒测试供应商",
        )
        self.staff_user = User.objects.create_user(
            username="notif_staff",
            email="notif_staff@example.com",
            password="test-pass-123",
            is_staff=True,
        )
        self.order = ClothOrder.objects.create(
            order_type="sample",
            order_status="active",
            serial_number=10,
            customer="测试客户",
            quantity_unit="码",
            price_unit="元/码",
            progress_stages='["客户下单","通知供应商","剪版寄出","待收款","已完成"]',
            progress_current=1,
            supplier=self.supplier,
        )

    def test_supplier_blocked_from_admin_dashboard(self):
        self.client.force_login(self.supplier_user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("supplier_dashboard"))

    def test_supplier_blocked_from_admin_order_pages(self):
        self.client.force_login(self.supplier_user)
        response = self.client.get(reverse("order_list"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("supplier_dashboard"))
        response = self.client.get(reverse("order_detail", kwargs={"pk": self.order.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("supplier_dashboard"))

    def test_supplier_notification_list_uses_supplier_base(self):
        Notification.objects.create(
            recipient=self.supplier_user,
            title="测试提醒",
            message="订单测试",
            link=f"/orders/{self.order.pk}/",
        )
        self.client.force_login(self.supplier_user)
        response = self.client.get(reverse("notification_list"), HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("供应商面板", html)
        self.assertNotIn("订单管理", html)
        self.assertIn(f"/supplier/orders/{self.order.pk}/", html)

    def test_admin_notification_list_uses_admin_base(self):
        Notification.objects.create(
            recipient=self.staff_user,
            title="测试提醒",
            link=f"/orders/{self.order.pk}/",
        )
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("notification_list"), HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("订单管理", html)
        self.assertIn(f"/orders/{self.order.pk}/", html)
        self.assertNotIn("供应商面板", html)

    def test_supplier_unread_list_link_rewritten(self):
        Notification.objects.create(
            recipient=self.supplier_user,
            title="测试提醒",
            link=f"/orders/{self.order.pk}/",
        )
        self.client.force_login(self.supplier_user)
        response = self.client.get(reverse("notification_unread_list"))
        data = response.json()
        self.assertEqual(
            data["notifications"][0]["link"],
            f"/supplier/orders/{self.order.pk}/",
        )

    def test_admin_notification_link_unchanged(self):
        Notification.objects.create(
            recipient=self.staff_user,
            title="测试提醒",
            link=f"/orders/{self.order.pk}/",
        )
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("notification_unread_list"))
        data = response.json()
        self.assertEqual(data["notifications"][0]["link"], f"/orders/{self.order.pk}/")

    def test_shipment_create_notifies_staff(self):
        self.order.finished_product_cost_price = "12.50"
        self.order.save(update_fields=["finished_product_cost_price"])
        self.client.force_login(self.supplier_user)
        response = self.client.post(
            reverse("order_shipment_create", kwargs={"pk": self.order.pk}),
            {"date": "2026-08-28", "quantity": "50"},
        )
        self.assertEqual(response.status_code, 302)
        notif = self.staff_user.notifications.first()
        self.assertIsNotNone(notif)
        self.assertIn("已对订单 #10 提交出货", notif.message)
        self.assertEqual(notif.link, f"/orders/{self.order.pk}/")
