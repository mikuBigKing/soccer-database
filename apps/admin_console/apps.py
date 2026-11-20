from django.apps import AppConfig


class AdminConsoleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.admin_console'
    verbose_name = '管理端（球员 / 教练员）'
