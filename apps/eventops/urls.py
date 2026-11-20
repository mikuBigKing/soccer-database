"""eventops 路由：统一挂在 /api/ops/ 下。"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    DocumentDownloadView,
    DocumentPreviewView,
    FinalizeView,
    MatchDocumentViewSet,
    MatchEventSummaryView,
    MatchEventViewSet,
    MatchOfficialViewSet,
    OperationLogViewSet,
    RecordStateView,
    RefereeViewSet,
    ScoreCheckView,
    UnfinalizeView,
)

router = DefaultRouter()
router.register('referees', RefereeViewSet, basename='eventops-referees')
router.register('operation-logs', OperationLogViewSet, basename='eventops-operation-logs')

# 嵌套在比赛下
officials_list = MatchOfficialViewSet.as_view({'get': 'list', 'post': 'create'})
officials_detail = MatchOfficialViewSet.as_view({'delete': 'destroy'})

events_list = MatchEventViewSet.as_view({'get': 'list', 'post': 'create'})
event_detail = MatchEventViewSet.as_view({
    'get': 'retrieve', 'patch': 'partial_update', 'delete': 'destroy',
})
event_revisions = MatchEventViewSet.as_view({'get': 'revisions'})
event_restore = MatchEventViewSet.as_view({'post': 'restore'})

documents_list = MatchDocumentViewSet.as_view({'get': 'list', 'post': 'create'})
document_detail = MatchDocumentViewSet.as_view({'get': 'retrieve', 'patch': 'partial_update'})
document_versions = MatchDocumentViewSet.as_view({'get': 'versions', 'post': 'versions'})
document_archive = MatchDocumentViewSet.as_view({'post': 'archive'})
document_unarchive = MatchDocumentViewSet.as_view({'post': 'unarchive'})

urlpatterns = [
    path('', include(router.urls)),

    # 比赛嵌套资源
    path('matches/<int:match_id>/officials/', officials_list, name='eventops-match-officials'),
    path('matches/<int:match_id>/officials/<int:pk>/', officials_detail, name='eventops-match-official-detail'),

    path('matches/<int:match_id>/events/', events_list, name='eventops-match-events'),
    path('events/<int:pk>/', event_detail, name='eventops-event-detail'),
    path('events/<int:pk>/revisions/', event_revisions, name='eventops-event-revisions'),
    path('events/<int:pk>/restore/', event_restore, name='eventops-event-restore'),

    path('matches/<int:match_id>/event-summary/', MatchEventSummaryView.as_view(), name='eventops-event-summary'),

    path('matches/<int:match_id>/documents/', documents_list, name='eventops-match-documents'),
    path('documents/<int:pk>/', document_detail, name='eventops-document-detail'),
    path('documents/<int:pk>/versions/', document_versions, name='eventops-document-versions'),
    path('documents/<int:pk>/archive/', document_archive, name='eventops-document-archive'),
    path('documents/<int:pk>/unarchive/', document_unarchive, name='eventops-document-unarchive'),

    path('document-versions/<int:version_id>/preview/', DocumentPreviewView.as_view(), name='eventops-document-preview'),
    path('document-versions/<int:version_id>/download/', DocumentDownloadView.as_view(), name='eventops-document-download'),

    path('matches/<int:match_id>/record-state/', RecordStateView.as_view(), name='eventops-record-state'),
    path('matches/<int:match_id>/score-check/', ScoreCheckView.as_view(), name='eventops-score-check'),
    path('matches/<int:match_id>/finalize/', FinalizeView.as_view(), name='eventops-finalize'),
    path('matches/<int:match_id>/unfinalize/', UnfinalizeView.as_view(), name='eventops-unfinalize'),
]
