"""校级端路由（role=school，按本校数据域裁剪）。"""
from django.urls import path

from .views_school import (
    SchoolAthleteListView,
    SchoolAthleteVerifyView,
    SchoolAuditLogListView,
    SchoolCoachListView,
    SchoolCoachVerifyView,
    SchoolNotificationListView,
    SchoolStatisticsView,
)

urlpatterns = [
    # 实名审核
    path('coaches', SchoolCoachListView.as_view()),
    path('coaches/<int:pk>/verify', SchoolCoachVerifyView.as_view()),
    path('athletes', SchoolAthleteListView.as_view()),
    path('athletes/<int:pk>/verify', SchoolAthleteVerifyView.as_view()),

    # 本校统计
    path('statistics', SchoolStatisticsView.as_view()),

    # 校级通知
    path('notifications', SchoolNotificationListView.as_view()),

    # 本校操作日志
    path('audit-logs', SchoolAuditLogListView.as_view()),
]
