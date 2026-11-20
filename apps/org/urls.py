"""管理员：行政区划 / 城市 / 学校（挂在 /api/admin/ 下）。"""
from django.urls import path

from .views import (
    CityDetailView,
    CityListCreateView,
    ProvinceDetailView,
    ProvinceListCreateView,
    SchoolDetailView,
    SchoolListCreateView,
    TeamAthletesView,
    TeamCoachesView,
    TeamDetailView,
    TeamListView,
    TeamMatchesView,
    TeamResetCoachPasswordView,
)

urlpatterns = [
    path('provinces', ProvinceListCreateView.as_view()),
    path('provinces/<int:pk>', ProvinceDetailView.as_view()),
    path('cities', CityListCreateView.as_view()),
    path('cities/<int:pk>', CityDetailView.as_view()),
    path('schools', SchoolListCreateView.as_view()),
    path('schools/<int:pk>', SchoolDetailView.as_view()),
    path('teams', TeamListView.as_view()),
    path('teams/<int:pk>', TeamDetailView.as_view()),
    path('teams/<int:pk>/athletes', TeamAthletesView.as_view()),
    path('teams/<int:pk>/coaches', TeamCoachesView.as_view()),
    path('teams/<int:pk>/matches', TeamMatchesView.as_view()),
    path('teams/<int:pk>/reset-coach-password', TeamResetCoachPasswordView.as_view()),
]
