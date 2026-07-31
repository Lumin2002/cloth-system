from time import time
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from order import service_monitor
from order.middleware import SESSION_LAST_ACTIVITY_KEY
from order.models import Supplier


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
        self.client.post(
            reverse("home"),
            {
                "username": self.supplier_user.username,
                "password": "test-pass-123",
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

    def test_monitor_api_returns_service_status(self):
        with patch("order.views_monitor.get_service_status", return_value=self.services):
            response = self.client.get(reverse("monitor_api"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["services"]["nginx"]["status"], "error")
