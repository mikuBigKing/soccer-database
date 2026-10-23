"""报名管理 app。"""
from django.apps import AppConfig


class RegistrationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.registration'
    verbose_name = '报名管理'
