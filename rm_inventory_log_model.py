import re

# ===== models.py =====
m = open("D:/PycharmProjects/cloth-system/order/models.py","r",encoding="utf-8").read()
# Remove InventoryLog model class (L380-407)
old_model = "class InventoryLog(ClothCatalogMixin, models.Model):\n    """\u5e93\u5b58\u53d8\u52a8\u8bb0\u5f55"""\n\n    LOG_TYPE_CHOICES = [\n        ('in',     '\u5165\u5e93'),\n        ('out',    '\u51fa\u5e93'),\n        ('adjust', '\u8c03\u6574'),\n    ]\n\n    item = models.ForeignKey(\n    InventoryItem,\n    on_delete=models.SET_NULL,\n    related_name='logs',\n    null=True,\n    blank=True\n    )\n    log_type  = models.CharField(max_length=10, choices=LOG_TYPE_CHOICES, verbose_name='\u53d8\u52a8\u7c7b\u578b')\n    quantity  = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='\u53d8\u52a8\u6570\u91cf')\n    remark    = models.TextField(verbose_name='\u5907\u6ce8', blank=True)\n    created_at = models.DateTimeField(auto_now_add=True, verbose_name='\u8bb0\u5f55\u65f6\u95f4')\n    created_by = models.CharField(max_length=50, verbose_name='\u64cd\u4f5c\u4eba', blank=True)\n    class Meta:\n        verbose_name        = '\u5e93\u5b58\u8bb0\u5f55'\n        verbose_name_plural = '\u5e93\u5b58\u8bb0\u5f55'\n        ordering            = ['-created_at']\n\n    def __str__(self):\n        return f\"{self.item} {self.get_log_type_display()} {self.quantity}\"\n\n"
m = m.replace(old_model, "")
# Also remove any references in import line
m = m.replace(", InventoryLog", "")
m = m.replace("InventoryLog, ", "")
open("D:/PycharmProjects/cloth-system/order/models.py","w",encoding="utf-8").write(m)
print("Removed InventoryLog model from models.py")

# ===== admin.py =====
a = open("D:/PycharmProjects/cloth-system/order/admin.py","r",encoding="utf-8").read()
old_admin = "@admin.register(InventoryLog)\nclass InventoryLogAdmin(admin.ModelAdmin):\n    list_display = ['item', 'log_type', 'quantity', 'created_at', 'created_by']\n    list_filter = ['log_type', 'created_at']\n    search_fields = ['item__cloth_name', 'remark']\n    readonly_fields = ['created_at']\n\n"
a = a.replace(old_admin, "")
a = a.replace("InventoryLog, ", "")
a = a.replace(", InventoryLog", "")
open("D:/PycharmProjects/cloth-system/order/admin.py","w",encoding="utf-8").write(a)
print("Removed InventoryLog from admin.py")
