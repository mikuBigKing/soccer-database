"""校级端视图：实名审核、统计报表、通知、操作日志。"""
from django.db.models import Count
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AthleteProfile, CoachProfile, ProfileStatus, UserRole
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.pagination import paginated_response
from apps.common.permissions import role_permission
from apps.live.views import _school_id_of
from apps.org.models import School, Team
from apps.registration.models import Registration, RegistrationLog, SchoolAuditLog, SchoolNotification
from apps.registration.serializers import CoachMaterialSerializer, PlayerMaterialSerializer
from apps.registration.services import write_log

SchoolOnly = role_permission(UserRole.SCHOOL)


class IdentityVerifySerializer(serializers.Serializer):
    """实名认证审核：approve 通过（需已填身份证号），reject 驳回（必填原因）。"""

    approve = serializers.BooleanField()
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs['approve'] and not attrs.get('reason'):
            raise serializers.ValidationError({'reason': '驳回时必须填写原因'})
        return attrs


def _school_id_or_403(request) -> int:
    school_id = _school_id_of(request.user)
    if school_id is None:
        raise BusinessException(ResponseCode.FORBIDDEN, '当前账号未绑定学校')
    return school_id


def _write_audit(school_id, user, action, target, detail=None):
    """写入校级操作日志。"""
    SchoolAuditLog.objects.create(
        school_id=school_id, actor=user, action=action, target=target, detail=detail or {},
    )


# ---------------- 校级实名审核 ----------------

class SchoolCoachVerifyView(APIView):
    """校级教练实名认证审核：仅可审核本校教练。"""

    permission_classes = [SchoolOnly]

    def post(self, request, pk: int):
        school_id = _school_id_or_403(request)
        profile = CoachProfile.objects.filter(pk=pk).first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '教练不存在', status_code=404)
        # 教练通过其执教的球队归属本校
        if not Team.objects.filter(coach=profile.user, school_id=school_id).exists():
            raise BusinessException(ResponseCode.FORBIDDEN, '无权审核他校教练')
        if profile.status != ProfileStatus.PENDING:
            raise BusinessException(ResponseCode.PARAM_INVALID, '仅待审核的实名认证可操作')
        serializer = IdentityVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        approve = serializer.validated_data['approve']

        if approve:
            if not profile.id_card:
                raise BusinessException(ResponseCode.PARAM_INVALID, '教练未填写身份证号，无法通过实名认证')
            profile.status = ProfileStatus.APPROVED
            profile.reject_reason = None
            action = RegistrationLog.Action.IDENTITY_APPROVE
            detail = {'target': 'coach', 'coach_id': profile.pk}
        else:
            profile.status = ProfileStatus.REJECTED
            profile.reject_reason = serializer.validated_data['reason']
            action = RegistrationLog.Action.IDENTITY_REJECT
            detail = {'target': 'coach', 'coach_id': profile.pk, 'reason': serializer.validated_data['reason']}

        profile.save(update_fields=['status', 'reject_reason', 'updated_at'])
        write_log(action, request.user, None, detail)
        _write_audit(school_id, request.user, action, 'coach', {'coach_id': profile.pk})
        return Response(CoachMaterialSerializer(profile).data)


class SchoolAthleteVerifyView(APIView):
    """校级球员实名认证审核：仅可审核本校球员。"""

    permission_classes = [SchoolOnly]

    def post(self, request, pk: int):
        school_id = _school_id_or_403(request)
        profile = AthleteProfile.objects.filter(pk=pk).first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '球员不存在', status_code=404)
        # 球员通过其球队归属本校
        if not profile.team or profile.team.school_id != school_id:
            raise BusinessException(ResponseCode.FORBIDDEN, '无权审核他校球员')
        if profile.status != ProfileStatus.PENDING:
            raise BusinessException(ResponseCode.PARAM_INVALID, '仅待审核的实名认证可操作')
        serializer = IdentityVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        approve = serializer.validated_data['approve']

        if approve:
            if not profile.id_card:
                raise BusinessException(ResponseCode.PARAM_INVALID, '球员未填写身份证号，无法通过实名认证')
            profile.status = ProfileStatus.APPROVED
            profile.reject_reason = None
            action = RegistrationLog.Action.IDENTITY_APPROVE
            detail = {'target': 'athlete', 'athlete_id': profile.pk}
        else:
            profile.status = ProfileStatus.REJECTED
            profile.reject_reason = serializer.validated_data['reason']
            action = RegistrationLog.Action.IDENTITY_REJECT
            detail = {'target': 'athlete', 'athlete_id': profile.pk, 'reason': serializer.validated_data['reason']}

        profile.save(update_fields=['status', 'reject_reason', 'updated_at'])
        write_log(action, request.user, None, detail)
        _write_audit(school_id, request.user, action, 'athlete', {'athlete_id': profile.pk})
        return Response(PlayerMaterialSerializer(profile).data)


# ---------------- 本校统计报表 ----------------

class SchoolStatisticsView(APIView):
    """本校统计：球队、报名单、比赛、球员实名认证状态分布。"""

    permission_classes = [SchoolOnly]

    def get(self, request):
        school_id = _school_id_or_403(request)
        school = School.objects.filter(pk=school_id).first()
        if school is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '学校不存在', status_code=404)

        # 球队统计
        team_qs = Team.objects.filter(school_id=school_id)
        team_stats = {
            'total': team_qs.count(),
            'by_status': dict(team_qs.values_list('status').annotate(count=Count('id'))),
        }

        # 报名单统计
        reg_qs = Registration.objects.filter(team__school_id=school_id)
        reg_stats = {
            'total': reg_qs.count(),
            'by_status': dict(reg_qs.values_list('status').annotate(count=Count('id'))),
        }

        # 球员实名认证统计
        athlete_qs = AthleteProfile.objects.filter(team__school_id=school_id)
        athlete_stats = {
            'total': athlete_qs.count(),
            'by_status': dict(athlete_qs.values_list('status').annotate(count=Count('id'))),
        }

        # 教练实名认证统计
        coach_user_ids = team_qs.values_list('coach_id', flat=True)
        coach_qs = CoachProfile.objects.filter(user_id__in=coach_user_ids)
        coach_stats = {
            'total': coach_qs.count(),
            'by_status': dict(coach_qs.values_list('status').annotate(count=Count('id'))),
        }

        return Response({
            'school_id': school_id,
            'school_name': school.name,
            'teams': team_stats,
            'registrations': reg_stats,
            'athletes': athlete_stats,
            'coaches': coach_stats,
        })


# ---------------- 校级通知 ----------------

class SchoolNotificationSerializer(serializers.ModelSerializer):
    title = serializers.CharField(max_length=100)
    content = serializers.CharField(max_length=5000)

    class Meta:
        model = SchoolNotification
        fields = ('id', 'title', 'content', 'created_by', 'created_at')
        read_only_fields = ('id', 'created_by', 'created_at')


class SchoolNotificationListView(APIView):
    """校级通知列表与发布。"""

    permission_classes = [SchoolOnly]

    def get(self, request):
        school_id = _school_id_or_403(request)
        queryset = SchoolNotification.objects.filter(school_id=school_id)
        return Response(paginated_response(queryset, request, SchoolNotificationSerializer))

    def post(self, request):
        school_id = _school_id_or_403(request)
        serializer = SchoolNotificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notification = SchoolNotification.objects.create(
            school_id=school_id,
            title=serializer.validated_data['title'],
            content=serializer.validated_data['content'],
            created_by=request.user,
        )
        SchoolAuditLog.objects.create(
            school_id=school_id, actor=request.user, action='notification.create',
            target='notification', detail={'notification_id': notification.pk, 'title': notification.title},
        )
        return Response(SchoolNotificationSerializer(notification).data)


# ---------------- 本校操作日志 ----------------

class SchoolAuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source='actor.name', read_only=True, default='')

    class Meta:
        model = SchoolAuditLog
        fields = ('id', 'action', 'target', 'detail', 'actor_name', 'created_at')


class SchoolAuditLogListView(APIView):
    """本校操作日志列表。"""

    permission_classes = [SchoolOnly]

    def get(self, request):
        school_id = _school_id_or_403(request)
        queryset = SchoolAuditLog.objects.filter(school_id=school_id).select_related('actor')
        action = request.query_params.get('action')
        if action:
            allowed = {
                'identity.approve', 'identity.reject', 'notification.create',
                'match.create', 'match.update', 'match.delete',
            }
            if action not in allowed:
                raise BusinessException(ResponseCode.PARAM_INVALID, f'不支持的 action: {action}')
            queryset = queryset.filter(action=action)
        return Response(paginated_response(queryset, request, SchoolAuditLogSerializer))
