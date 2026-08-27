"""视图模块：通知（由 views.py 拆分而来）。"""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from .models import (
    ClothCatalog,
    ClothOrder,
    FabricQuotation,
    InventoryItem,
    InventoryLog,
    Notification,
    Shipment,
    Supplier,
    notify_all_staff,
    notify_user,
)



def notification_list(request):
    is_supplier = hasattr(request.user, "supplier_profile") and not request.user.is_staff
    template = (
        "order/notification_list_supplier.html"
        if is_supplier
        else "order/notification_list.html"
    )
    notifications = request.user.notifications.all()
    page = request.GET.get("page", 1)
    paginator = Paginator(notifications, 20)
    page_obj = paginator.get_page(page)
    for n in page_obj:
        n.display_link = n.link_for(request.user)
    return render(
        request,
        template,
        {
            "page_obj": page_obj,
            "unread_count": notifications.filter(is_read=False).count(),
            "supplier": request.user.supplier_profile if is_supplier else None,
        },
    )


def notification_unread_count(request):
    count = request.user.notifications.filter(is_read=False).count()
    return JsonResponse({"count": count})


def notification_mark_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.is_read = True
    notification.save(update_fields=["is_read"])
    return JsonResponse({"ok": True})


def notification_mark_all_read(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return JsonResponse({"ok": True})


def notification_unread_list(request):
    qs = request.user.notifications.filter(is_read=False)[:5]
    data = []
    for n in qs:
        data.append(
            {
                "id": n.pk,
                "title": n.title,
                "message": n.message,
                "link": n.link_for(request.user),
                "created_at": n.created_at.strftime("%m-%d %H:%M"),
            }
        )
    return JsonResponse({"notifications": data})
