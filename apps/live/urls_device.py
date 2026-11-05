"""计分网关路由（设备鉴权，非登录态）。"""
from django.urls import path

from .views_device import DeviceScoreReportView

urlpatterns = [
    path('live/report', DeviceScoreReportView.as_view()),
]
