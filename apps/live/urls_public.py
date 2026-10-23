"""赛事直播公众端路由（免登）。"""
from django.urls import path

from .views import PublicMatchDetailView, PublicMatchEventListView, PublicMatchListView

urlpatterns = [
    path('matches', PublicMatchListView.as_view()),
    path('matches/<int:pk>', PublicMatchDetailView.as_view()),
    path('matches/<int:pk>/events', PublicMatchEventListView.as_view()),
]
