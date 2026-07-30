from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('order', '0004_alter_clothorder_paid_amount_yes_no'),
    ]

    operations = [
        migrations.AddField(
            model_name='clothorder',
            name='order_status',
            field=models.CharField(
                choices=[('active', '正常'), ('cancelled', '已取消')],
                db_index=True,
                default='active',
                max_length=20,
                verbose_name='订单状态',
            ),
        ),
    ]
