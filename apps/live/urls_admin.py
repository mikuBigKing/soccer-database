"""赛事直播管理端路由（登录 + live:* 权限点）。"""
from django.urls import path

from .views import (
    AdminEventDetailView,
    AdminFaceVerifyRecordsView,
    AdminFaceVerifyView,
    AdminFileDeleteView,
    AdminFileDownloadView,
    AdminLiveLinkView,
    AdminMatchDetailView,
    AdminMatchEventView,
    AdminMatchFileView,
    AdminMatchLineupView,
    AdminMatchListCreateView,
    AdminMatchStatusView,
    AdminRefereeView,
)

urlpatterns = [
    # 比赛 CRUD + 状态流转 + 阵容
    path('matches', AdminMatchListCreateView.as_view()),
    path('matches/<int:pk>', AdminMatchDetailView.as_view()),
    path('matches/<int:pk>/status', AdminMatchStatusView.as_view()),
    path('matches/<int:pk>/lineup', AdminMatchLineupView.as_view()),

    # 事件
    path('matches/<int:pk>/events', AdminMatchEventView.as_view()),
    path('events/<int:pk>', AdminEventDetailView.as_view()),

    # 裁判
    path('matches/<int:pk>/referees', AdminRefereeView.as_view()),

    # 赛事文件
    path('matches/<int:pk>/files', AdminMatchFileView.as_view()),
    path('files/<int:pk>/download', AdminFileDownloadView.as_view()),
    path('files/<int:pk>', AdminFileDeleteView.as_view()),

    # 直播链接
    path('matches/<int:pk>/live-link', AdminLiveLinkView.as_view()),

    # 人脸核验
    path('matches/<int:pk>/face-verify', AdminFaceVerifyView.as_view()),
    path('matches/<int:pk>/face-verify-records', AdminFaceVerifyRecordsView.as_view()),
]
