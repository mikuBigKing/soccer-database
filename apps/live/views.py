"""赛事直播视图：公众端 + 管理端。"""
import os

from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import SchoolProfile, UserRole
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.pagination import paginate
from apps.common.permissions import is_admin, live_permission

from .models import EventType, Match, MatchFile, MatchStatus
from .serializers import (
    AdminMatchDetailSerializer,
    EventStoreSerializer,
    EventUpdateSerializer,
    FaceVerifyRecordSerializer,
    FaceVerifySerializer,
    LineupSerializer,
    LiveLinkSerializer,
    MatchDetailSerializer,
    MatchEventSerializer,
    MatchFileSerializer,
    MatchFileUploadSerializer,
    MatchListSerializer,
    MatchStatusSerializer,
    MatchStoreSerializer,
    MatchUpdateSerializer,
    RefereeSerializer,
    RefereesSerializer,
)
from .services import (
    FaceVerifyService,
    MatchEventService,
    MatchFileService,
    MatchService,
    RefereeService,
)


def _get_match(pk: int) -> Match:
    match = Match.objects.filter(pk=pk).first()
    if match is None:
        raise BusinessException(ResponseCode.NOT_FOUND, '比赛不存在', status_code=404)
    return match


# ---------------- 校级数据范围辅助 ----------------

def _school_id_of(user) -> int | None:
    """获取当前用户绑定的学校 id；未绑定返回 None。"""
    if not user or not user.is_authenticated:
        return None
    if is_admin(user):
        return None
    profile = getattr(user, 'school_profile', None)
    return profile.school_id if profile else None


def _school_match_queryset(user):
    """返回「主队或客队属于本校」的比赛查询集；管理员/省级返回全量，校级未绑定返回空。"""
    if is_admin(user) or getattr(user, 'role', None) == UserRole.PROVINCE:
        return Match.objects.all()
    school_id = _school_id_of(user)
    if school_id is None:
        return Match.objects.none()
    return Match.objects.filter(
        Q(home_team__school_id=school_id) | Q(away_team__school_id=school_id)
    )


def _ensure_school_can_host(user, home_team_id, away_team_id):
    """校验举办比赛的三条硬性规则。"""
    # ① 参赛球队必选
    if not home_team_id and not away_team_id:
        raise BusinessException(ResponseCode.PARAM_INVALID, '必须选择至少一支参赛球队')
    # ② 球队必须存在
    from apps.org.models import Team
    team_ids = [tid for tid in (home_team_id, away_team_id) if tid]
    existing = set(Team.objects.filter(id__in=team_ids).values_list('id', flat=True))
    missing = [tid for tid in team_ids if tid not in existing]
    if missing:
        raise BusinessException(ResponseCode.PARAM_INVALID, f'球队不存在: {missing}')
    # ③ 必须含本校球队
    school_id = _school_id_of(user)
    if school_id is None:
        raise BusinessException(ResponseCode.FORBIDDEN, '当前账号未绑定学校')
    school_team_ids = set(
        Team.objects.filter(id__in=team_ids, school_id=school_id).values_list('id', flat=True)
    )
    if not school_team_ids:
        raise BusinessException(ResponseCode.FORBIDDEN, '仅可举办含本校球队的比赛')


def _ensure_school_match(user, match: Match):
    """校验比赛是否属于本校（主队或客队），否则抛 20004。管理员/省级放行，校级未绑定拒绝。"""
    if is_admin(user) or getattr(user, 'role', None) == UserRole.PROVINCE:
        return
    school_id = _school_id_of(user)
    if school_id is None:
        raise BusinessException(ResponseCode.FORBIDDEN, '当前账号未绑定学校')
    home_school = getattr(match.home_team, 'school_id', None)
    away_school = getattr(match.away_team, 'school_id', None)
    if home_school != school_id and away_school != school_id:
        raise BusinessException(ResponseCode.FORBIDDEN, '无权操作他校比赛')


def _get_event(pk: int):
    from .models import MatchEvent

    event = MatchEvent.objects.filter(pk=pk).first()
    if event is None:
        raise BusinessException(ResponseCode.NOT_FOUND, '事件不存在', status_code=404)
    return event


def _raise_file_error(serializer, field: str = 'file'):
    """把文件类校验错误映射为 10006 / 10007。"""
    errors = serializer.errors.get(field) or []
    text = ' '.join(str(e) for e in errors)
    code = ResponseCode.PARAM_INVALID
    if 'image' in text.lower() or '格式' in text or 'type' in text.lower():
        code = ResponseCode.FILE_TYPE_INVALID
    if 'size' in text.lower() or '大小' in text or '10' in text:
        code = ResponseCode.FILE_SIZE_EXCEEDED
    raise BusinessException(code, str(errors[0]) if errors else None)


def _event_action_result(match_id: int, event) -> dict:
    """事件增删改统一回包：事件 + 最新比分 + 版本号（前端 EventActionResult）。"""
    match = Match.objects.get(pk=match_id)
    return {
        'event': MatchEventSerializer(event).data if event is not None else None,
        'home_score': match.home_score,
        'away_score': match.away_score,
        'version': match.version,
    }


# ---------------------------------------------------------------------------
# 公众端（免登）
# ---------------------------------------------------------------------------

class PublicMatchListView(APIView):
    def get(self, request):
        queryset = Match.objects.all()
        status_value = request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        group_type = request.query_params.get('group_type')
        if group_type:
            queryset = queryset.filter(group_type=group_type)

        data = paginate(queryset, request)
        data['list'] = MatchListSerializer(data['list'], many=True).data
        return Response(data)


class PublicMatchDetailView(APIView):
    def get(self, request, pk: int):
        match = _get_match(pk)
        payload = MatchDetailSerializer(match).data
        payload['referees'] = RefereeSerializer(match.referees.all(), many=True).data
        payload['events'] = MatchEventSerializer(match.events.all(), many=True).data
        return Response(payload)


class PublicMatchEventListView(APIView):
    def get(self, request, pk: int):
        match = _get_match(pk)
        queryset = match.events.all()
        since_id = request.query_params.get('since_id')
        if since_id:
            try:
                queryset = queryset.filter(id__gt=int(since_id))
            except (TypeError, ValueError):
                pass
        return Response(MatchEventSerializer(queryset, many=True).data)


# ---------------------------------------------------------------------------
# 管理端
# ---------------------------------------------------------------------------

class AdminMatchListCreateView(APIView):
    def get_permissions(self):
        perm = 'live:match:view' if self.request.method == 'GET' else 'live:match:create'
        return [live_permission(perm)()]

    def get(self, request):
        queryset = _school_match_queryset(request.user)
        status_value = request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        group_type = request.query_params.get('group_type')
        if group_type:
            queryset = queryset.filter(group_type=group_type)
        keyword = request.query_params.get('keyword')
        if keyword:
            queryset = queryset.filter(
                Q(home_team_name__icontains=keyword) | Q(away_team_name__icontains=keyword)
            )

        data = paginate(queryset, request)
        data['list'] = AdminMatchDetailSerializer(data['list'], many=True).data
        return Response(data)

    def post(self, request):
        serializer = MatchStoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # 校级举办比赛三条硬性规则
        if _school_id_of(request.user) is not None:
            _ensure_school_can_host(
                request.user,
                data.get('home_team_id'),
                data.get('away_team_id'),
            )
        match = MatchService().create(data, request.user.id)
        return Response(AdminMatchDetailSerializer(match).data)


class AdminMatchDetailView(APIView):
    def get_permissions(self):
        perm = {
            'GET': 'live:match:view',
            'PUT': 'live:match:update',
            'DELETE': 'live:match:delete',
        }.get(self.request.method, 'live:match:view')
        return [live_permission(perm)()]

    def get(self, request, pk: int):
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        return Response(AdminMatchDetailSerializer(match).data)

    def put(self, request, pk: int):
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        serializer = MatchUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        # 校级编辑比赛时，若修改了参赛球队，需重新校验三条硬性规则
        if _school_id_of(request.user) is not None:
            home_team_id = data.get('home_team_id', match.home_team_id)
            away_team_id = data.get('away_team_id', match.away_team_id)
            _ensure_school_can_host(request.user, home_team_id, away_team_id)
        version = data.pop('version')
        updated = MatchService().update(match, data, version, request.user.id)
        return Response(AdminMatchDetailSerializer(updated).data)

    def delete(self, request, pk: int):
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        MatchService().delete(match, request.user.id)
        return Response({'id': pk})


class AdminMatchStatusView(APIView):
    permission_classes = [live_permission('live:match:update')]

    def patch(self, request, pk: int):
        serializer = MatchStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        match = MatchService().change_status(
            match,
            serializer.validated_data['action'],
            serializer.validated_data['version'],
            request.user.id,
        )
        return Response(AdminMatchDetailSerializer(match).data)


class AdminMatchLineupView(APIView):
    permission_classes = [live_permission('live:match:update')]

    def put(self, request, pk: int):
        serializer = LineupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        match = MatchService().update_lineup(
            match,
            serializer.validated_data['lineup_home'],
            serializer.validated_data['lineup_away'],
            serializer.validated_data['version'],
            request.user.id,
        )
        return Response(AdminMatchDetailSerializer(match).data)


class AdminMatchEventView(APIView):
    permission_classes = [live_permission('live:event:manage')]

    def get(self, request, pk: int):
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        queryset = match.events.all()
        since_id = request.query_params.get('since_id')
        if since_id:
            try:
                queryset = queryset.filter(id__gt=int(since_id))
            except (TypeError, ValueError):
                pass
        return Response(MatchEventSerializer(queryset, many=True).data)

    def post(self, request, pk: int):
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        serializer = EventStoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = MatchEventService().add_event(pk, serializer.validated_data, request.user.id)
        return Response(_event_action_result(pk, event))


class AdminEventDetailView(APIView):
    permission_classes = [live_permission('live:event:manage')]

    def put(self, request, pk: int):
        event = _get_event(pk)
        _ensure_school_match(request.user, event.match)
        match_id = event.match_id
        serializer = EventUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = MatchEventService().update_event(event, serializer.validated_data, request.user.id)
        return Response(_event_action_result(match_id, updated))

    def delete(self, request, pk: int):
        event = _get_event(pk)
        _ensure_school_match(request.user, event.match)
        match_id = event.match_id
        MatchEventService().delete_event(event, request.user.id)
        return Response(_event_action_result(match_id, None))


class AdminRefereeView(APIView):
    permission_classes = [live_permission('live:referee:manage')]

    def get(self, request, pk: int):
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        return Response(RefereeSerializer(RefereeService().list_for_match(match), many=True).data)

    def put(self, request, pk: int):
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        serializer = RefereesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        referees = RefereeService().replace(match, serializer.validated_data['referees'], request.user.id)
        return Response(RefereeSerializer(referees, many=True).data)


class AdminMatchFileView(APIView):
    permission_classes = [live_permission('live:file:manage')]

    def get(self, request, pk: int):
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        return Response(MatchFileSerializer(MatchFileService().list_for_match(match), many=True).data)

    def post(self, request, pk: int):
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        serializer = MatchFileUploadSerializer(data=request.data)
        if not serializer.is_valid():
            _raise_file_error(serializer)
        record = MatchFileService().upload(
            match,
            serializer.validated_data['type'],
            serializer.validated_data['file'],
            request.user.id,
        )
        return Response(MatchFileSerializer(record).data)


class AdminFileDownloadView(APIView):
    permission_classes = [live_permission('live:file:manage')]

    def get(self, request, pk: int):
        record = MatchFile.objects.filter(pk=pk).first()
        if record is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '文件不存在', status_code=404)
        _ensure_school_match(request.user, record.match)
        path = MatchFileService().absolute_path(record)
        if not os.path.exists(path):
            raise BusinessException(ResponseCode.NOT_FOUND, '文件已丢失', status_code=404)
        return FileResponse(open(path, 'rb'), as_attachment=True, filename=record.file_name, content_type=record.mime)


class AdminFileDeleteView(APIView):
    permission_classes = [live_permission('live:file:manage')]

    def delete(self, request, pk: int):
        record = MatchFile.objects.filter(pk=pk).first()
        if record is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '文件不存在', status_code=404)
        _ensure_school_match(request.user, record.match)
        MatchFileService().delete(record, request.user.id)
        return Response({'id': pk})


class AdminLiveLinkView(APIView):
    permission_classes = [live_permission('live:link:manage')]

    def put(self, request, pk: int):
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        if match.status == MatchStatus.FINISHED:
            raise BusinessException(ResponseCode.MATCH_FINISHED_LOCKED)
        serializer = LiveLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        match.live_link = serializer.validated_data.get('live_link') or None
        match.version = match.version + 1
        match.save(update_fields=['live_link', 'version', 'updated_at'])
        return Response({'live_link': match.live_link, 'version': match.version})


class AdminFaceVerifyView(APIView):
    permission_classes = [live_permission('live:face:manage')]

    def post(self, request, pk: int):
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        serializer = FaceVerifySerializer(data=request.data)
        if not serializer.is_valid():
            _raise_file_error(serializer, 'photo')
        record = FaceVerifyService().verify(
            match,
            serializer.validated_data['player_name'],
            serializer.validated_data.get('player_no'),
            serializer.validated_data['photo'],
            request.user.id,
        )
        return Response(FaceVerifyRecordSerializer(record).data)


class AdminFaceVerifyRecordsView(APIView):
    permission_classes = [live_permission('live:face:manage')]

    def get(self, request, pk: int):
        match = _get_match(pk)
        _ensure_school_match(request.user, match)
        return Response(FaceVerifyRecordSerializer(FaceVerifyService().records(match), many=True).data)
