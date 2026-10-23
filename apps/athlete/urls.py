"""运动员端路由（挂在 /api/athlete/ 下）。"""
from django.urls import path

from .views import AthleteMatchListView, AthleteProfileView, AthleteStandingsView, AthleteStatsView

urlpatterns = [
    path('profile', AthleteProfileView.as_view()),
    path('matches', AthleteMatchListView.as_view()),
    path('stats', AthleteStatsView.as_view()),
    path('standings', AthleteStandingsView.as_view()),
]
