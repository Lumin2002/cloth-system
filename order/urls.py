from django.urls import path

from . import views_auth
from . import views_backup
from . import views_catalog
from . import views_dashboard
from . import views_inventory
from . import views_monitor
from . import views_notifications
from . import views_orders
from . import views_quotation
from . import views_settings
from . import views_supplier

urlpatterns = [
    # 主页和其他
    path('', views_auth.home_view, name='home'),
    path('captcha/', views_auth.captcha_image, name='captcha_image'),
    path('logout/', views_auth.logout_view, name='logout'),
    path('dashboard/', views_dashboard.dashboard_view, name='dashboard'),
    # 订单
    path('orders/', views_orders.OrderListView.as_view(), name='order_list'),
    path('orders/add/', views_orders.OrderCreateView.as_view(), name='order_create'),
    path('orders/bulk/', views_orders.bulk_orders_action, name='bulk_orders'),
    path('orders/<int:pk>/', views_orders.OrderDetailView.as_view(), name='order_detail'),
    path('orders/<int:pk>/edit/', views_orders.order_edit_redirect, name='order_edit'),
    path("orders/<int:pk>/statement/", views_orders.order_statement, name="order_statement"),
    path("orders/statement/bulk/", views_orders.order_statement_bulk, name="order_statement_bulk"),
    path('orders/<int:pk>/toggle-status/', views_orders.order_toggle_status, name='order_toggle_status'),
    path('orders/<int:pk>/toggle-payment/', views_orders.order_toggle_payment_status, name='order_toggle_payment_status'),
    path('orders/<int:pk>/toggle-supplier-paid/', views_orders.order_toggle_supplier_paid, name='order_toggle_supplier_paid'),
    path('orders/<int:pk>/refresh/', views_orders.order_refresh_calculations, name='order_refresh_calculations'),
    path('orders/<int:pk>/update-progress/', views_orders.order_update_progress, name='order_update_progress'),
    path("orders/<int:pk>/shipments/add/", views_supplier.order_shipment_create, name="order_shipment_create"),
    path("orders/<int:pk>/shipments/<int:shipment_pk>/edit/", views_supplier.order_shipment_edit, name="order_shipment_edit"),
    path("orders/<int:pk>/shipments/<int:shipment_pk>/delete/", views_supplier.order_shipment_delete, name="order_shipment_delete"),
    path('orders/import/', views_orders.orders_import_page, name='orders_import'),
    path('orders/import/start/', views_orders.orders_import_start, name='orders_import_start'),
    path('orders/import/progress/<uuid:task_id>/', views_orders.orders_import_progress, name='orders_import_progress'),
    path('orders/export/', views_orders.orders_export, name='orders_export'),
    path('orders/delete-all/', views_orders.orders_delete_all, name='orders_delete_all'),
    path('orders/<int:pk>/delete/', views_orders.order_delete, name='order_delete'),
    # 库存
    path('inventory/bulk/', views_inventory.inventory_bulk_action, name='inventory_bulk'),
    path('inventory/', views_inventory.InventoryListView.as_view(), name='inventory_list'),
    path('inventory/<int:pk>/', views_inventory.InventoryDetailView.as_view(), name='inventory_detail'),
    path('inventory/<int:pk>/edit/', views_inventory.InventoryUpdateView.as_view(), name='inventory_edit'),
    path('inventory/import/', views_inventory.inventory_import_page, name='inventory_import'),
    path('inventory/import/start/', views_inventory.inventory_import_start, name='inventory_import_start'),
    path('inventory/import/progress/<uuid:task_id>/', views_inventory.inventory_import_progress, name='inventory_import_progress'),
    path('inventory/export/', views_inventory.inventory_export, name='inventory_export'),
    path('inventory/delete-all/', views_inventory.inventory_delete_all, name='inventory_delete_all'),
    # 库存日志
    path('inventory/logs/', views_inventory.InventoryLogListView.as_view(), name='inventory_log_list'),
    path('inventory/logs/export/', views_inventory.inventory_log_export, name='inventory_log_export'),
    path('inventory/logs/delete-all/', views_inventory.inventory_log_delete_all, name='inventory_log_delete_all'),

    # 布种编号
    path("cloth-catalog/", views_catalog.ClothCatalogListView.as_view(), name="cloth_catalog_list"),
    path("cloth-catalog/<int:pk>/", views_catalog.ClothCatalogDetailView.as_view(), name="cloth_catalog_detail"),
    path("cloth-catalog/import/", views_catalog.cloth_catalog_import, name="cloth_catalog_import"),
    path("cloth-catalog/import/start/", views_catalog.cloth_catalog_import_start, name="cloth_catalog_import_start"),
    path("cloth-catalog/add/", views_catalog.ClothCatalogCreateView.as_view(), name="cloth_catalog_add"),
    path("cloth-catalog/import/progress/<uuid:task_id>/", views_catalog.cloth_catalog_import_progress, name="cloth_catalog_import_progress"),
    path("cloth-catalog/<int:pk>/delete/", views_catalog.cloth_catalog_delete, name="cloth_catalog_delete"),
    path("cloth-catalog/delete-all/", views_catalog.cloth_catalog_delete_all, name="cloth_catalog_delete_all"),
    path("cloth-catalog/api/autocomplete/", views_catalog.cloth_catalog_autocomplete, name="cloth_catalog_autocomplete"),
    # 供应商账号管理
    path("supplier/manage/", views_supplier.SupplierManageListView.as_view(), name="supplier_manage_list"),
    path("supplier/manage/add/", views_supplier.SupplierManageCreateView.as_view(), name="supplier_manage_add"),
    path("supplier/manage/<int:pk>/edit/", views_supplier.SupplierManageUpdateView.as_view(), name="supplier_manage_edit"),
    path("supplier/manage/<int:pk>/delete/", views_supplier.supplier_manage_delete, name="supplier_manage_delete"),
    path("supplier/manage/<int:pk>/reset-password/", views_supplier.supplier_manage_reset_password, name="supplier_manage_reset_password"),
    path("supplier/", views_supplier.SupplierDashboardView.as_view(), name="supplier_dashboard"),
    path("supplier/orders/<int:pk>/", views_supplier.SupplierOrderDetailView.as_view(), name="supplier_order_detail"),
    path("supplier/logout/", views_auth.supplier_logout_view, name="supplier_logout"),
    path("orders/updated-at/", views_orders.order_list_updated_at, name="order_list_updated_at"),
    # 通知
    path("notifications/", views_notifications.notification_list, name="notification_list"),
    path("notifications/unread-count/", views_notifications.notification_unread_count, name="notification_unread_count"),
    path("notifications/unread-list/", views_notifications.notification_unread_list, name="notification_unread_list"),
    path("notifications/<int:pk>/mark-read/", views_notifications.notification_mark_read, name="notification_mark_read"),
    path("notifications/mark-all-read/", views_notifications.notification_mark_all_read, name="notification_mark_all_read"),

    # 面料报价
    path('quotation/lookup/', views_quotation.quotation_lookup, name='quotation_lookup'),
    path("quotation/", views_quotation.QuotationListView.as_view(), name="quotation_list"),
    path("quotation/add/", views_quotation.QuotationCreateView.as_view(), name="quotation_add"),
    path("quotation/<int:pk>/", views_quotation.QuotationDetailView.as_view(), name="quotation_detail"),
    path("quotation/import/", views_quotation.quotation_import, name="quotation_import"),
    path("quotation/delete-all/", views_quotation.quotation_delete_all, name="quotation_delete_all"),
    path("quotation/<int:pk>/delete/", views_quotation.quotation_delete, name="quotation_delete"),

    # 系统监控
    path("monitor/", views_monitor.monitor_view, name="monitor"),
    path("monitor/api/", views_monitor.monitor_api, name="monitor_api"),
    path("monitor/terminal/", views_monitor.terminal_run, name="monitor_terminal"),

    # 数据库备份
    path("backup/", views_backup.backup_list, name="backup_list"),
    path("backup/create/", views_backup.backup_create, name="backup_create"),
    path("backup/config/save/", views_backup.backup_config_save, name="backup_config_save"),
    path("backup/<str:name>/download/", views_backup.backup_download, name="backup_download"),
    path("backup/<str:name>/delete/", views_backup.backup_delete, name="backup_delete"),

    # 系统设置
    path("system/settings/", views_settings.settings_index, name="system_settings"),
    path("system/settings/redis/", views_settings.settings_redis, name="settings_redis"),
    path("system/settings/redis/save/", views_settings.settings_redis_save, name="settings_redis_save"),
    path("system/settings/redis/test/", views_settings.settings_redis_test, name="settings_redis_test"),
    path("system/settings/mysql/", views_settings.settings_mysql, name="settings_mysql"),
    path("system/settings/mysql/save/", views_settings.settings_mysql_save, name="settings_mysql_save"),
    path("system/settings/nginx/", views_settings.settings_nginx, name="settings_nginx"),
    path("system/settings/nginx/save/", views_settings.settings_nginx_save, name="settings_nginx_save"),
    path("system/settings/logs/", views_settings.settings_logs, name="settings_logs"),
    path("system/settings/logs/clear/", views_settings.settings_logs_clear, name="settings_logs_clear"),

]
