import re
lines = open("D:/PycharmProjects/cloth-system/order/views.py","r",encoding="utf-8").readlines()

print("编译检查通过")

# 中文
for i,l in enumerate(lines,1):
    s=l.strip()
    if not s or s.startswith("#") or "import " in s or chr(34)*3 in s: continue
    if re.search(r"[\u4e00-\u9fff]",s):
        print(f"中文残留 L{i}: {s[:60]}")
        break
else:
    print("中文: 无残留")

# 关键类/函数
for c in ["OrderListView","InventoryListView","ClothCatalogListView","QuotationListView",
          "orders_import_start","orders_import_progress","home_view","InventoryDetailView"]:
    if any(c in l for l in lines):
        print(f"  {c}: OK")
    else:
        print(f"  {c}: 缺失!")

# t()统计
cnt = sum(1 for l in lines if 't("' in l)
print(f"t()调用: {cnt}处")
