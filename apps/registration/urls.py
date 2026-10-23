"""提交方（教练/校级）报名路由，挂在 /api/ 下。"""
from django.urls import path

from .views import (
    CoachQualificationUploadView,
    CompetitionListView,
    EnrollmentCertUploadView,
    MyRegistrationDetailView,
    MyRegistrationListCreateView,
    RegistrationSubmitView,
    StampedFormUploadView,
)

urlpatterns = [
    path('competitions', CompetitionListView.as_view()),
    path('registrations', MyRegistrationListCreateView.as_view()),
    path('registrations/<int:pk>', MyRegistrationDetailView.as_view()),
    path('registrations/<int:pk>/submit', RegistrationSubmitView.as_view()),
    path('registrations/<int:pk>/stamped-form', StampedFormUploadView.as_view()),
    path('registrations/<int:pk>/athletes/<int:athlete_id>/enrollment-cert', EnrollmentCertUploadView.as_view()),
    path('registrations/<int:pk>/coach-qualification', CoachQualificationUploadView.as_view()),
]
