# Created to resolve migration conflict with database state.
# Database has 0037 recorded but the file was missing from the codebase.
# This empty migration reconciles the graph: 0034 -> 0037.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("order", "0034_alter_fabricquotation_options_and_more"),
    ]

    operations = [
    ]
