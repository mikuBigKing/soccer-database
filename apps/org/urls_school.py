"""校级端球队路由（挂在 /api/school/ 下）。"""
from django.urls import path

from .views_school import SchoolTeamListView, SchoolTeamVerifyView

urlpatterns = [
    path('teams', SchoolTeamListView.as_view()),
    path('teams/<int:pk>/verify', SchoolTeamVerifyView.as_view()),
]
