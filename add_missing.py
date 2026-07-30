lines = open("D:/PycharmProjects/cloth-system/order/views.py","r",encoding="utf-8").readlines()
for i,l in enumerate(lines,1):
    if "def inventory_import_start" in l:
        lines.insert(i-1, "def inventory_import_page(request):\n    return render(request, \"order/inventory_import.html\")\n\n")
        print(f"Added at L{i}")
        break
open("D:/PycharmProjects/cloth-system/order/views.py","w",encoding="utf-8").writelines(lines)
