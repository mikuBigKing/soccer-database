"""赛事报告公众端路由（挂载在 /api/public/live/）。"""
from django.urls import path

from .views_public import PublicMatchReportView

urlpatterns = [
    path('matches/<int:pk>/report', PublicMatchReportView.as_view()),
]
