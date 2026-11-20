from django.urls import path

from .views import (
    BestGenerateView,
    BestListView,
    BestPublishView,
    ProfileListView,
    ScoreDetailView,
    ScoreExportView,
    ScoreListView,
    ScoreSummaryView,
)

urlpatterns = [
    path('profiles', ProfileListView.as_view()),
    path('scores', ScoreListView.as_view()),
    path('scores/summary', ScoreSummaryView.as_view()),
    path('scores/export', ScoreExportView.as_view()),
    path('scores/<int:pk>', ScoreDetailView.as_view()),
    path('best', BestListView.as_view()),
    path('best/generate', BestGenerateView.as_view()),
    path('best/publish', BestPublishView.as_view()),
]
