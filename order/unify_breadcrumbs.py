import re, os, sys
sys.stdout.reconfigure(encoding="utf-8")

root = "D:/PycharmProjects/cloth-system/order/templates"

def std_breadcrumb(items):
    lines = []
    lines.append("<nav aria-label=\"breadcrumb\" class=\"mb-3\">")
    lines.append("    <ol class=\"breadcrumb mb-0\">")
    for label, url in items:
        if url:
            lines.append("        <li class=\"breadcrumb-item\"><a href=\"{% url \'" + url + "\' %}\">" + label + "</a></li>")
        else:
            lines.append("        <li class=\"breadcrumb-item active\" aria-current=\"page\">" + label + "</li>")
    lines.append("    </ol>")
    lines.append("</nav>")
    return "\n".join(lines)

files_map = {
    "dashboard.html": [("仪表板", None)],
    "order_list.html": [("仪表板", "dashboard"), ("订单列表", None)],
    "order_create.html": [("仪表板", "dashboard"), ("订单列表", "order_list"), ("新增订单", None)],
    "order_form.html": [("仪表板", "dashboard"), ("订单列表", "order_list"), ("编辑订单", None)],
    "orders_import.html": [("仪表板", "dashboard"), ("订单列表", "order_list"), ("导入订单", None)],
    "delete_all_confirm.html": [("仪表板", "dashboard"), ("订单列表", "order_list"), ("清空全部订单", None)],
    "inventory_list.html": [("仪表板", "dashboard"), ("库存列表", None)],
    "inventory_detail.html": [("仪表板", "dashboard"), ("库存列表", "inventory_list"), ("库存详情", None)],
    "inventory_form.html": [("仪表板", "dashboard"), ("库存列表", "inventory_list"), ("编辑库存", None)],
    "inventory_import.html": [("仪表板", "dashboard"), ("库存列表", "inventory_list"), ("导入库存", None)],
    "inventory_log_list.html": [("仪表板", "dashboard"), ("库存列表", "inventory_list"), ("库存变动日志", None)],
    "cloth_catalog_list.html": [("布种编号表", None)],
    "cloth_catalog_form.html": [("布种编号表", "cloth_catalog_list"), (None, None)],
    "cloth_catalog_import.html": [("布种编号表", "cloth_catalog_list"), ("导入Excel", None)],
    "supplier_manage_list.html": [("仪表板", "dashboard"), ("供应商账号", None)],
    "supplier_manage_form.html": [("仪表板", "dashboard"), ("供应商账号", "supplier_manage_list"), (None, None)],
}

for fname, items in files_map.items():
    path = os.path.join(root, fname)
    content = open(path, "r", encoding="utf-8").read()

    # Resolve dynamic labels
    resolved = []
    for label, url in items:
        if label is None and url is None:
            resolved.append(("编辑", None))
        else:
            resolved.append((label, url))

    new_breadcrumb = std_breadcrumb(resolved)

    pattern = r"<nav[^>]*aria-label=[\"']breadcrumb[\"'][^>]*>.*?</nav>"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        content = content[:match.start()] + new_breadcrumb + content[match.end():]
        open(path, "w", encoding="utf-8").write(content)
        print(fname + ": OK")
    else:
        print(fname + ": no breadcrumb found")

# Special cases with dynamic content
print()
print("=== Special cases ===")

# order_detail.html
path = os.path.join(root, "order_detail.html")
content = open(path, "r", encoding="utf-8").read()
new = "<nav aria-label=\"breadcrumb\" class=\"mb-3\"><ol class=\"breadcrumb mb-0\"><li class=\"breadcrumb-item\"><a href=\"{% url 'dashboard' %}\">仪表板</a></li><li class=\"breadcrumb-item\"><a href=\"{% url 'order_list' %}\">订单列表</a></li><li class=\"breadcrumb-item active\" aria-current=\"page\">订单 #{{ order.serial_number }}</li></ol></nav>"
content = re.sub(r"<nav[^>]*aria-label=[\"']breadcrumb[\"'][^>]*>.*?</nav>", new, content, count=1, flags=re.DOTALL)
open(path, "w", encoding="utf-8").write(content)
print("order_detail.html: OK")

# cloth_catalog_detail.html
path = os.path.join(root, "cloth_catalog_detail.html")
content = open(path, "r", encoding="utf-8").read()
new = "<nav aria-label=\"breadcrumb\" class=\"mb-3\"><ol class=\"breadcrumb mb-0\"><li class=\"breadcrumb-item\"><a href=\"{% url 'cloth_catalog_list' %}\">布种编号表</a></li><li class=\"breadcrumb-item active\" aria-current=\"page\">{{ item.cloth_code }}</li></ol></nav>"
content = re.sub(r"<nav[^>]*aria-label=[\"']breadcrumb[\"'][^>]*>.*?</nav>", new, content, count=1, flags=re.DOTALL)
open(path, "w", encoding="utf-8").write(content)
print("cloth_catalog_detail.html: OK")

# cloth_catalog_form.html - special: dynamic form_title
path = os.path.join(root, "cloth_catalog_form.html")
content = open(path, "r", encoding="utf-8").read()
new = "<nav aria-label=\"breadcrumb\" class=\"mb-3\"><ol class=\"breadcrumb mb-0\"><li class=\"breadcrumb-item\"><a href=\"{% url 'cloth_catalog_list' %}\">布种编号表</a></li><li class=\"breadcrumb-item active\" aria-current=\"page\">{{ form_title }}</li></ol></nav>"
content = re.sub(r"<nav[^>]*aria-label=[\"']breadcrumb[\"'][^>]*>.*?</nav>", new, content, count=1, flags=re.DOTALL)
open(path, "w", encoding="utf-8").write(content)
print("cloth_catalog_form.html: OK")

# supplier_order_detail.html - supplier side
path = os.path.join(root, "supplier_order_detail.html")
content = open(path, "r", encoding="utf-8").read()
new = "<nav aria-label=\"breadcrumb\" class=\"mb-3\"><ol class=\"breadcrumb mb-0\"><li class=\"breadcrumb-item\"><a href=\"{% url 'supplier_dashboard' %}\">我的订单</a></li><li class=\"breadcrumb-item active\" aria-current=\"page\">订单 #{{ order.serial_number }}</li></ol></nav>"
content = re.sub(r"<nav[^>]*aria-label=[\"']breadcrumb[\"'][^>]*>.*?</nav>", new, content, count=1, flags=re.DOTALL)
open(path, "w", encoding="utf-8").write(content)
print("supplier_order_detail.html: OK")

print()
print("All done")
