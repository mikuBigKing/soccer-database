"""赛事运营后台（eventops）应用。

功能范围：裁判员主档、比赛裁判安排、赛事事件全量记录（进球/红黄牌/犯规）、
事件版本快照、赛事文件档案（版本链 + 分册 + 归档）、赛事数据归档锁、
统一操作日志。
"""
from django.apps import AppConfig


class EventopsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.eventops'
    verbose_name = '赛事运营后台'
