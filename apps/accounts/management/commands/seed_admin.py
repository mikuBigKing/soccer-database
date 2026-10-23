"""创建/重置演示管理员账号（对应 Laravel seeder 的 admin@ball.local / admin123）。

用法：python manage.py seed_admin [--email admin@ball.local] [--password admin123]
"""
from django.core.management.base import BaseCommand

from apps.accounts.models import AdminProfile, User, UserRole


class Command(BaseCommand):
    help = '创建或重置管理员账号'

    def add_arguments(self, parser):
        parser.add_argument('--email', default='admin@ball.local')
        parser.add_argument('--password', default='admin123')
        parser.add_argument('--name', default='系统管理员')

    def handle(self, *args, **options):
        email = options['email'].lower()
        password = options['password']

        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            user = User(username=email, email=email)
        user.name = options['name']
        user.role = UserRole.ADMIN
        user.is_staff = True
        user.is_superuser = True
        user.permissions = []
        user.set_password(password)
        user.save()

        AdminProfile.objects.update_or_create(user=user, defaults={'name': options['name']})
        self.stdout.write(self.style.SUCCESS(f'管理员已就绪：{email} / {password}'))
