"""管理员账号总表（/api/admin/users）：全角色账号的查询、创建、修改、禁用与重置密码。"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import (
    AdminProfile,
    AthleteProfile,
    CityProfile,
    CoachProfile,
    CountyProfile,
    ProfileStatus,
    ProvinceProfile,
    SchoolProfile,
    UserRole,
    UserStatus,
)
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.formatting import format_dt
from apps.common.pagination import paginate
from apps.common.permissions import role_permission
from apps.org.models import City, Province, School

from . import services

User = get_user_model()

ADMIN_ONLY = [IsAuthenticated, role_permission('admin')]

SCOPE_ROLES = {UserRole.PROVINCE, UserRole.CITY, UserRole.COUNTY, UserRole.SCHOOL}
PHONE_ROLES = {UserRole.PROVINCE, UserRole.CITY, UserRole.COUNTY, UserRole.SCHOOL,
               UserRole.COACH, UserRole.ATHLETE, UserRole.PERSONAL}


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _ensure_phone_available(phone: str, exclude_pk: int = None) -> None:
    qs = User.objects.filter(Q(phone=phone) | Q(username=phone))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        raise BusinessException(ResponseCode.PARAM_INVALID, 'phone: 该手机号已被使用')


def _ensure_email_available(email: str, exclude_pk: int = None) -> None:
    qs = User.objects.filter(Q(email=email) | Q(username=email))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        raise BusinessException(ResponseCode.PARAM_INVALID, 'email: 该邮箱已被使用')


def _get_scope_model(model, pk: int, label: str):
    obj = model.objects.filter(pk=pk).first()
    if obj is None:
        raise BusinessException(ResponseCode.PARAM_INVALID, f'{label}不存在')
    return obj


class UserQuerySerializer(serializers.Serializer):
    role = serializers.CharField(required=False, allow_blank=True, help_text='可逗号分隔多个角色')
    status = serializers.ChoiceField(choices=UserStatus.choices, required=False)
    keyword = serializers.CharField(required=False, allow_blank=True)
    province_id = serializers.IntegerField(required=False)
    city_id = serializers.IntegerField(required=False)
    school_id = serializers.IntegerField(required=False)


class UserCreateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=UserRole.choices)
    name = serializers.CharField(max_length=50)
    email = serializers.EmailField(max_length=191)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)
    province_id = serializers.IntegerField(required=False, allow_null=True)
    city_id = serializers.IntegerField(required=False, allow_null=True)
    school_id = serializers.IntegerField(required=False, allow_null=True)
    county = serializers.CharField(max_length=50, required=False, allow_blank=True)

    def validate_email(self, value):
        return value.strip().lower()

    def validate(self, attrs):
        role = attrs['role']
        if role in (UserRole.CITY, UserRole.COUNTY) and not attrs.get('city_id'):
            raise serializers.ValidationError({'city_id': '必须选择所属城市'})
        if role == UserRole.PROVINCE and not attrs.get('province_id'):
            raise serializers.ValidationError({'province_id': '必须选择所属省份'})
        if role == UserRole.SCHOOL and not attrs.get('school_id'):
            raise serializers.ValidationError({'school_id': '必须选择所属学校'})
        return attrs


class UserUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50, required=False)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=UserStatus.choices, required=False)
    province_id = serializers.IntegerField(required=False, allow_null=True)
    city_id = serializers.IntegerField(required=False, allow_null=True)
    school_id = serializers.IntegerField(required=False, allow_null=True)
    county = serializers.CharField(max_length=50, required=False, allow_blank=True)


class UserBatchResetSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    password = serializers.CharField(required=False, allow_blank=True)


class ResetPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# 业务
# ---------------------------------------------------------------------------

_ROLE_LABELS = dict(UserRole.choices)


def _scope_of(user) -> dict:
    if user.role == UserRole.PROVINCE:
        return {'province_id': user.scope_province_id,
                'province_name': user.scope_province.name if user.scope_province_id else None}
    if user.role == UserRole.CITY:
        return {'city_id': user.scope_city_id,
                'city_name': user.scope_city.name if user.scope_city_id else None}
    if user.role == UserRole.COUNTY:
        profile = getattr(user, 'county_profile', None)
        return {'city_id': profile.city_id if profile else None,
                'city_name': profile.city.name if profile and profile.city_id else None,
                'county': profile.county if profile else None}
    if user.role == UserRole.SCHOOL:
        return {'school_id': user.scope_school_id,
                'school_name': user.scope_school.name if user.scope_school_id else None}
    if user.role in (UserRole.COACH, UserRole.ATHLETE):
        profile = user.role_profile()
        return {'profile_id': profile.id if profile else None}
    return {}


def _serialize(user) -> dict:
    return {
        'id': user.id,
        'name': user.name,
        'role': user.role,
        'role_label': _ROLE_LABELS.get(user.role),
        'phone': user.phone,
        'email': user.email,
        'status': user.status,
        'status_label': user.get_status_display(),
        'is_superuser': user.is_superuser,
        'last_login': format_dt(user.last_login) if user.last_login else None,
        'created_at': format_dt(user.date_joined),
        **_scope_of(user),
    }


def _base_qs():
    return (User.objects
            .select_related('scope_province', 'scope_city', 'scope_school')
            .order_by('-id'))


def list_users(request, params: dict) -> dict:
    qs = _base_qs()
    roles = [r for r in (params.get('role') or '').split(',') if r]
    if roles:
        invalid = [r for r in roles if r not in _ROLE_LABELS]
        if invalid:
            raise BusinessException(ResponseCode.PARAM_INVALID, f'role: 不支持的 role {invalid[0]}')
        qs = qs.filter(role__in=roles)
    if params.get('status'):
        qs = qs.filter(status=params['status'])
    if params.get('province_id'):
        qs = qs.filter(scope_province_id=params['province_id'])
    if params.get('city_id'):
        qs = qs.filter(Q(scope_city_id=params['city_id']) | Q(county_profile__city_id=params['city_id']))
    if params.get('school_id'):
        qs = qs.filter(scope_school_id=params['school_id'])
    keyword = (params.get('keyword') or '').strip()
    if keyword:
        qs = qs.filter(
            Q(name__icontains=keyword) | Q(phone__icontains=keyword)
            | Q(email__icontains=keyword) | Q(username__icontains=keyword)
        )

    data = paginate(qs, request)
    data['list'] = [_serialize(u) for u in data['list']]
    return data


@transaction.atomic
def create_user(request, data: dict) -> dict:
    role = data['role']
    password = data.get('password') or settings.DEFAULT_RESET_PASSWORD
    name = data['name']
    email = data['email'].strip().lower()
    phone = (data.get('phone') or '').strip() or None

    _ensure_email_available(email)
    if phone:
        _ensure_phone_available(phone)

    user = User.objects.create_user(username=email, email=email, phone=phone,
                                    name=name, role=role, password=password)
    if role == UserRole.ADMIN:
        AdminProfile.objects.create(user=user, name=name)
    else:
        _create_profile(user, name, role, data)

    if data.get('province_id') or data.get('city_id') or data.get('school_id'):
        _apply_scope(user, data)

    services._log(request, '创建账号', 'user', user.id, None, {'role': role, 'name': name})
    return _serialize(user)


def _create_profile(user, name: str, role: str, data: dict) -> None:
    if role == UserRole.PROVINCE:
        ProvinceProfile.objects.create(
            user=user, name=name,
            province=_get_scope_model(Province, data['province_id'], '省份'),
        )
    elif role == UserRole.CITY:
        CityProfile.objects.create(
            user=user, name=name,
            city=_get_scope_model(City, data['city_id'], '城市'),
        )
    elif role == UserRole.COUNTY:
        CountyProfile.objects.create(
            user=user, name=name,
            city=_get_scope_model(City, data['city_id'], '城市'),
            county=(data.get('county') or '').strip() or None,
        )
    elif role == UserRole.SCHOOL:
        SchoolProfile.objects.create(
            user=user, name=name,
            school=_get_scope_model(School, data['school_id'], '学校'),
        )
    elif role == UserRole.COACH:
        CoachProfile.objects.create(
            user=user, name=name, phone=user.phone, status=ProfileStatus.PENDING,
        )
    elif role == UserRole.ATHLETE:
        AthleteProfile.objects.create(
            user=user, name=name, status=ProfileStatus.PENDING,
        )


def _apply_scope(user, data: dict) -> None:
    if user.role == UserRole.PROVINCE:
        province = _get_scope_model(Province, data['province_id'], '省份')
        user.scope_province = province
        user.save(update_fields=['scope_province'])
        profile = getattr(user, 'province_profile', None)
        if profile:
            profile.province = province
            profile.save(update_fields=['province'])
    elif user.role == UserRole.CITY:
        city = _get_scope_model(City, data['city_id'], '城市')
        user.scope_city = city
        user.save(update_fields=['scope_city'])
        profile = getattr(user, 'city_profile', None)
        if profile:
            profile.city = city
            profile.save(update_fields=['city'])
    elif user.role == UserRole.SCHOOL:
        school = _get_scope_model(School, data['school_id'], '学校')
        user.scope_school = school
        user.save(update_fields=['scope_school'])
        profile = getattr(user, 'school_profile', None)
        if profile:
            profile.school = school
            profile.save(update_fields=['school'])
    elif user.role == UserRole.COUNTY and data.get('city_id'):
        profile = getattr(user, 'county_profile', None)
        if profile:
            profile.city = _get_scope_model(City, data['city_id'], '城市')
            profile.county = (data.get('county') or profile.county or '').strip() or None
            profile.save(update_fields=['city', 'county'])


def _get_user(pk: int):
    user = _base_qs().filter(pk=pk).first()
    if user is None:
        raise BusinessException(ResponseCode.NOT_FOUND, '账号不存在', status_code=404)
    return user


@transaction.atomic
def update_user(request, pk: int, data: dict) -> dict:
    user = _get_user(pk)
    before = _serialize(user)
    updates = []

    if 'name' in data:
        user.name = data['name']
        updates.append('name')
        profile = user.role_profile()
        if profile is not None:
            profile.name = data['name']
            profile.save(update_fields=['name'])

    if data.get('phone') and data['phone'] != user.phone:
        phone = data['phone'].strip()
        _ensure_phone_available(phone, exclude_pk=user.pk)
        user.phone = phone
        updates.append('phone')
        if user.role == UserRole.COACH:
            profile = user.role_profile()
            if profile is not None:
                profile.phone = phone
                profile.save(update_fields=['phone'])

    if 'email' in data and (data['email'] or '') != (user.email or ''):
        email = (data['email'] or '').strip().lower() or None
        if email:
            _ensure_email_available(email, exclude_pk=user.pk)
            user.username = email
            updates.append('username')
        user.email = email
        updates.append('email')

    if data.get('status') and data['status'] != user.status:
        user.status = data['status']
        user.is_active = data['status'] == UserStatus.ACTIVE
        updates.extend(['status', 'is_active'])

    if updates:
        user.save(update_fields=list(dict.fromkeys(updates)))
    if any(k in data for k in ('province_id', 'city_id', 'school_id', 'county')):
        _apply_scope(user, data)

    after = _serialize(_get_user(pk))
    services._log(request, '修改账号', 'user', pk, before, after)
    return after


@transaction.atomic
def disable_user(request, pk: int) -> dict:
    user = _get_user(pk)
    if user.pk == request.user.pk:
        raise BusinessException(ResponseCode.PARAM_INVALID, '不能禁用当前登录账号')
    if user.is_superuser:
        raise BusinessException(ResponseCode.PARAM_INVALID, '不能禁用超级管理员')
    user.status = UserStatus.DISABLED
    user.is_active = False
    user.save(update_fields=['status', 'is_active'])
    services._log(request, '禁用账号', 'user', pk, None, {'status': user.status})
    return {'id': pk, 'status': user.status}


def _password_of(data) -> tuple[str, bool]:
    custom = bool((data or {}).get('password'))
    return (data.get('password') if custom else settings.DEFAULT_RESET_PASSWORD), custom


@transaction.atomic
def reset_password(request, pk: int, data=None) -> dict:
    user = _get_user(pk)
    password, _ = _password_of(data)
    user.set_password(password)
    user.save(update_fields=['password'])
    services._log(request, '重置账号密码', 'user', pk, None, None)
    return {'id': pk, 'reset': True, 'password': password}


@transaction.atomic
def batch_reset_password(request, data: dict) -> dict:
    password, custom = _password_of(data)
    success = 0
    skipped = []
    for uid in data['ids']:
        user = User.objects.filter(pk=uid).first()
        if user is None or user.is_superuser:
            skipped.append({'id': uid, 'reason': '账号不存在或不可重置'})
            continue
        user.set_password(password)
        user.save(update_fields=['password'])
        success += 1
    result = {'total': len(data['ids']), 'success': success, 'skipped': skipped}
    if not custom:
        result['password'] = password
    services._log(request, '批量重置账号密码', 'user', 0, None, {'success': success})
    return result


# ---------------------------------------------------------------------------
# 视图
# ---------------------------------------------------------------------------

class UserListView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request):
        serializer = UserQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(list_users(request, serializer.validated_data))

    def post(self, request):
        serializer = UserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(create_user(request, serializer.validated_data))


class UserDetailView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request, pk: int):
        return Response(_serialize(_get_user(pk)))

    def put(self, request, pk: int):
        serializer = UserUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(update_user(request, pk, serializer.validated_data))

    def delete(self, request, pk: int):
        return Response(disable_user(request, pk))


class UserResetPasswordView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request, pk: int):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(reset_password(request, pk, serializer.validated_data))


class UserBatchResetPasswordView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request):
        serializer = UserBatchResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(batch_reset_password(request, serializer.validated_data))
