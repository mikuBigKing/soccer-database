"""省级审核端报名路由，挂在 /api/province/ 下。"""
from django.urls import path

from .views_province import (
    ProvinceAccountDetailView,
    ProvinceAccountListCreateView,
    ProvinceAthleteAvatarView,
    ProvinceAthleteDetailView,
    ProvinceAthleteListView,
    ProvinceCoachDetailView,
    ProvinceCoachListView,
    ProvinceCoachQualificationDownloadView,
    ProvinceEnrollmentCertDownloadView,
    ProvinceFormDocxView,
    ProvinceLogListView,
    ProvinceRegistrationApproveView,
    ProvinceRegistrationDetailView,
    ProvinceRegistrationExportView,
    ProvinceRegistrationListView,
    ProvinceRegistrationRejectView,
    ProvinceStampedFormView,
    ProvinceStatisticsView,
)

urlpatterns = [
    path('registrations', ProvinceRegistrationListView.as_view()),
    path('registrations/export', ProvinceRegistrationExportView.as_view()),
    path('registrations/statistics', ProvinceStatisticsView.as_view()),
    path('registrations/<int:pk>', ProvinceRegistrationDetailView.as_view()),
    path('registrations/<int:pk>/approve', ProvinceRegistrationApproveView.as_view()),
    path('registrations/<int:pk>/reject', ProvinceRegistrationRejectView.as_view()),
    path('registrations/<int:pk>/stamped-form', ProvinceStampedFormView.as_view()),
    path('registrations/<int:pk>/form-docx', ProvinceFormDocxView.as_view()),
    path('coaches', ProvinceCoachListView.as_view()),
    path('coaches/<int:pk>', ProvinceCoachDetailView.as_view()),
    path('coaches/<int:pk>/qualification/download', ProvinceCoachQualificationDownloadView.as_view()),
    path('athletes', ProvinceAthleteListView.as_view()),
    path('athletes/<int:pk>', ProvinceAthleteDetailView.as_view()),
    path('athletes/<int:pk>/avatar', ProvinceAthleteAvatarView.as_view()),
    path('athletes/<int:pk>/enrollment-cert/download', ProvinceEnrollmentCertDownloadView.as_view()),
    path('accounts', ProvinceAccountListCreateView.as_view()),
    path('accounts/<int:pk>', ProvinceAccountDetailView.as_view()),
    path('logs', ProvinceLogListView.as_view()),
]
