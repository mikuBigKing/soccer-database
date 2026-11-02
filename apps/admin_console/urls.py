"""管理端球员 / 教练员路由（挂在 /api/admin/ 下，不带结尾斜杠）。"""
from django.urls import path

from .views import (
    CoachAccountStatusView,
    CoachBatchResetPasswordView,
    CoachDetailView,
    CoachListView,
    CoachObjectionListView,
    CoachPublishView,
    CoachResetPasswordView,
    CoachRestoreView,
    CoachUnpublishView,
    ObjectionHandleView,
    PlayerAccountStatusView,
    PlayerBatchResetPasswordView,
    PlayerDetailView,
    PlayerListView,
    PlayerPublishView,
    PlayerResetPasswordView,
    PlayerRestoreView,
    PlayerStatusFlagsView,
    PlayerUnpublishView,
)

urlpatterns = [
    path('players', PlayerListView.as_view()),
    path('players/batch-reset-password', PlayerBatchResetPasswordView.as_view()),
    path('players/<int:pk>', PlayerDetailView.as_view()),
    path('players/<int:pk>/publish', PlayerPublishView.as_view()),
    path('players/<int:pk>/unpublish', PlayerUnpublishView.as_view()),
    path('players/<int:pk>/reset-password', PlayerResetPasswordView.as_view()),
    path('players/<int:pk>/status-flags', PlayerStatusFlagsView.as_view()),
    path('players/<int:pk>/account-status', PlayerAccountStatusView.as_view()),
    path('players/<int:pk>/restore', PlayerRestoreView.as_view()),

    path('coaches', CoachListView.as_view()),
    path('coaches/batch-reset-password', CoachBatchResetPasswordView.as_view()),
    path('coaches/<int:pk>', CoachDetailView.as_view()),
    path('coaches/<int:pk>/publish', CoachPublishView.as_view()),
    path('coaches/<int:pk>/unpublish', CoachUnpublishView.as_view()),
    path('coaches/<int:pk>/reset-password', CoachResetPasswordView.as_view()),
    path('coaches/<int:pk>/account-status', CoachAccountStatusView.as_view()),
    path('coaches/<int:pk>/restore', CoachRestoreView.as_view()),
    path('coaches/<int:pk>/objections', CoachObjectionListView.as_view()),

    path('objections/<int:pk>/handle', ObjectionHandleView.as_view()),
]
