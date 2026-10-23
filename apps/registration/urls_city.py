"""市州端报名路由，挂在 /api/city/ 下。"""
from django.urls import path

from .views_city import (
    CityCoachQualificationDownloadView,
    CityEnrollmentCertDownloadView,
    CityRegistrationApproveView,
    CityRegistrationBatchReportView,
    CityRegistrationDetailView,
    CityRegistrationListView,
    CityRegistrationRejectView,
    CityRegistrationReportView,
    CityRegistrationSummaryView,
    CityStampedFormDownloadView,
)

urlpatterns = [
    path('registrations', CityRegistrationListView.as_view()),
    path('registrations/summary', CityRegistrationSummaryView.as_view()),
    path('registrations/report', CityRegistrationBatchReportView.as_view()),
    path('registrations/<int:pk>', CityRegistrationDetailView.as_view()),
    path('registrations/<int:pk>/approve', CityRegistrationApproveView.as_view()),
    path('registrations/<int:pk>/reject', CityRegistrationRejectView.as_view()),
    path('registrations/<int:pk>/report', CityRegistrationReportView.as_view()),
    path('registrations/<int:pk>/stamped-form/download', CityStampedFormDownloadView.as_view()),
    path('athletes/<int:pk>/enrollment-cert/download', CityEnrollmentCertDownloadView.as_view()),
    path('coaches/<int:pk>/qualification/download', CityCoachQualificationDownloadView.as_view()),
]
