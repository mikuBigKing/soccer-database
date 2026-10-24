"""管理端球员 / 教练员路由（挂在 /api/admin/ 下，不带结尾斜杠）。"""
from django.urls import path

from .views import (
    CoachDetailView,
    CoachListView,
    CoachPublishView,
    CoachResetPasswordView,
    CoachUnpublishView,
    PlayerDetailView,
    PlayerListView,
    PlayerPublishView,
    PlayerResetPasswordView,
    PlayerUnpublishView,
)

urlpatterns = [
    path('players', PlayerListView.as_view()),
    path('players/<int:pk>', PlayerDetailView.as_view()),
    path('players/<int:pk>/publish', PlayerPublishView.as_view()),
    path('players/<int:pk>/unpublish', PlayerUnpublishView.as_view()),
    path('players/<int:pk>/reset-password', PlayerResetPasswordView.as_view()),

    path('coaches', CoachListView.as_view()),
    path('coaches/<int:pk>', CoachDetailView.as_view()),
    path('coaches/<int:pk>/publish', CoachPublishView.as_view()),
    path('coaches/<int:pk>/unpublish', CoachUnpublishView.as_view()),
    path('coaches/<int:pk>/reset-password', CoachResetPasswordView.as_view()),
]
