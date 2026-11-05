"""赛事运营后台视图。

权限矩阵：
- 查询裁判 / 比赛事件 / 裁判安排 / 文件档案 / 归档状态 → admin / province / school（校级仅限本校比赛）
- 裁判员增删改、裁判安排增删 → admin / province
- 事件增删改 → admin / province（全量）、school（仅限本校球队事件）
- 恢复事件版本 → 仅 admin
- 文件上传 / 新版本 / 归档 / 改标题 → admin / province（全量）、school（仅限本校比赛）
- 文件解档 → 仅 admin
- 赛事归档 finalize → admin / province
- 取消归档 unfinalize → 仅 admin
- 操作日志 → 仅 admin
"""
import os

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AthleteProfile
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.permissions import (
    RolePermission,
    get_user_school,
    role_permission,
    user_is_admin,
    user_is_admin_or_province,
    user_is_ops,
)
from apps.live.models import Match, MatchStatus
from apps.org.models import Team

from . import services
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
from .serializers import (
    DOC_TYPE_PART_KEYS,
    DocumentVersionUploadSerializer,
    EventRevisionSerializer,
    FinalizeRequestSerializer,
    MatchDocumentCreateSerializer,
    MatchDocumentSerializer,
    MatchDocumentUpdateSerializer,
    MatchDocumentVersionSerializer,
    MatchEventSerializer,
    MatchEventWriteSerializer,
    MatchOfficialSerializer,
    MatchRecordSerializer,
    OperationLogSerializer,
    RefereeSerializer,
    UnfinalizeRequestSerializer,
    validate_uploaded_file,
)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

OPS_ROLES = ('admin', 'province', 'school')


def _scope_q(user) -> Q | None:
    """返回校级过滤条件：match 的主客队至少一支属于管辖学校。非校级返回 None。"""
    school = get_user_school(user)
    if school is None:
        return None
    return Q(home_team__school=school) | Q(away_team__school=school)


def _assert_match_scope(user, match: Match):
    """校级访问越界抛 BusinessException(NOT_FOUND)，避免暴露比赛存在性。"""
    q = _scope_q(user)
    if q is not None and not qs_match(match, q):
        raise BusinessException(ResponseCode.NOT_FOUND, '比赛不存在', status_code=404)


def qs_match(match: Match, q: Q) -> bool:
    # 用匹配查询验证是否在范围内
    return Match.objects.filter(pk=match.pk).filter(q).exists()


def _assert_match_editable(user, match: Match):
    """比赛未开始或已归档 → 禁止事件写入。"""
    if match.status == MatchStatus.NOT_STARTED:
        raise BusinessException(ResponseCode.MATCH_NOT_STARTED)
    if services.is_match_locked(match):
        raise BusinessException(ResponseCode.MATCH_EVENTED_LOCKED)


def _assert_write_admin_or_province(user):
    if not user_is_admin_or_province(user):
        _forbidden()


def _assert_against_lock(user, match: Match):
    # finalize 之后 events_locked → 所有事件写操作被拒
    if services.is_match_locked(match):
        raise BusinessException(ResponseCode.MATCH_EVENTED_LOCKED)


def _trace(request):
    return services.get_trace_id(request)


def _ip(request):
    return services.get_ip(request)


def _forbidden(msg='无操作权限'):
    raise BusinessException(ResponseCode.FORBIDDEN, msg, status_code=403)


def _not_found(msg='资源不存在'):
    raise BusinessException(ResponseCode.NOT_FOUND, msg, status_code=404)


def _conflict(msg=None):
    raise BusinessException(ResponseCode.OPTIMISTIC_LOCK_CONFLICT, msg, status_code=409)


# ---------------------------------------------------------------------------
# 裁判主档
# ---------------------------------------------------------------------------

class RefereeViewSet(viewsets.ModelViewSet):
    queryset = Referee.objects.all()
    serializer_class = RefereeSerializer
    permission_classes = [role_permission(*OPS_ROLES)]

    def get_queryset(self):
        qs = Referee.objects.all()
        kw = self.request.query_params.get('keyword', '').strip()
        level = self.request.query_params.get('level', '').strip()
        if kw:
            qs = qs.filter(Q(name__icontains=kw) | Q(certificate_no__icontains=kw))
        if level:
            qs = qs.filter(level=level)
        return qs

    def perform_create(self, serializer):
        try:
            serializer.save()
        except IntegrityError:
            raise BusinessException(ResponseCode.DUPLICATE_SUBMIT, '裁判证号已存在')

    def perform_update(self, serializer):
        try:
            serializer.save()
        except IntegrityError:
            raise BusinessException(ResponseCode.DUPLICATE_SUBMIT, '裁判证号已存在')

    def perform_destroy(self, instance):
        if MatchOfficial.objects.filter(referee=instance).exists():
            raise BusinessException(ResponseCode.REF_IN_USE)
        instance.delete()
        services.log_operation(
            user=self.request.user, action='delete_referee',
            resource_type='referee', resource_id=instance.id,
            before_data=RefereeSerializer(instance).data,
            ip=_ip(self.request), trace_id=_trace(self.request),
        )

    def _guard_write_only_admin_or_province(self):
        if self.request.method not in ('GET', 'HEAD', 'OPTIONS'):
            _assert_write_admin_or_province(self.request.user)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self._guard_write_only_admin_or_province()


# ---------------------------------------------------------------------------
# 比赛裁判安排（嵌套 /matches/{match_id}/officials/）
# ---------------------------------------------------------------------------

class MatchOfficialViewSet(viewsets.ModelViewSet):
    serializer_class = MatchOfficialSerializer
    permission_classes = [role_permission(*OPS_ROLES)]

    def get_queryset(self):
        match_id = self.kwargs.get('match_id')
        if match_id:
            _assert_match_scope(self.request.user, Match.objects.get(pk=match_id))
            return MatchOfficial.objects.filter(match_id=match_id)
        return MatchOfficial.objects.none()

    def perform_create(self, serializer):
        _assert_write_admin_or_province(self.request.user)
        match_id = self.kwargs.get('match_id')
        match = Match.objects.get(pk=match_id)
        _assert_match_scope(self.request.user, match)
        try:
            serializer.save(match=match)
        except IntegrityError:
            raise BusinessException(ResponseCode.DUPLICATE_SUBMIT, '同一比赛的裁判安排重复')
        services.log_operation(
            user=self.request.user, action='assign_official',
            resource_type='match_official', resource_id=serializer.instance.id,
            after_data=MatchOfficialSerializer(serializer.instance).data,
            ip=_ip(self.request), trace_id=_trace(self.request),
        )

    def perform_destroy(self, instance):
        _assert_write_admin_or_province(self.request.user)
        instance.delete()
        services.log_operation(
            user=self.request.user, action='remove_official',
            resource_type='match_official', resource_id=instance.id,
            before_data=MatchOfficialSerializer(instance).data,
            ip=_ip(self.request), trace_id=_trace(self.request),
        )


# ---------------------------------------------------------------------------
# 比赛事件
# ---------------------------------------------------------------------------

class MatchEventViewSet(viewsets.ModelViewSet):
    permission_classes = [role_permission(*OPS_ROLES)]

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return MatchEventWriteSerializer
        return MatchEventSerializer

    def get_queryset(self):
        qs = MatchEvent.objects.select_related('match', 'team', 'athlete', 'related_athlete')
        match_id = self.kwargs.get('match_id')
        if match_id:
            match = Match.objects.get(pk=match_id)
            _assert_match_scope(self.request.user, match)
            qs = qs.filter(match_id=match_id)
        scope_q = _scope_q(self.request.user)
        if scope_q is not None:
            # 通过 match 间接过滤范围；同时避免拿到其他学校的事件
            qs = qs.filter(match__in=Match.objects.filter(scope_q))
        include_deleted = self.request.query_params.get('include_deleted', '0').lower() in ('1', 'true')
        if not include_deleted:
            qs = qs.filter(is_deleted=False)
        event_type = self.request.query_params.get('event_type', '').strip()
        if event_type:
            qs = qs.filter(event_type=event_type)
        team_id = self.request.query_params.get('team_id', '').strip()
        if team_id.isdigit():
            qs = qs.filter(team_id=int(team_id))
        return qs

    def get_object(self):
        """取详情/修改/删除/恢复时允许读到已软删除记录。"""
        try:
            obj = MatchEvent.objects.select_related('match', 'team', 'athlete', 'related_athlete').get(
                pk=self.kwargs['pk'],
            )
        except MatchEvent.DoesNotExist:
            raise BusinessException(ResponseCode.NOT_FOUND, '事件不存在', status_code=404)
        _assert_match_scope(self.request.user, obj.match)
        return obj

    # ----- create -----
    def perform_create(self, serializer):
        match_id = self.kwargs.get('match_id') or serializer.validated_data.get('match_id')
        match = serializer.validated_data.get('match')
        if match is None:
            match = Match.objects.get(pk=match_id)
        serializer.validated_data['match'] = match

        _assert_match_scope(self.request.user, match)
        _assert_match_editable(self.request.user, match)

        # 校级仅能写本校球队事件
        school = get_user_school(self.request.user)
        if school is not None and serializer.validated_data.get('team') is not None:
            team = serializer.validated_data['team']
            if team.school_id != school.id:
                _forbidden('校级账号只能录入本校球队事件')

        with transaction.atomic():
            event = serializer.save(
                created_by=self.request.user.id,
                updated_by=self.request.user.id,
                version=1,
            )
            services.create_event_revision(
                event, 'create',
                changed_by=self.request.user,
                trace_id=_trace(self.request),
            )
        services.log_operation(
            user=self.request.user, action='create_event',
            resource_type='match_event', resource_id=event.id,
            after_data=services.snapshot_event(event),
            ip=_ip(self.request), trace_id=_trace(self.request),
        )

    # ----- update -----
    def perform_update(self, serializer):
        change_reason = (self.request.data or {}).get('change_reason')
        old_version = self.request.data.get('version')

        # 校级：不能修改（只允许 admin/province）
        if not user_is_admin_or_province(self.request.user):
            _forbidden()

        instance = self.get_object()
        if instance.is_deleted:
            raise BusinessException(ResponseCode.BUSINESS_ERROR, '已删除事件不可修改，请先恢复')

        _assert_against_lock(self.request.user, instance.match)
        if old_version is None:
            raise BusinessException(ResponseCode.PARAM_INVALID, '修改事件必须携带 version 做乐观锁')
        if instance.version != int(old_version):
            _conflict()
        if not change_reason:
            raise BusinessException(ResponseCode.PARAM_INVALID, '修改事件必须填写 change_reason')

        before = services.snapshot_event(instance)
        with transaction.atomic():
            new_version = instance.version + 1
            serializer.save(updated_by=self.request.user.id, version=new_version)
            instance.refresh_from_db()
            services.create_event_revision(
                instance, 'update', reason=change_reason,
                changed_by=self.request.user, trace_id=_trace(self.request),
            )
        services.log_operation(
            user=self.request.user, action='update_event',
            resource_type='match_event', resource_id=instance.id,
            before_data=before, after_data=services.snapshot_event(instance),
            ip=_ip(self.request), trace_id=_trace(self.request),
        )

    # ----- soft delete -----
    def perform_destroy(self, instance):
        _assert_write_admin_or_province(self.request.user)
        _assert_against_lock(self.request.user, instance.match)
        reason = (self.request.query_params.get('change_reason')
                  or (self.request.data or {}).get('change_reason') or '').strip()
        if not reason:
            raise BusinessException(ResponseCode.PARAM_INVALID, '删除事件必须填写 change_reason')

        # 幂等：已删除直接返回
        if instance.is_deleted:
            return

        with transaction.atomic():
            instance.is_deleted = True
            instance.deleted_at = timezone.now()
            instance.deleted_by = self.request.user.id
            instance.delete_reason = reason
            instance.updated_by = self.request.user.id
            instance.version += 1
            instance.save()
            services.create_event_revision(
                instance, 'delete', reason=reason,
                changed_by=self.request.user, trace_id=_trace(self.request),
            )
        services.log_operation(
            user=self.request.user, action='delete_event',
            resource_type='match_event', resource_id=instance.id,
            before_data={'is_deleted': True, 'reason': reason},
            ip=_ip(self.request), trace_id=_trace(self.request),
        )

    # ----- revisions -----
    @action(detail=True, methods=['get'])
    def revisions(self, request, pk=None):
        event = self.get_object()
        serializer = EventRevisionSerializer(event.revisions.order_by('-version_no'), many=True)
        return Response(serializer.data)

    # ----- restore -----
    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        if not user_is_admin(request.user):
            _forbidden()
        event = self.get_object()
        reason = (request.data or {}).get('change_reason', '').strip()
        if not reason:
            raise BusinessException(ResponseCode.PARAM_INVALID, '恢复事件必须填写 change_reason')
        if not event.is_deleted:
            raise BusinessException(ResponseCode.BUSINESS_ERROR, '事件未删除，无需恢复')

        with transaction.atomic():
            event.is_deleted = False
            event.deleted_at = None
            event.deleted_by = None
            event.delete_reason = ''
            event.updated_by = request.user.id
            event.version += 1
            event.save()
            services.create_event_revision(
                event, 'restore', reason=reason,
                changed_by=request.user, trace_id=_trace(request),
            )
        return Response(MatchEventSerializer(event).data)


# ---------------------------------------------------------------------------
# 事件汇总（APIView）
# ---------------------------------------------------------------------------

class MatchEventSummaryView(APIView):
    permission_classes = [role_permission(*OPS_ROLES)]

    def get(self, request, match_id):
        match = Match.objects.get(pk=match_id)
        _assert_match_scope(request.user, match)

        home = match.home_team_id
        away = match.away_team_id
        events = MatchEvent.objects.filter(match=match, is_deleted=False).exclude(
            period='shootout',
        )

        # 初始化
        summary = {
            'home': {},
            'away': {},
        }
        for et in MatchEvent._meta.get_field('event_type').choices:
            key = et[0]
            summary['home'][key] = 0
            summary['away'][key] = 0

        for e in events:
            tid = e.team_id
            side = 'home' if tid == home else ('away' if tid == away else None)
            if side is None:
                continue
            summary[side][e.event_type] += 1

        return Response({
            'match_id': match.id,
            'home_team': {'id': match.home_team_id, 'name': match.home_team_name},
            'away_team': {'id': match.away_team_id, 'name': match.away_team_name},
            'summary': summary,
        })


# ---------------------------------------------------------------------------
# 赛事文件档案
# ---------------------------------------------------------------------------

class MatchDocumentViewSet(viewsets.ModelViewSet):
    """
    - 列表 / 详情 / 创建 / 归档 / 解档 / versions 上传新文件
    - 创建必须 multipart：doc_type + part_key + file + 可选 title / change_note
    - 更新仅可改 title（归档后会在 perform_update 前拒绝）
    - 文件 preview / download 见下方 FileView / PreviewView / DownloadView
    """

    permission_classes = [role_permission(*OPS_ROLES)]

    def get_queryset(self):
        match_id = self.kwargs.get('match_id')
        if match_id:
            match = Match.objects.get(pk=match_id)
            _assert_match_scope(self.request.user, match)
            qs = MatchDocument.objects.filter(match_id=match_id)
        else:
            qs = MatchDocument.objects.none()
        scope_q = _scope_q(self.request.user)
        if scope_q is not None:
            qs = qs.filter(match__in=Match.objects.filter(scope_q))
        doc_type = self.request.query_params.get('doc_type', '').strip()
        if doc_type:
            qs = qs.filter(doc_type=doc_type)
        part_key = self.request.query_params.get('part_key', '').strip()
        if part_key:
            qs = qs.filter(part_key=part_key)
        status = self.request.query_params.get('status', '').strip()
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_serializer_class(self):
        if self.action == 'create':
            return MatchDocumentCreateSerializer
        if self.action in ('update', 'partial_update'):
            return MatchDocumentUpdateSerializer
        return MatchDocumentSerializer

    def get_object(self):
        try:
            doc = MatchDocument.objects.get(pk=self.kwargs['pk'])
        except MatchDocument.DoesNotExist:
            raise BusinessException(ResponseCode.NOT_FOUND, '档案不存在', status_code=404)
        _assert_match_scope(self.request.user, doc.match)
        return doc

    def create(self, request, *args, **kwargs):
        # 嵌套路由注入 match
        match_id = self.kwargs.get('match_id')
        match = Match.objects.get(pk=match_id)
        _assert_match_scope(request.user, match)
        _assert_write_admin_or_province_or_school(request.user, match)

        s = MatchDocumentCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        doc_type = s.validated_data['doc_type']
        part_key = (s.validated_data.get('part_key') or 'main')
        # 分册约束
        allowed = DOC_TYPE_PART_KEYS.get(doc_type)
        if allowed and part_key not in allowed:
            raise BusinessException(ResponseCode.PARAM_INVALID,
                                     f'{doc_type} 不允许分册 {part_key}，仅允许 {sorted(allowed)}')

        file_obj = s.validated_data['file']
        sha256, mime, size, ext = validate_uploaded_file(file_obj, doc_type)

        # 同分册重复 → DUPLICATE_SUBMIT
        if MatchDocument.objects.filter(match=match, doc_type=doc_type, part_key=part_key).exists():
            raise BusinessException(ResponseCode.DUPLICATE_SUBMIT, '同类型同分册档案已存在')

        # 文件内容重复（同 doc_type 下）
        if MatchDocumentVersion.objects.filter(sha256=sha256).exists():
            raise BusinessException(ResponseCode.DUPLICATE_SUBMIT, '文件内容重复，跳过上传')

        title = (s.validated_data.get('title') or '').strip()
        change_note = (s.validated_data.get('change_note') or '').strip() or '原件'

        with transaction.atomic():
            doc = MatchDocument.objects.create(
                match=match, doc_type=doc_type, part_key=part_key,
                title=title or doc_type,
                current_version_no=1,
            )
            rel_path, sha256_v, size_v, ext_v, mime_v = services.save_uploaded_file(doc, 1, file_obj)
            MatchDocumentVersion.objects.create(
                document=doc, version_no=1, file_path=rel_path,
                original_name=file_obj.name, file_ext=ext_v,
                mime_type=mime_v, file_size=size_v, sha256=sha256_v,
                change_note=change_note, uploaded_by=request.user.id,
            )

        services.log_operation(
            user=request.user, action='create_document',
            resource_type='match_document', resource_id=doc.id,
            after_data={'doc_type': doc_type, 'part_key': part_key,
                        'version_no': 1, 'sha256': sha256_v},
            ip=_ip(request), trace_id=_trace(request),
        )
        return Response(MatchDocumentSerializer(doc).data, status=201)

    def perform_update(self, serializer):
        doc = self.get_object()
        _assert_write_admin_or_province_or_school(self.request.user, doc.match)
        if doc.status == 'archived':
            raise BusinessException(ResponseCode.BUSINESS_ERROR, '档案已归档，禁止修改')
        before = MatchDocumentSerializer(doc).data
        serializer.save()
        services.log_operation(
            user=self.request.user, action='update_document',
            resource_type='match_document', resource_id=doc.id,
            before_data=before,
            after_data=MatchDocumentSerializer(doc).data,
            ip=_ip(self.request), trace_id=_trace(self.request),
        )

    def destroy(self, request, *args, **kwargs):
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '档案不允许物理删除，请使用归档代替')

    # ----- versions 列表 / 上传新版本 -----
    @action(detail=True, methods=['get', 'post'])
    def versions(self, request, pk=None):
        doc = self.get_object()
        if request.method == 'GET':
            return Response(
                MatchDocumentVersionSerializer(doc.versions.order_by('version_no'), many=True).data,
            )
        # POST 上传新版本
        _assert_write_admin_or_province_or_school(request.user, doc.match)
        if doc.status == 'archived':
            raise BusinessException(ResponseCode.MATCH_EVENTED_LOCKED, '档案已归档，禁止上传新版本')

        s = DocumentVersionUploadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        file_obj = s.validated_data['file']
        change_note = (s.validated_data.get('change_note') or '').strip()

        sha256, mime, size, ext = validate_uploaded_file(file_obj, doc.doc_type)
        if MatchDocumentVersion.objects.filter(sha256=sha256).exists():
            raise BusinessException(ResponseCode.DUPLICATE_SUBMIT, '文件内容重复，跳过上传')

        with transaction.atomic():
            version_no = doc.current_version_no + 1
            rel_path, sha256_v, size_v, ext_v, mime_v = services.save_uploaded_file(doc, version_no, file_obj)
            version = MatchDocumentVersion.objects.create(
                document=doc, version_no=version_no, file_path=rel_path,
                original_name=file_obj.name, file_ext=ext_v,
                mime_type=mime_v, file_size=size_v, sha256=sha256_v,
                change_note=change_note, uploaded_by=request.user.id,
            )
            doc.current_version_no = version_no
            doc.save()
        services.log_operation(
            user=request.user, action='upload_version',
            resource_type='document_version', resource_id=version.id,
            after_data={'document_id': doc.id, 'version_no': version_no, 'sha256': sha256_v},
            ip=_ip(request), trace_id=_trace(request),
        )
        return Response(MatchDocumentVersionSerializer(version).data, status=201)

    # ----- archive / unarchive -----
    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        _assert_write_admin_or_province_or_school(request.user, self.get_object().match)
        doc = self.get_object()
        if doc.status == 'archived':
            return Response(MatchDocumentSerializer(doc).data)
        doc.status = 'archived'
        doc.archived_at = timezone.now()
        doc.archived_by = request.user.id
        doc.save()
        services.log_operation(
            user=request.user, action='archive_document',
            resource_type='match_document', resource_id=doc.id,
            ip=_ip(request), trace_id=_trace(request),
        )
        return Response(MatchDocumentSerializer(doc).data)

    @action(detail=True, methods=['post'])
    def unarchive(self, request, pk=None):
        if not user_is_admin(request.user):
            _forbidden()
        doc = self.get_object()
        if doc.status != 'archived':
            raise BusinessException(ResponseCode.BUSINESS_ERROR, '档案未归档，无需解档')
        doc.status = 'active'
        doc.archived_at = None
        doc.archived_by = None
        doc.save()
        services.log_operation(
            user=request.user, action='unarchive_document',
            resource_type='match_document', resource_id=doc.id,
            ip=_ip(request), trace_id=_trace(request),
        )
        return Response(MatchDocumentSerializer(doc).data)


def _assert_write_admin_or_province_or_school(user, match: Match):
    """档案写操作：admin/province 全量；school 仅限本校比赛。"""
    school = get_user_school(user)
    if school is None:
        _assert_write_admin_or_province(user)
        return
    if not qs_match(match, Q(home_team__school=school) | Q(away_team__school=school)):
        _forbidden('校级账号只能操作本校比赛档案')


# ---------------------------------------------------------------------------
# 档案文件流：preview / download
# ---------------------------------------------------------------------------

class _DocumentFileBase(APIView):
    permission_classes = [role_permission(*OPS_ROLES)]

    def _get_version(self, request, version_id):
        try:
            version = MatchDocumentVersion.objects.select_related('document__match').get(pk=version_id)
        except MatchDocumentVersion.DoesNotExist:
            raise BusinessException(ResponseCode.NOT_FOUND, '文件版本不存在')
        _assert_match_scope(request.user, version.document.match)
        return version, _resolve_path(version.file_path)


def _resolve_path(rel_path: str):
    root = getattr(settings, 'PRIVATE_STORAGE_ROOT', None) or settings.MEDIA_ROOT
    return os.path.abspath(os.path.join(root, rel_path))


class DocumentDownloadView(_DocumentFileBase):
    def get(self, request, version_id):
        version, full_path = self._get_version(request, version_id)
        if not os.path.exists(full_path):
            raise BusinessException(ResponseCode.BUSINESS_ERROR, '文件已丢失')
        with open(full_path, 'rb') as f:
            content = f.read()
        response = HttpResponse(content, content_type=version.mime_type or 'application/octet-stream')
        response['Content-Disposition'] = f'attachment; filename="{version.original_name}"'
        services.log_operation(
            user=request.user, action='download_document',
            resource_type='document_version', resource_id=version.id,
            ip=_ip(request), trace_id=_trace(request),
        )
        return response


class DocumentPreviewView(_DocumentFileBase):
    """仅支持 PDF / 图片预览，其余类型 400。"""

    PREVIEWABLE_PREFIXES = ('application/pdf', 'image/')

    def get(self, request, version_id):
        version, full_path = self._get_version(request, version_id)
        if not version.mime_type or not version.mime_type.startswith(self.PREVIEWABLE_PREFIXES):
            raise BusinessException(ResponseCode.FILE_TYPE_INVALID, '仅支持 PDF / 图片在线预览')
        if not os.path.exists(full_path):
            raise BusinessException(ResponseCode.BUSINESS_ERROR, '文件已丢失')
        with open(full_path, 'rb') as f:
            content = f.read()
        services.log_operation(
            user=request.user, action='preview_document',
            resource_type='document_version', resource_id=version.id,
            ip=_ip(request), trace_id=_trace(request),
        )
        return HttpResponse(content, content_type=version.mime_type)


# ---------------------------------------------------------------------------
# 比分校验 / 归档状态 / finalize / unfinalize
# ---------------------------------------------------------------------------

class ScoreCheckView(APIView):
    permission_classes = [role_permission(*OPS_ROLES)]

    def get(self, request, match_id):
        match = Match.objects.get(pk=match_id)
        _assert_match_scope(request.user, match)
        return Response(services.check_goal_vs_score(match))


class RecordStateView(APIView):
    permission_classes = [role_permission(*OPS_ROLES)]

    def get(self, request, match_id):
        match = Match.objects.get(pk=match_id)
        _assert_match_scope(request.user, match)
        record, _ = MatchRecord.objects.get_or_create(match=match)
        payload = MatchRecordSerializer(record).data
        payload['score_check'] = services.check_goal_vs_score(match)
        return Response(payload)


class FinalizeView(APIView):
    permission_classes = [role_permission(*OPS_ROLES)]

    def post(self, request, match_id):
        _assert_write_admin_or_province(request.user)
        match = Match.objects.get(pk=match_id)
        _assert_match_scope(request.user, match)
        s = FinalizeRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        record = services.finalize_match(match, request.user, s.validated_data.get('lock_note', ''))
        services.log_operation(
            user=request.user, action='finalize_match',
            resource_type='match_record', resource_id=record.id,
            after_data={'events_locked': True},
            ip=_ip(request), trace_id=_trace(request),
        )
        return Response(MatchRecordSerializer(record).data)


class UnfinalizeView(APIView):
    permission_classes = [role_permission(*OPS_ROLES)]

    def post(self, request, match_id):
        if not user_is_admin(request.user):
            _forbidden()
        match = Match.objects.get(pk=match_id)
        _assert_match_scope(request.user, match)
        s = UnfinalizeRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        record = services.unfinalize_match(match, request.user, s.validated_data['reason'])
        services.log_operation(
            user=request.user, action='unfinalize_match',
            resource_type='match_record', resource_id=record.id,
            after_data={'events_locked': False, 'unlock_reason': s.validated_data['reason']},
            ip=_ip(request), trace_id=_trace(request),
        )
        return Response(MatchRecordSerializer(record).data)


# ---------------------------------------------------------------------------
# 操作日志（仅 admin）
# ---------------------------------------------------------------------------

class OperationLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = OperationLog.objects.all()
    serializer_class = OperationLogSerializer

    def get_permissions(self):
        if not user_is_admin(self.request.user):
            _forbidden()
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = OperationLog.objects.all()
        u = self.request.query_params
        if u.get('user_id'):
            qs = qs.filter(user_id=int(u['user_id']))
        if u.get('user_role'):
            qs = qs.filter(user_role=u['user_role'])
        if u.get('action'):
            qs = qs.filter(action=u['action'])
        if u.get('resource_type'):
            qs = qs.filter(resource_type=u['resource_type'])
        if u.get('resource_id'):
            qs = qs.filter(resource_id=int(u['resource_id']))
        if u.get('date_from'):
            qs = qs.filter(created_at__date__gte=u['date_from'])
        if u.get('date_to'):
            qs = qs.filter(created_at__date__lte=u['date_to'])
        return qs
