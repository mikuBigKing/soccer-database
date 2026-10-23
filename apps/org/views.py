"""行政区划与组织序列化器 + 视图。"""
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.pagination import paginate
from apps.common.permissions import role_permission

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
        fields = ('id', 'city', 'city_name', 'name', 'logo', 'address', 'status', 'created_at', 'updated_at')
        read_only_fields = ('created_at', 'updated_at')


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
        _get(Province, pk, '省份').delete()
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
        _get(City, pk, '城市').delete()
        return Response({'id': pk})


class SchoolListCreateView(APIView):
    permission_classes = [role_permission('admin', 'school', 'city')]

    def get(self, request):
        queryset = School.objects.all()
        city_id = request.query_params.get('city_id')
        if city_id:
            queryset = queryset.filter(city_id=city_id)
        user = request.user
        if user.role == 'school':
            profile = user.role_profile()
            queryset = queryset.filter(pk=profile.school_id) if profile and profile.school_id else queryset.none()
        elif user.role == 'city':
            profile = user.role_profile()
            queryset = queryset.filter(city_id=profile.city_id) if profile and profile.city_id else queryset.none()
        return Response(_paginated(School, request, SchoolSerializer, queryset))

    def post(self, request):
        serializer = SchoolSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(created_by=request.user.id)
        return Response(serializer.data)


class SchoolDetailView(APIView):
    permission_classes = [role_permission('admin', 'school')]

    def get(self, request, pk: int):
        self._ensure_scope(request, pk)
        return Response(SchoolSerializer(_get(School, pk, '学校')).data)

    def put(self, request, pk: int):
        self._ensure_scope(request, pk)
        obj = _get(School, pk, '学校')
        serializer = SchoolSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk: int):
        self._ensure_scope(request, pk)
        _get(School, pk, '学校').delete()
        return Response({'id': pk})

    @staticmethod
    def _ensure_scope(request, pk: int):
        user = request.user
        if user.role != 'school':
            return
        profile = user.role_profile()
        if not profile or profile.school_id != pk:
            raise BusinessException(ResponseCode.FORBIDDEN, '仅可操作本校数据')


class TeamListView(APIView):
    """球队列表：管理员全量，校级仅本校。"""

    permission_classes = [role_permission('admin', 'school')]

    def get(self, request):
        queryset = Team.objects.all()
        status_value = request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        user = request.user
        if user.role == 'school':
            profile = user.role_profile()
            queryset = queryset.filter(school_id=profile.school_id) if profile and profile.school_id else queryset.none()
        return Response(_paginated(Team, request, TeamSerializer, queryset))


def _get(model, pk: int, label: str):
    obj = model.objects.filter(pk=pk).first()
    if obj is None:
        raise BusinessException(ResponseCode.NOT_FOUND, f'{label}不存在', status_code=404)
    return obj
