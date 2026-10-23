"""市级 / 校级查阅（挂在 /api/ 下）。"""
from django.urls import path

from .views import SchoolListCreateView

urlpatterns = [
    path('city/schools', SchoolListCreateView.as_view()),
]
