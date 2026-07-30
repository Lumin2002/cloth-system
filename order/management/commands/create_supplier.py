"""创建供应商账号的管理命令。"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from order.models import Supplier


class Command(BaseCommand):
    help = "创建供应商账号"

    def add_arguments(self, parser):
        parser.add_argument("username", help="登录用户名")
        parser.add_argument("company", help="公司名称")
        parser.add_argument("--password", help="密码，默认与用户名相同")
        parser.add_argument("--contact", help="联系人", default="")

    def handle(self, *args, **options):
        username = options["username"]
        company = options["company"]
        password = options["password"] or username
        contact = options["contact"]

        if User.objects.filter(username=username).exists():
            raise CommandError(f"用户 {username} 已存在")
        if Supplier.objects.filter(company_name=company).exists():
            raise CommandError(f"供应商 {company} 已存在")

        user = User.objects.create_user(username=username, password=password)
        supplier = Supplier.objects.create(
            user=user,
            company_name=company,
            contact_person=contact,
        )
        self.stdout.write(self.style.SUCCESS(
            f"供应商账号创建成功: {company} (username={username})"
        ))
