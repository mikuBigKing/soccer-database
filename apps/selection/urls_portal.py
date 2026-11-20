from django.urls import path

from .views import PortalBestView, PortalScoreView

urlpatterns = [
    path('selection-scores/', PortalScoreView.as_view()),
    path('selection-best/', PortalBestView.as_view()),
]
