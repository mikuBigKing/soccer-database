"""校级端：球队审核（本校）。"""
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import UserRole
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.pagination import paginate
from apps.common.permissions import role_permission
from apps.live.views import _school_id_of
from apps.registration.models import RegistrationLog, SchoolAuditLog
from apps.registration.services import write_log

from .models import Team, TeamStatus
from .views_coach import CoachTeamSerializer


class TeamVerifySerializer(serializers.Serializer):
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


class SchoolTeamListView(APIView):
    """本校球队列表（可按审核状态/关键字筛选）。"""

    permission_classes = [role_permission(UserRole.SCHOOL)]

    def get(self, request):
        school_id = _school_id_or_403(request)
        queryset = Team.objects.select_related('school', 'coach').filter(school_id=school_id)
        status_value = request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        keyword = request.query_params.get('keyword')
        if keyword:
            queryset = queryset.filter(name__icontains=keyword)
        data = paginate(queryset, request)
        data['list'] = CoachTeamSerializer(data['list'], many=True).data
        return Response(data)


class SchoolTeamVerifyView(APIView):
    """校级审核教练建队：通过 / 驳回（仅本校待审核球队）。"""

    permission_classes = [role_permission(UserRole.SCHOOL)]

    def post(self, request, pk: int):
        school_id = _school_id_or_403(request)
        team = Team.objects.filter(pk=pk).first()
        if team is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '球队不存在', status_code=404)
        if team.school_id != school_id:
            raise BusinessException(ResponseCode.FORBIDDEN, '无权审核他校球队')
        if team.status != TeamStatus.PENDING:
            raise BusinessException(ResponseCode.PARAM_INVALID, '仅待审核的球队可操作')

        serializer = TeamVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data['approve']:
            team.status = TeamStatus.APPROVED
            team.reject_reason = None
            action = RegistrationLog.Action.TEAM_APPROVE
            log_action = 'team.approve'
        else:
            team.status = TeamStatus.REJECTED
            team.reject_reason = serializer.validated_data['reason']
            action = RegistrationLog.Action.TEAM_REJECT
            log_action = 'team.reject'

        team.save(update_fields=['status', 'reject_reason', 'updated_at'])
        write_log(action, request.user, None, {'team_id': team.pk})
        SchoolAuditLog.objects.create(
            school_id=school_id,
            actor=request.user,
            action=log_action,
            target='team',
            detail={'team_id': team.pk, 'reason': team.reject_reason},
        )
        return Response(CoachTeamSerializer(team).data)
