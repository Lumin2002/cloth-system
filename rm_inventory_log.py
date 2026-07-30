import re

# ===== views.py =====
c = open("D:/PycharmProjects/cloth-system/order/views.py","r",encoding="utf-8").read()

# Remove InventoryLogListView class (4 lines)
old_class = "class InventoryLogListView(LoginRequiredMixin, ListView):\n    model = InventoryLog\n    template_name = \"order/inventory_log_list.html\"\n    context_object_name = \"items\"\n    paginate_by = 50\n\n"
c = c.replace(old_class, "")

# Remove InventoryLog from import line
c = c.replace(", InventoryLog,", ",")
c = c.replace("InventoryLog, ", "")
c = c.replace(", InventoryLog", "")

open("D:/PycharmProjects/cloth-system/order/views.py","w",encoding="utf-8").write(c)

# ===== urls.py =====
u = open("D:/PycharmProjects/cloth-system/order/urls.py","r",encoding="utf-8").read()
old_url = "    path('inventory/logs/', views.InventoryLogListView.as_view(), name='inventory_log_list'),\n"
u = u.replace(old_url, "")
open("D:/PycharmProjects/cloth-system/order/urls.py","w",encoding="utf-8").write(u)

print("Removed inventory log feature")

# Verify
compile(c, "test.py", "exec")
print("COMPILE OK")
