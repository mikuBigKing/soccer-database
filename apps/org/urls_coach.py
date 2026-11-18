"""教练端路由（挂在 /api/coach/ 下）。"""
from django.urls import path

from .views_coach import (
    CoachProfileView,
    CoachTeamAthleteDetailView,
    CoachTeamAthleteListView,
    CoachTeamDetailView,
    CoachTeamListCreateView,
)

urlpatterns = [
    path('profile', CoachProfileView.as_view()),
    path('teams', CoachTeamListCreateView.as_view()),
    path('teams/<int:pk>', CoachTeamDetailView.as_view()),
    path('teams/<int:pk>/athletes', CoachTeamAthleteListView.as_view()),
    path('teams/<int:pk>/athletes/<int:athlete_id>', CoachTeamAthleteDetailView.as_view()),
]
