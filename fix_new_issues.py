import re

c = open("D:/PycharmProjects/cloth-system/order/views.py","r",encoding="utf-8").read()

# Fix 任务初始化中 -> t()
c = c.replace('\u4efb\u52a1\u521d\u59cb\u5316\u4e2d', 't("page.task_initializing")')

# Add InventoryListView
cls = (
    "class InventoryListView(LoginRequiredMixin, ListView):\n"
    "    model = InventoryItem\n"
    '    template_name = "order/inventory_list.html"\n'
    '    context_object_name = "items"\n'
    "    paginate_by = 50\n\n"
    "    def get_queryset(self):\n"
    "        qs = InventoryItem.objects.all()\n"
    '        q = self.request.GET.get("q", "").strip()\n'
    "        if q:\n"
    "            from django.db.models import Q\n"
    "            qs = qs.filter(\n"
    "                Q(cloth_name__icontains=q) | Q(color__icontains=q) |\n"
    "                Q(serial_no__icontains=q) | Q(customer__icontains=q) |\n"
    "                Q(composition_en__icontains=q) | Q(composition_cn__icontains=q)\n"
    "            )\n"
    "        return qs\n\n"
    "    def get_context_data(self, **kwargs):\n"
    "        ctx = super().get_context_data(**kwargs)\n"
    '        ctx["total"] = InventoryItem.objects.count()\n'
    '        ctx["q"] = self.request.GET.get("q", "")\n'
    "        return ctx\n\n\n"
)

c = c.replace("class InventoryDetailView(LoginRequiredMixin, DetailView):",
              cls + "class InventoryDetailView(LoginRequiredMixin, DetailView):")

open("D:/PycharmProjects/cloth-system/order/views.py","w",encoding="utf-8").write(c)

try:
    compile(c, "test.py", "exec")
    print("COMPILE OK")
except SyntaxError as e:
    print(f"Error L{e.lineno}: {e.msg}")
