# Generated manually - change style_number from CharField to TextField
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('order', '0032_auto_20260727_1503'),
    ]
    operations = [
        migrations.AlterField(
            model_name='clothorder',
            name='style_number',
            field=models.TextField(blank=True, verbose_name='款号'),
        ),
    ]
