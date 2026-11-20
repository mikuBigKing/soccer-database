"""行政区划与组织序列化器 + 视图。"""
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException, delete_or_block
from apps.common.pagination import paginate
from apps.common.permissions import is_admin, role_permission

from .models import City, Province, School, Team


class ProvinceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Province
        fields = ('id', 'name', 'code')


class CitySerializer(serializers.ModelSerializer):
    province_name = serializers.CharField(source='province.name', read_only=True)

    class Meta:
        model = City
        fields = ('id', 'province', 'province_name', 'name', 'code')


class SchoolSerializer(serializers.ModelSerializer):
    city_name = serializers.CharField(source='city.name', read_only=True)

    class Meta:
        model = School
        fields = (
            'id', 'city', 'city_name', 'county', 'name', 'logo', 'address',
            'status', 'created_at', 'updated_at',
        )
        read_only_fields = ('created_at', 'updated_at')


class SchoolCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = School
        fields = ('id', 'city', 'county', 'name', 'logo', 'address', 'status')
        read_only_fields = ('id',)


class TeamSerializer(serializers.ModelSerializer):
    school_name = serializers.CharField(source='school.name', read_only=True)
    coach_name = serializers.CharField(source='coach.name', read_only=True)

    class Meta:
        model = Team
        fields = (
            'id', 'school', 'school_name', 'coach', 'coach_name', 'name', 'logo',
            'group_type', 'status', 'reject_reason', 'created_at', 'updated_at',
        )
        read_only_fields = ('coach', 'status', 'reject_reason', 'created_at', 'updated_at')


def _paginated(model, request, serializer_class, queryset=None, keyword_fields=('name',)):
    queryset = queryset if queryset is not None else model.objects.all()
    keyword = request.query_params.get('keyword')
    if keyword:
        from django.db.models import Q

        condition = Q()
        for field in keyword_fields:
            condition |= Q(**{f'{field}__icontains': keyword})
        queryset = queryset.filter(condition)

    data = paginate(queryset, request)
    data['list'] = serializer_class(data['list'], many=True).data
    return data


class ProvinceListCreateView(APIView):
    permission_classes = [role_permission('admin')]

    def get(self, request):
        return Response(_paginated(Province, request, ProvinceSerializer))

    def post(self, request):
        serializer = ProvinceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class ProvinceDetailView(APIView):
    permission_classes = [role_permission('admin')]

    def get(self, request, pk: int):
        return Response(ProvinceSerializer(_get(Province, pk, '省份')).data)

    def put(self, request, pk: int):
        obj = _get(Province, pk, '省份')
        serializer = ProvinceSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk: int):
        delete_or_block(_get(Province, pk, '省份'), '省份')
        return Response({'id': pk})


class CityListCreateView(APIView):
    permission_classes = [role_permission('admin', 'city')]

    def get(self, request):
        queryset = City.objects.all()
        province_id = request.query_params.get('province_id')
        if province_id:
            queryset = queryset.filter(province_id=province_id)
        user = request.user
        if user.role == 'city':
            profile = user.role_profile()
            if not profile or not profile.city_id:
                return Response({'list': [], 'total': 0, 'page': 1, 'page_size': 20})
            queryset = queryset.filter(pk=profile.city_id)
        return Response(_paginated(City, request, CitySerializer, queryset))

    def post(self, request):
        # 市级不可新增市州，仅管理员可维护行政区划
        if not is_admin(request.user):
            raise BusinessException(ResponseCode.FORBIDDEN, '仅管理员可新增市州')
        serializer = CitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class CityDetailView(APIView):
    permission_classes = [role_permission('admin')]

    def get(self, request, pk: int):
        return Response(CitySerializer(_get(City, pk, '城市')).data)

    def put(self, request, pk: int):
        obj = _get(City, pk, '城市')
        serializer = CitySerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk: int):
        delete_or_block(_get(City, pk, '城市'), '城市')
        return Response({'id': pk})


class SchoolListCreateView(APIView):
    """学校列表/新增：管理员全量；市州仅本市；校级仅本校。"""

    permission_classes = [role_permission('admin', 'city', 'school')]

    def get(self, request):
        queryset = School.objects.select_related('city').all()
        city_id = request.query_params.get('city_id')
        if city_id:
            queryset = queryset.filter(city_id=city_id)
        keyword = request.query_params.get('keyword')
        if keyword:
            queryset = queryset.filter(name__icontains=keyword)
        user = request.user
        if user.role == 'school':
            profile = user.role_profile()
            queryset = queryset.filter(pk=profile.school_id) if profile and profile.school_id else queryset.none()
        elif user.role == 'city':
            profile = user.role_profile()
            queryset = queryset.filter(city_id=profile.city_id) if profile and profile.city_id else queryset.none()
        return Response(_paginated(School, request, SchoolSerializer, queryset))

    def post(self, request):
        user = request.user
        # 校级不可新增学校；市州仅可在本市新增
        if not (is_admin(user) or user.role == 'city'):
            raise BusinessException(ResponseCode.FORBIDDEN, '仅管理员或市州可新增学校')
        serializer = SchoolCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if user.role == 'city':
            profile = user.role_profile()
            if not profile or not profile.city_id:
                raise BusinessException(ResponseCode.FORBIDDEN, '当前账号未绑定市州')
            if serializer.validated_data['city'].pk != profile.city_id:
                raise BusinessException(ResponseCode.FORBIDDEN, '仅可在本市州新增学校')
        school = serializer.save(created_by=user.id)
        return Response(SchoolSerializer(school).data)


class SchoolDetailView(APIView):
    """学校详情：管理员全量；市州仅本市；校级仅本校且只能改基础资料。"""

    permission_classes = [role_permission('admin', 'city', 'school')]

    def get(self, request, pk: int):
        return Response(SchoolSerializer(self._scoped_school(request, pk)).data)

    def put(self, request, pk: int):
        school = self._scoped_school(request, pk)
        user = request.user
        data = dict(request.data)
        if user.role == 'school':
            allowed = {'name', 'logo', 'address'}
            if set(data) - allowed:
                raise BusinessException(ResponseCode.FORBIDDEN, '校级仅可修改本校名称、Logo 与地址')
        elif user.role == 'city':
            allowed = {'name', 'logo', 'address', 'county', 'status'}
            if set(data) - allowed:
                raise BusinessException(ResponseCode.FORBIDDEN, '市州仅可维护本市学校基础资料与状态')
            if 'city' in data and str(data['city']) != str(school.city_id):
                raise BusinessException(ResponseCode.FORBIDDEN, '不可将学校迁移到其他市州')
        serializer = SchoolSerializer(school, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk: int):
        # 学校删除仅管理员可操作；被球队/报名引用时由 PROTECT 拦截并返回业务提示
        if not is_admin(request.user):
            raise BusinessException(ResponseCode.FORBIDDEN, '仅管理员可删除学校')
        delete_or_block(_get(School, pk, '学校'), '学校')
        return Response({'id': pk})

    @staticmethod
    def _scoped_school(request, pk: int) -> School:
        school = _get(School, pk, '学校')
        user = request.user
        if is_admin(user):
            return school
        profile = user.role_profile()
        if user.role == 'school':
            if not profile or profile.school_id != school.id:
                raise BusinessException(ResponseCode.FORBIDDEN, '仅可操作本校数据')
        elif user.role == 'city':
            if not profile or not profile.city_id or profile.city_id != school.city_id:
                raise BusinessException(ResponseCode.FORBIDDEN, '仅可操作本市州学校')
        else:
            raise BusinessException(ResponseCode.FORBIDDEN, '无操作权限')
        return school


class TeamListView(APIView):
    """球队列表：管理员全量，校级仅本校；支持市州/区县/组别/赛事/状态筛选。"""

    permission_classes = [role_permission('admin', 'city', 'school')]

    def get(self, request):
        queryset = (Team.objects.select_related('school__city', 'coach')
                    .order_by('-id'))
        params = request.query_params
        if params.get('city_id'):
            queryset = queryset.filter(school__city_id=params['city_id'])
        if params.get('county'):
            queryset = queryset.filter(school__county=params['county'])
        if params.get('school_id'):
            queryset = queryset.filter(school_id=params['school_id'])
        if params.get('group_type'):
            queryset = queryset.filter(group_type=params['group_type'])
        if params.get('status'):
            queryset = queryset.filter(status=params['status'])
        if params.get('competition_id'):
            queryset = queryset.filter(registrations__competition_id=params['competition_id']).distinct()
        keyword = (params.get('keyword') or '').strip()
        if keyword:
            from django.db.models import Q

            queryset = queryset.filter(Q(name__icontains=keyword) | Q(school__name__icontains=keyword))

        user = request.user
        if user.role == 'school':
            profile = user.role_profile()
            queryset = queryset.filter(school_id=profile.school_id) if profile and profile.school_id else queryset.none()
        elif user.role == 'city':
            profile = user.role_profile()
            queryset = queryset.filter(school__city_id=profile.city_id) if profile and profile.city_id else queryset.none()
        return Response(_paginated(Team, request, TeamSerializer, queryset))


class TeamDetailView(APIView):
    """球队详情：基础信息 + 学校/教练 + 报名记录 + 最近比赛。"""

    permission_classes = [role_permission('admin', 'city', 'school')]

    def get(self, request, pk: int):
        team = _scoped_team(request, pk)
        return Response(_team_detail(team))


class TeamAthletesView(APIView):
    """球队成员（球员档案）。"""

    permission_classes = [role_permission('admin', 'city', 'school')]

    def get(self, request, pk: int):
        from apps.accounts.models import AthleteProfile

        team = _scoped_team(request, pk)
        rows = (AthleteProfile.objects.filter(team=team, deleted_at__isnull=True)
                .select_related('school').order_by('jersey_no', 'id'))
        return Response({'total': len(rows), 'list': [{
            'id': row.id,
            'name': row.name,
            'jersey_no': row.jersey_no,
            'position': row.position,
            'status': row.status,
            'status_label': row.get_status_display(),
            'eligibility_status': row.eligibility_status,
            'phone': row.user.phone,
            'is_public': row.is_public,
        } for row in rows]})


class TeamCoachesView(APIView):
    """球队教练（当前为球队绑定教练一人）。"""

    permission_classes = [role_permission('admin', 'city', 'school')]

    def get(self, request, pk: int):
        from apps.accounts.models import CoachProfile

        team = _scoped_team(request, pk)
        coach = team.coach
        profile = CoachProfile.objects.filter(user=coach).first()
        return Response({'total': 1 if coach else 0, 'list': [{
            'id': coach.id if coach else None,
            'name': coach.name if coach else None,
            'phone': coach.phone if coach else None,
            'status': coach.status if coach else None,
            'profile_status': profile.status if profile else None,
            'profile_status_label': profile.get_status_display() if profile else None,
            'is_public': profile.is_public if profile else None,
        }]})


class TeamMatchesView(APIView):
    """球队比赛记录（比分 + 结果）。"""

    permission_classes = [role_permission('admin', 'city', 'school')]

    def get(self, request, pk: int):
        from django.db.models import Q

        from apps.live.models import Match

        team = _scoped_team(request, pk)
        matches = (Match.objects.filter(Q(home_team=team) | Q(away_team=team))
                   .select_related('competition', 'home_team', 'away_team')
                   .order_by('-match_time'))
        rows = []
        for match in matches[:100]:
            is_home = match.home_team_id == team.id
            my = match.home_score if is_home else match.away_score
            opp = match.away_score if is_home else match.home_score
            result = 'win' if my > opp else ('draw' if my == opp else 'loss')
            rows.append({
                'id': match.id,
                'competition_id': match.competition_id,
                'competition_name': match.competition.name if match.competition_id else None,
                'match_time': match.match_time.strftime('%Y-%m-%d %H:%M:%S') if match.match_time else None,
                'home_team': match.home_team.name if match.home_team_id else None,
                'away_team': match.away_team.name if match.away_team_id else None,
                'home_score': match.home_score,
                'away_score': match.away_score,
                'stage': match.stage,
                'status': match.status,
                'result': result,
            })
        return Response({'total': len(rows), 'list': rows})


class TeamResetCoachPasswordView(APIView):
    """重置球队教练账号密码，返回本次生效的密码（默认密码或自定义）。"""

    permission_classes = [role_permission('admin', 'city', 'school')]

    def post(self, request, pk: int):
        from django.conf import settings

        team = _scoped_team(request, pk)
        if team.coach_id is None:
            raise BusinessException(ResponseCode.PARAM_INVALID, '球队未绑定教练')
        password = (request.data.get('password') or '').strip() or settings.DEFAULT_RESET_PASSWORD
        team.coach.set_password(password)
        team.coach.save(update_fields=['password'])
        from apps.common.audit import write_audit

        write_audit(request, module='team', action='重置球队教练密码',
                    resource_type='team', resource_id=team.id)
        return Response({'id': team.id, 'coach_id': team.coach_id, 'password': password})


def _scoped_team(request, pk: int) -> Team:
    team = Team.objects.select_related('school__city', 'coach').filter(pk=pk).first()
    if team is None:
        raise BusinessException(ResponseCode.NOT_FOUND, '球队不存在', status_code=404)
    user = request.user
    if is_admin(user):
        return team
    profile = user.role_profile()
    if user.role == 'school' and (not profile or profile.school_id != team.school_id):
        raise BusinessException(ResponseCode.FORBIDDEN, '仅可操作本校球队')
    if user.role == 'city' and (not profile or not profile.city_id
                                or profile.city_id != team.school.city_id):
        raise BusinessException(ResponseCode.FORBIDDEN, '仅可操作本市球队')
    return team


def _team_detail(team: Team) -> dict:
    from apps.accounts.models import AthleteProfile

    registrations = (team.registrations.select_related('competition')
                     .order_by('-created_at'))
    return {
        'id': team.id,
        'name': team.name,
        'logo': team.logo,
        'group_type': team.group_type,
        'group_type_label': team.get_group_type_display(),
        'status': team.status,
        'status_label': team.get_status_display(),
        'reject_reason': team.reject_reason,
        'created_at': team.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        'school': {
            'id': team.school_id,
            'name': team.school.name,
            'county': team.school.county,
            'city_id': team.school.city_id,
            'city_name': team.school.city.name if team.school.city_id else None,
        },
        'coach': {
            'id': team.coach_id,
            'name': team.coach.name,
            'phone': team.coach.phone,
            'status': team.coach.status,
        } if team.coach_id else None,
        'athlete_count': AthleteProfile.objects.filter(team=team, deleted_at__isnull=True).count(),
        'registrations': [{
            'id': reg.id,
            'competition_id': reg.competition_id,
            'competition_name': reg.competition.name if reg.competition_id else None,
            'status': reg.status,
            'created_at': reg.created_at.strftime('%Y-%m-%d %H:%M:%S') if reg.created_at else None,
        } for reg in registrations],
    }


def _get(model, pk: int, label: str):
    obj = model.objects.filter(pk=pk).first()
    if obj is None:
        raise BusinessException(ResponseCode.NOT_FOUND, f'{label}不存在', status_code=404)
    return obj
