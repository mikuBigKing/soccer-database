"""教练端：建队 / 改队（自有球队）。"""
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AthleteProfile, CoachProfile, ProfileStatus
from apps.accounts.validators import ensure_id_card_unique
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.formatting import mask_id_card
from apps.common.pagination import paginate
from apps.common.permissions import role_permission
from apps.registration.models import RegistrationLog
from apps.registration.serializers import PlayerMaterialSerializer
from apps.registration.services import write_log

from .models import GroupType, School, Team, TeamStatus

GROUP_TYPES = [choice[0] for choice in GroupType.choices]


class CoachTeamSerializer(serializers.ModelSerializer):
    school_name = serializers.CharField(source='school.name', read_only=True)
    group_type_label = serializers.CharField(source='get_group_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Team
        fields = (
            'id', 'school', 'school_name', 'name', 'logo', 'group_type', 'group_type_label',
            'status', 'status_label', 'reject_reason', 'created_at', 'updated_at',
        )


class CoachTeamCreateSerializer(serializers.Serializer):
    school_id = serializers.IntegerField(min_value=1)
    name = serializers.CharField(max_length=150)
    logo = serializers.CharField(max_length=255, required=False, allow_null=True, allow_blank=True)
    group_type = serializers.ChoiceField(choices=GROUP_TYPES)


class CoachTeamUpdateSerializer(serializers.Serializer):
    school_id = serializers.IntegerField(min_value=1, required=False)
    name = serializers.CharField(max_length=150, required=False)
    logo = serializers.CharField(max_length=255, required=False, allow_null=True, allow_blank=True)
    group_type = serializers.ChoiceField(choices=GROUP_TYPES, required=False)


def _coach_profile(user):
    profile = user.role_profile()
    if profile is None:
        raise BusinessException(ResponseCode.NOT_FOUND, '教练资料不存在', status_code=404)
    return profile


def _require_approved_coach(user):
    profile = _coach_profile(user)
    if profile.status != ProfileStatus.APPROVED:
        raise BusinessException(
            ResponseCode.FORBIDDEN, '教练实名认证通过后方可创建球队，请等待校级审核'
        )
    return profile


class CoachProfileSerializer(serializers.ModelSerializer):
    has_qualification = serializers.SerializerMethodField()
    id_card = serializers.SerializerMethodField()

    class Meta:
        model = CoachProfile
        fields = (
            'id', 'name', 'phone', 'id_card', 'status', 'reject_reason',
            'qualification_name', 'has_qualification',
        )

    def get_has_qualification(self, obj):
        return bool(obj.qualification_path)

    def get_id_card(self, obj):
        return mask_id_card(obj.id_card)


class CoachProfileUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50, required=False)
    id_card = serializers.CharField(max_length=18, required=False, allow_null=True, allow_blank=True)


class CoachProfileView(APIView):
    """教练本人资料：查看 / 维护姓名与身份证号（实名审核前必须填写）。"""

    permission_classes = [role_permission('coach')]

    def get(self, request):
        return Response(CoachProfileSerializer(_coach_profile(request.user)).data)

    def put(self, request):
        profile = _coach_profile(request.user)
        serializer = CoachProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if 'id_card' in data:
            new_card = ensure_id_card_unique(data['id_card'], coach_pk=profile.pk)
            if new_card != profile.id_card:
                profile.id_card = new_card
                # 身份证变更后需校级重新实名审核
                if profile.status == ProfileStatus.APPROVED:
                    profile.status = ProfileStatus.PENDING
                    profile.reject_reason = None
        if data.get('name'):
            profile.name = data['name']
            request.user.name = data['name']
            request.user.save(update_fields=['name'])
        profile.save()
        return Response(CoachProfileSerializer(profile).data)


def _own_team(user, pk: int) -> Team:
    team = Team.objects.select_related('school').filter(pk=pk).first()
    if team is None:
        raise BusinessException(ResponseCode.NOT_FOUND, '球队不存在', status_code=404)
    if team.coach_id != user.pk:
        raise BusinessException(ResponseCode.FORBIDDEN, '仅可操作自己创建的球队')
    return team


class CoachTeamListCreateView(APIView):
    """GET 我的球队 / POST 创建球队（需教练实名认证通过，创建后待校级审核）。"""

    permission_classes = [role_permission('coach')]

    def get(self, request):
        queryset = Team.objects.select_related('school').filter(coach=request.user)
        status_value = request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        keyword = request.query_params.get('keyword')
        if keyword:
            queryset = queryset.filter(name__icontains=keyword)
        data = paginate(queryset, request)
        data['list'] = CoachTeamSerializer(data['list'], many=True).data
        return Response(data)

    def post(self, request):
        _require_approved_coach(request.user)
        serializer = CoachTeamCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        school = School.objects.filter(pk=data['school_id']).first()
        if school is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '学校不存在', status_code=404)
        if Team.objects.filter(coach=request.user, school=school, name=data['name']).exists():
            raise BusinessException(ResponseCode.DUPLICATE_SUBMIT, '您已在该校创建过同名球队')

        team = Team.objects.create(
            school=school,
            coach=request.user,
            name=data['name'],
            logo=data.get('logo') or None,
            group_type=data['group_type'],
            status=TeamStatus.PENDING,
        )
        write_log(RegistrationLog.Action.TEAM_CREATE, request.user, None,
                  {'team_id': team.pk, 'school_id': school.pk, 'name': team.name})
        return Response(CoachTeamSerializer(team).data)


class CoachTeamDetailView(APIView):
    """GET / PUT 我的球队；修改关键信息后重新进入待审核。"""

    permission_classes = [role_permission('coach')]

    def get(self, request, pk: int):
        return Response(CoachTeamSerializer(_own_team(request.user, pk)).data)

    def put(self, request, pk: int):
        team = _own_team(request.user, pk)
        serializer = CoachTeamUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        changed = []
        if 'school_id' in data:
            school = School.objects.filter(pk=data['school_id']).first()
            if school is None:
                raise BusinessException(ResponseCode.NOT_FOUND, '学校不存在', status_code=404)
            if school.pk != team.school_id:
                team.school = school
                changed.append('school')
        for field in ('name', 'logo', 'group_type'):
            if field in data:
                value = data[field] or (None if field == 'logo' else data[field])
                if getattr(team, field) != value:
                    setattr(team, field, value)
                    changed.append(field)

        if changed:
            # 名称/学校/组别变化会改变审核结论，需校级重新审核；仅改 Logo 不重置
            if {'school', 'name', 'group_type'} & set(changed):
                team.status = TeamStatus.PENDING
                team.reject_reason = None
            team.save()
            write_log(RegistrationLog.Action.TEAM_UPDATE, request.user, None,
                      {'team_id': team.pk, 'changed': changed, 'status': team.status})
        return Response(CoachTeamSerializer(team).data)


class CoachTeamAthleteListView(APIView):
    """本队球员名单：GET 列表 / POST 把已通过实名审核的球员加入本队。"""

    permission_classes = [role_permission('coach')]

    def get(self, request, pk: int):
        team = _own_team(request.user, pk)
        queryset = AthleteProfile.objects.select_related('user', 'team').filter(team=team).order_by('jersey_no', 'id')
        return Response(PlayerMaterialSerializer(queryset, many=True).data)

    def post(self, request, pk: int):
        team = _own_team(request.user, pk)
        athlete_id = request.data.get('athlete_id')
        try:
            athlete_id = int(athlete_id)
        except (TypeError, ValueError):
            raise BusinessException(ResponseCode.PARAM_INVALID, 'athlete_id 必须为整数')
        profile = AthleteProfile.objects.select_related('user', 'team').filter(pk=athlete_id).first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '球员不存在', status_code=404)
        if profile.status != ProfileStatus.APPROVED:
            raise BusinessException(ResponseCode.PARAM_INVALID, '球员实名认证未通过，暂不可加入球队')
        if profile.team_id and profile.team_id != team.pk:
            raise BusinessException(ResponseCode.PARAM_INVALID, '该球员已属于其他球队')
        if profile.jersey_no is not None:
            duplicated = AthleteProfile.objects.filter(
                team=team, jersey_no=profile.jersey_no
            ).exclude(pk=profile.pk).exists()
            if duplicated:
                raise BusinessException(
                    ResponseCode.PARAM_INVALID, '该球员球衣号在本队已被占用，请先修改球衣号'
                )

        if profile.team_id != team.pk:
            profile.team = team
            profile.save(update_fields=['team', 'updated_at'])
            write_log(RegistrationLog.Action.TEAM_UPDATE, request.user, None,
                      {'team_id': team.pk, 'roster': 'add', 'athlete_id': profile.pk})
        return Response(PlayerMaterialSerializer(profile).data)


class CoachTeamAthleteDetailView(APIView):
    """把球员移出本队（历史报名使用提交时快照，不受影响）。"""

    permission_classes = [role_permission('coach')]

    def delete(self, request, pk: int, athlete_id: int):
        team = _own_team(request.user, pk)
        profile = AthleteProfile.objects.filter(pk=athlete_id, team=team).first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '该球员不在此球队中', status_code=404)
        profile.team = None
        profile.save(update_fields=['team', 'updated_at'])
        write_log(RegistrationLog.Action.TEAM_UPDATE, request.user, None,
                  {'team_id': team.pk, 'roster': 'remove', 'athlete_id': profile.pk})
        return Response({'id': athlete_id})
