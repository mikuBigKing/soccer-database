from django.contrib import admin

from .models import (
    EventRevision,
    MatchDocument,
    MatchDocumentVersion,
    MatchEvent,
    MatchOfficial,
    MatchRecord,
    OperationLog,
    Referee,
)


@admin.register(Referee)
class RefereeAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'certificate_no', 'level', 'phone', 'is_active', 'created_at']
    list_filter = ['level', 'is_active']
    search_fields = ['name', 'certificate_no']


@admin.register(MatchOfficial)
class MatchOfficialAdmin(admin.ModelAdmin):
    list_display = ['id', 'match', 'referee', 'role', 'created_at']
    list_filter = ['role']


@admin.register(MatchEvent)
class MatchEventAdmin(admin.ModelAdmin):
    list_display = ['id', 'match', 'event_type', 'period', 'minute', 'team', 'athlete', 'is_deleted', 'version']
    list_filter = ['event_type', 'period', 'is_deleted']


@admin.register(EventRevision)
class EventRevisionAdmin(admin.ModelAdmin):
    list_display = ['id', 'event', 'version_no', 'change_type', 'changed_by', 'created_at']


@admin.register(MatchDocument)
class MatchDocumentAdmin(admin.ModelAdmin):
    list_display = ['id', 'match', 'doc_type', 'part_key', 'status', 'current_version_no', 'archived_at']
    list_filter = ['doc_type', 'status']


@admin.register(MatchDocumentVersion)
class MatchDocumentVersionAdmin(admin.ModelAdmin):
    list_display = ['id', 'document', 'version_no', 'original_name', 'file_size', 'uploaded_by', 'created_at']


@admin.register(MatchRecord)
class MatchRecordAdmin(admin.ModelAdmin):
    list_display = ['id', 'match', 'events_locked', 'locked_at', 'unlocked_at']


@admin.register(OperationLog)
class OperationLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'action', 'resource_type', 'resource_id', 'user_role', 'user_name', 'created_at']
    list_filter = ['resource_type', 'action', 'user_role']
