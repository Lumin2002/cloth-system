from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('order', '0006_add_stock_models'),
    ]

    operations = [
        migrations.DeleteModel(name='InventoryLog'),
        migrations.DeleteModel(name='InventoryItem'),
        migrations.CreateModel(
            name='InventoryItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('serial_no', models.IntegerField(blank=True, null=True, verbose_name='序号')),
                ('cloth_type_id', models.IntegerField(blank=True, null=True, verbose_name='布种类型ID')),
                ('unique_id', models.CharField(blank=True, db_index=True, max_length=200, verbose_name='唯一标识')),
                ('cloth_name', models.CharField(blank=True, db_index=True, max_length=200, verbose_name='布种/加工别')),
                ('color', models.CharField(blank=True, max_length=100, verbose_name='颜色/COLOR')),
                ('composition_en', models.CharField(blank=True, max_length=200, verbose_name='COMPOSITION')),
                ('composition_cn', models.CharField(blank=True, max_length=200, verbose_name='成份')),
                ('specification', models.CharField(blank=True, max_length=200, verbose_name='规格/SPECIFICATION')),
                ('finishing_en', models.CharField(blank=True, max_length=200, verbose_name='FINISHING')),
                ('finishing_cn', models.CharField(blank=True, max_length=200, verbose_name='整理')),
                ('width', models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True, verbose_name='幅宽(英寸)')),
                ('weight', models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True, verbose_name='克重(g/㎡)')),
                ('sides', models.CharField(blank=True, max_length=50, verbose_name='正反面')),
                ('customer', models.CharField(blank=True, db_index=True, max_length=100, verbose_name='客户/CLIENT')),
                ('usage', models.CharField(blank=True, max_length=100, verbose_name='用途/USE')),
                ('bath_no', models.CharField(blank=True, max_length=100, verbose_name='生产缸号')),
                ('position', models.CharField(blank=True, max_length=100, verbose_name='存放位置')),
                ('remark', models.TextField(blank=True, verbose_name='备注/REMARK')),
                ('grey_fabric_no', models.CharField(blank=True, max_length=100, verbose_name='胚布编号')),
                ('grey_fabric_price', models.CharField(blank=True, max_length=50, verbose_name='胚布价格')),
                ('grey_fabric_source', models.CharField(blank=True, max_length=100, verbose_name='胚布来源')),
                ('grey_fabric_date', models.CharField(blank=True, max_length=50, verbose_name='调胚时间')),
                ('finished_price', models.CharField(blank=True, max_length=50, verbose_name='成品价格')),
                ('quantity', models.IntegerField(default=0, validators=[django.core.validators.MinValueValidator(0)], verbose_name='库存数量')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '库存',
                'verbose_name_plural': '库存',
                'ordering': ['serial_no'],
                'indexes': [
                    models.Index(fields=['cloth_name'], name='order_inv_cloth_idx'),
                    models.Index(fields=['customer'], name='order_inv_cust_idx'),
                    models.Index(fields=['quantity'], name='order_inv_qty_idx'),
                ],
            },
        ),
    ]
