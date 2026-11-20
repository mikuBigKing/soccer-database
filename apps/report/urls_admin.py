"""赛事报告管理端路由（挂载在 /api/admin/live/）。"""
from django.urls import path

from .views_admin import AdminMatchReportView

urlpatterns = [
    path('matches/<int:pk>/report', AdminMatchReportView.as_view()),
]
