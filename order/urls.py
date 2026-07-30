from django.urls import path
from . import views

urlpatterns = [
    # 主页和其他
    path('', views.home_view, name='home'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    # 订单
    path('orders/', views.OrderListView.as_view(), name='order_list'),
    path('orders/add/', views.OrderCreateView.as_view(), name='order_create'),
    path('orders/bulk/', views.bulk_orders_action, name='bulk_orders'),
    path('orders/<int:pk>/', views.OrderDetailView.as_view(), name='order_detail'),
    path('orders/<int:pk>/edit/', views.order_edit_redirect, name='order_edit'),
    path("orders/<int:pk>/statement/", views.order_statement, name="order_statement"),
    path("orders/statement/bulk/", views.order_statement_bulk, name="order_statement_bulk"),
    path('orders/<int:pk>/toggle-status/', views.order_toggle_status, name='order_toggle_status'),
    path('orders/<int:pk>/toggle-payment/', views.order_toggle_payment_status, name='order_toggle_payment_status'),
    path('orders/<int:pk>/toggle-supplier-paid/', views.order_toggle_supplier_paid, name='order_toggle_supplier_paid'),
    path('orders/<int:pk>/refresh/', views.order_refresh_calculations, name='order_refresh_calculations'),
    path('orders/<int:pk>/update-progress/', views.order_update_progress, name='order_update_progress'),
    path("orders/<int:pk>/shipments/add/", views.order_shipment_create, name="order_shipment_create"),
    path("orders/<int:pk>/shipments/<int:shipment_pk>/edit/", views.order_shipment_edit, name="order_shipment_edit"),
    path("orders/<int:pk>/shipments/<int:shipment_pk>/delete/", views.order_shipment_delete, name="order_shipment_delete"),
    path('orders/import/', views.orders_import_page, name='orders_import'),
    path('orders/import/start/', views.orders_import_start, name='orders_import_start'),
    path('orders/import/progress/<uuid:task_id>/', views.orders_import_progress, name='orders_import_progress'),
    path('orders/export/', views.orders_export, name='orders_export'),
    path('orders/delete-all/', views.orders_delete_all, name='orders_delete_all'),
    path('orders/<int:pk>/delete/', views.order_delete, name='order_delete'),
    # 库存
    path('inventory/bulk/', views.inventory_bulk_action, name='inventory_bulk'),
    path('inventory/', views.InventoryListView.as_view(), name='inventory_list'),
    path('inventory/<int:pk>/', views.InventoryDetailView.as_view(), name='inventory_detail'),
    path('inventory/<int:pk>/edit/', views.InventoryUpdateView.as_view(), name='inventory_edit'),
    path('inventory/import/', views.inventory_import_page, name='inventory_import'),
    path('inventory/import/start/', views.inventory_import_start, name='inventory_import_start'),
    path('inventory/import/progress/<uuid:task_id>/', views.inventory_import_progress, name='inventory_import_progress'),
    path('inventory/export/', views.inventory_export, name='inventory_export'),
    path('inventory/delete-all/', views.inventory_delete_all, name='inventory_delete_all'),
    # 库存日志
    path('inventory/logs/', views.InventoryLogListView.as_view(), name='inventory_log_list'),
    path('inventory/logs/export/', views.inventory_log_export, name='inventory_log_export'),
    path('inventory/logs/delete-all/', views.inventory_log_delete_all, name='inventory_log_delete_all'),

    # 布种编号
    path("cloth-catalog/", views.ClothCatalogListView.as_view(), name="cloth_catalog_list"),
    path("cloth-catalog/<int:pk>/", views.ClothCatalogDetailView.as_view(), name="cloth_catalog_detail"),
    path("cloth-catalog/import/", views.cloth_catalog_import, name="cloth_catalog_import"),
    path("cloth-catalog/import/start/", views.cloth_catalog_import_start, name="cloth_catalog_import_start"),
    path("cloth-catalog/add/", views.ClothCatalogCreateView.as_view(), name="cloth_catalog_add"),
    path("cloth-catalog/import/progress/<uuid:task_id>/", views.cloth_catalog_import_progress, name="cloth_catalog_import_progress"),
    path("cloth-catalog/<int:pk>/delete/", views.cloth_catalog_delete, name="cloth_catalog_delete"),
    path("cloth-catalog/delete-all/", views.cloth_catalog_delete_all, name="cloth_catalog_delete_all"),
    path("cloth-catalog/api/autocomplete/", views.cloth_catalog_autocomplete, name="cloth_catalog_autocomplete"),
    # 供应商面板
    # 供应商账号管理
    path("supplier/manage/", views.SupplierManageListView.as_view(), name="supplier_manage_list"),
    path("supplier/manage/add/", views.SupplierManageCreateView.as_view(), name="supplier_manage_add"),
    path("supplier/manage/<int:pk>/edit/", views.SupplierManageUpdateView.as_view(), name="supplier_manage_edit"),
    path("supplier/manage/<int:pk>/delete/", views.supplier_manage_delete, name="supplier_manage_delete"),
    path("supplier/manage/<int:pk>/reset-password/", views.supplier_manage_reset_password, name="supplier_manage_reset_password"),
    path("supplier/", views.SupplierDashboardView.as_view(), name="supplier_dashboard"),
    path("supplier/orders/<int:pk>/", views.SupplierOrderDetailView.as_view(), name="supplier_order_detail"),
    path("supplier/logout/", views.supplier_logout_view, name="supplier_logout"),
    path("orders/updated-at/", views.order_list_updated_at, name="order_list_updated_at"),
    # news
    path("notifications/", views.notification_list, name="notification_list"),
    path("notifications/unread-count/", views.notification_unread_count, name="notification_unread_count"),
    path("notifications/unread-list/", views.notification_unread_list, name="notification_unread_list"),
    path("notifications/<int:pk>/mark-read/", views.notification_mark_read, name="notification_mark_read"),
    path("notifications/mark-all-read/", views.notification_mark_all_read, name="notification_mark_all_read"),

    # fabric quotation lookup
    path('quotation/lookup/', views.quotation_lookup, name='quotation_lookup'),
    # fabric quotation management
    path("quotation/", views.QuotationListView.as_view(), name="quotation_list"),
    path("quotation/add/", views.QuotationCreateView.as_view(), name="quotation_add"),
    path("quotation/<int:pk>/", views.QuotationDetailView.as_view(), name="quotation_detail"),
    path("quotation/import/", views.quotation_import, name="quotation_import"),
    path("quotation/delete-all/", views.quotation_delete_all, name="quotation_delete_all"),
    path("quotation/<int:pk>/delete/", views.quotation_delete, name="quotation_delete"),


]
