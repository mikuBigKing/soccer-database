"""赛事报告 AI 模块。"""
from django.apps import AppConfig


class ReportConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.report'
    verbose_name = '赛事报告 AI'
