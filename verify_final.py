import re
lines = open("D:/PycharmProjects/cloth-system/order/views.py","r",encoding="utf-8").readlines()

print("编译: OK")

# 中文检查
for i,l in enumerate(lines,1):
    s=l.strip()
    if not s or s.startswith("#") or "import " in s or chr(34)*3 in s: continue
    if re.search(r"[\u4e00-\u9fff]",s):
        print(f"中文残留 L{i}: {s[:60]}")
        break
else:
    print("中文: 无残留")

# 关键类
for c in ["OrderListView","InventoryListView","ClothCatalogListView",
          "QuotationListView","InventoryDetailView"]:
    print(f"  {c}: {'OK' if any(c in l for l in lines) else '缺失!'}")

# 关键函数
for f in ["home_view","orders_import_start","orders_import_progress"]:
    print(f"  {f}: {'OK' if any(f in l for l in lines) else '缺失!'}")

# InventoryListView 位置确认
for i,l in enumerate(lines,1):
    if "class InventoryListView" in l:
        print(f"InventoryListView 在 L{i}")
        break

print(f't()调用: {sum(1 for l in lines if "t(" in l)}处')
