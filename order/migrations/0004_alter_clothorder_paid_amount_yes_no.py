from django.db import migrations, models


def clear_legacy_paid_amount_values(apps, schema_editor):
    """原 Decimal 金额数据与汇总表 是/否 含义不一致，迁移时清空。"""
    ClothOrder = apps.get_model('order', 'ClothOrder')
    ClothOrder.objects.all().update(paid_amount=None)


class Migration(migrations.Migration):

    dependencies = [
        ('order', '0003_alter_clothorder_certificate_status_and_more'),
    ]

    operations = [
        migrations.RunPython(clear_legacy_paid_amount_values, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='clothorder',
            name='paid_amount',
            field=models.CharField(
                blank=True,
                choices=[('yes', '是'), ('no', '否')],
                default='',
                max_length=10,
                verbose_name='已付款',
            ),
        ),
    ]
