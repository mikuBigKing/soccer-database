"""省级审核端视图（role:province，管理员直通）。

报名审核：
- GET  /api/province/registrations                          全省报名查询/搜索
- GET  /api/province/registrations/<id>                     详情（含资料清单）
- POST /api/province/registrations/<id>/approve             通过
- POST /api/province/registrations/<id>/reject              驳回（必填原因）
- GET  /api/province/registrations/<id>/stamped-form        查看/下载盖章扫描件（?inline=1 在线查看）
- GET  /api/province/registrations/<id>/form-docx           下载报名表 Word
- GET  /api/province/registrations/export                   报名情况导出 Excel
- GET  /api/province/registrations/statistics               市州/高校报名统计

身份信息与材料：
- GET/PUT  /api/province/coaches(/<id>)                     教练员搜索/修改
- GET/PUT  /api/province/athletes(/<id>)                    球员搜索/修改
- POST/GET /api/province/athletes/<id>/avatar               上传/查看球员头像
- GET  /api/province/athletes/<id>/enrollment-cert/download 下载学籍验证材料
- GET  /api/province/coaches/<id>/qualification/download    下载教练资质附件

账号与日志：
- GET/POST         /api/province/accounts                   报名账号查询/创建
- PUT/DELETE       /api/province/accounts/<id>              报名账号修改/删除
- GET              /api/province/logs                       报名日志查询
"""
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.http import HttpResponse
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AthleteProfile, CityProfile, CoachProfile, ProvinceProfile, UserRole
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.pagination import paginated_response
from apps.common.permissions import is_admin, role_permission
from apps.registration.models import Registration, RegistrationLog
from apps.registration.serializers import (
    CoachMaterialSerializer,
    PlayerMaterialSerializer,
    RegistrationDetailSerializer,
    RegistrationListSerializer,
    RegistrationLogSerializer,
    RejectSerializer,
)
from apps.registration.services import (
    ExportService,
    MaterialService,
    RegistrationService,
    _get_registration,
    filter_registrations,
    material_download,
    write_log,
)

User = get_user_model()

ProvinceOnly = role_permission('province')

# 报名账号管理的角色范围（与用户确认：市州端/省级端/管理员端账号）
MANAGED_ACCOUNT_ROLES = (UserRole.CITY, UserRole.PROVINCE, UserRole.ADMIN)


# ---------------- 报名审核 ----------------

class ProvinceRegistrationListView(APIView):
    permission_classes = [ProvinceOnly]

    def get(self, request):
        queryset = filter_registrations(Registration.objects.all(), request.query_params)
        return Response(paginated_response(queryset, request, RegistrationListSerializer))


class ProvinceRegistrationDetailView(APIView):
    permission_classes = [ProvinceOnly]

    def get(self, request, pk: int):
        return Response(RegistrationDetailSerializer(_get_registration(pk)).data)


class ProvinceRegistrationApproveView(APIView):
    permission_classes = [ProvinceOnly]

    def post(self, request, pk: int):
        registration = RegistrationService().province_review(request.user, _get_registration(pk), approve=True)
        return Response(RegistrationDetailSerializer(registration).data)


class ProvinceRegistrationRejectView(APIView):
    permission_classes = [ProvinceOnly]

    def post(self, request, pk: int):
        serializer = RejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration = RegistrationService().province_review(
            request.user, _get_registration(pk), approve=False, reason=serializer.validated_data['reason']
        )
        return Response(RegistrationDetailSerializer(registration).data)


class ProvinceStampedFormView(APIView):
    """查看盖章扫描件：默认附件下载，?inline=1 浏览器在线查看。"""

    permission_classes = [ProvinceOnly]

    def get(self, request, pk: int):
        registration = _get_registration(pk)
        inline = request.query_params.get('inline') == '1'
        return material_download(registration.stamped_form_path, registration.stamped_form_name, inline=inline)


class ProvinceFormDocxView(APIView):
    permission_classes = [ProvinceOnly]

    def get(self, request, pk: int):
        registration = _get_registration(pk)
        buffer = ExportService().registration_docx(registration)
        write_log(RegistrationLog.Action.FORM_DOWNLOAD, request.user, registration)
        response = HttpResponse(
            buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        filename = quote(f'报名表-{registration.team.name}.docx')
        response['Content-Disposition'] = (
            f"attachment; filename=registration_{registration.pk}.docx; filename*=UTF-8''{filename}"
        )
        return response


class ProvinceRegistrationExportView(APIView):
    permission_classes = [ProvinceOnly]

    def get(self, request):
        queryset = filter_registrations(Registration.objects.all(), request.query_params)
        buffer = ExportService().registrations_xlsx(queryset)
        write_log(RegistrationLog.Action.EXPORT, request.user, None, {'total': queryset.count()})
        response = HttpResponse(
            buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = "attachment; filename=registrations.xlsx; filename*=UTF-8''%E6%8A%A5%E5%90%8D%E6%83%85%E5%86%B5.xlsx"
        return response


class ProvinceStatisticsView(APIView):
    """市州/高校报名统计。"""

    permission_classes = [ProvinceOnly]

    def get(self, request):
        queryset = Registration.objects.all()
        competition_id = request.query_params.get('competition_id')
        if competition_id:
            queryset = queryset.filter(competition_id=competition_id)

        status_annotations = {s: Count('id', filter=Q(status=s)) for s, _ in Registration.Status.choices}

        def expand(rows, key_fields):
            result = []
            for row in rows:
                item = {key: row[key] for key in key_fields}
                item['total'] = row['total']
                for s, _ in Registration.Status.choices:
                    item[s] = row.get(s, 0)
                result.append(item)
            return result

        by_city = expand(
            queryset.values('team__school__city_id', 'team__school__city__name')
            .annotate(total=Count('id')).annotate(**status_annotations)
            .order_by('team__school__city_id'),
            ('team__school__city_id', 'team__school__city__name'),
        )
        by_group = expand(
            queryset.values('team__group_type')
            .annotate(total=Count('id')).annotate(**status_annotations)
            .order_by('team__group_type'),
            ('team__group_type',),
        )
        university_by_school = expand(
            queryset.filter(team__group_type='university')
            .values('team__school_id', 'team__school__name')
            .annotate(total=Count('id')).annotate(**status_annotations)
            .order_by('team__school_id'),
            ('team__school_id', 'team__school__name'),
        )
        return Response({
            'by_city': by_city,
            'by_group': by_group,
            'university_by_school': university_by_school,
            'total': queryset.count(),
        })


# ---------------- 教练员/球员身份信息 ----------------

class CoachUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50, required=False)
    phone = serializers.RegexField(r'^1[3-9]\d{9}$', required=False, allow_null=True, allow_blank=True)
    id_card = serializers.CharField(max_length=18, required=False, allow_null=True, allow_blank=True)


class AthleteUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50, required=False)
    jersey_no = serializers.IntegerField(min_value=0, max_value=99, required=False, allow_null=True)
    position = serializers.CharField(max_length=20, required=False, allow_null=True, allow_blank=True)
    id_card = serializers.CharField(max_length=18, required=False, allow_null=True, allow_blank=True)
    team_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class ProvinceCoachListView(APIView):
    permission_classes = [ProvinceOnly]

    def get(self, request):
        queryset = CoachProfile.objects.all().order_by('id')
        keyword = request.query_params.get('keyword')
        if keyword:
            queryset = queryset.filter(
                Q(name__icontains=keyword) | Q(phone__icontains=keyword) | Q(id_card__icontains=keyword)
            )
        return Response(paginated_response(queryset, request, CoachMaterialSerializer))


class ProvinceCoachDetailView(APIView):
    permission_classes = [ProvinceOnly]

    def put(self, request, pk: int):
        profile = CoachProfile.objects.filter(pk=pk).first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '教练不存在', status_code=404)
        serializer = CoachUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        changed = {}
        for field, value in serializer.validated_data.items():
            if getattr(profile, field) != value:
                changed[field] = value
                setattr(profile, field, value)
        if changed:
            profile.save(update_fields=list(changed.keys()) + ['updated_at'])
            write_log(RegistrationLog.Action.IDENTITY_UPDATE, request.user, None,
                      {'target': 'coach', 'coach_id': profile.pk, 'changed': list(changed.keys())})
        return Response(CoachMaterialSerializer(profile).data)


class ProvinceAthleteListView(APIView):
    permission_classes = [ProvinceOnly]

    def get(self, request):
        queryset = AthleteProfile.objects.select_related('team').all().order_by('id')
        keyword = request.query_params.get('keyword')
        if keyword:
            queryset = queryset.filter(
                Q(name__icontains=keyword) | Q(id_card__icontains=keyword) | Q(team__name__icontains=keyword)
            )
        team_id = request.query_params.get('team_id')
        if team_id:
            queryset = queryset.filter(team_id=team_id)
        return Response(paginated_response(queryset, request, PlayerMaterialSerializer))


class ProvinceAthleteDetailView(APIView):
    permission_classes = [ProvinceOnly]

    def put(self, request, pk: int):
        profile = AthleteProfile.objects.filter(pk=pk).first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '球员不存在', status_code=404)
        serializer = AthleteUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        changed = {}
        for field, value in serializer.validated_data.items():
            if getattr(profile, field) != value:
                changed[field] = value
                setattr(profile, field, value)
        if changed:
            profile.save(update_fields=list(changed.keys()) + ['updated_at'])
            write_log(RegistrationLog.Action.IDENTITY_UPDATE, request.user, None,
                      {'target': 'athlete', 'athlete_id': profile.pk, 'changed': list(changed.keys())})
        return Response(PlayerMaterialSerializer(profile).data)


class ProvinceAthleteAvatarView(APIView):
    permission_classes = [ProvinceOnly]

    def post(self, request, pk: int):
        uploaded = request.FILES.get('file')
        if uploaded is None:
            raise BusinessException(ResponseCode.PARAM_INVALID, '请上传文件')
        profile = AthleteProfile.objects.filter(pk=pk).first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '球员不存在', status_code=404)
        service = MaterialService()
        path, name = service.save(f'avatars/{profile.pk}', uploaded, MaterialService.IMAGE_EXTS)
        service.remove_quietly(profile.avatar_path)
        profile.avatar_path = path
        profile.avatar_name = name
        profile.save(update_fields=['avatar_path', 'avatar_name', 'updated_at'])
        write_log(RegistrationLog.Action.AVATAR_UPLOAD, request.user, None,
                  {'athlete_id': profile.pk, 'name': name})
        return Response(PlayerMaterialSerializer(profile).data)

    def get(self, request, pk: int):
        profile = AthleteProfile.objects.filter(pk=pk).first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '球员不存在', status_code=404)
        return material_download(profile.avatar_path, profile.avatar_name, inline=True)


class ProvinceEnrollmentCertDownloadView(APIView):
    permission_classes = [ProvinceOnly]

    def get(self, request, pk: int):
        profile = AthleteProfile.objects.filter(pk=pk).first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '球员不存在', status_code=404)
        return material_download(profile.enrollment_cert_path, profile.enrollment_cert_name)


class ProvinceCoachQualificationDownloadView(APIView):
    permission_classes = [ProvinceOnly]

    def get(self, request, pk: int):
        profile = CoachProfile.objects.filter(pk=pk).first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '教练不存在', status_code=404)
        return material_download(profile.qualification_path, profile.qualification_name)


# ---------------- 报名账号管理（市州/省级/管理员账号） ----------------

class AccountSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    city_id = serializers.SerializerMethodField()
    province_id = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'name', 'phone', 'email', 'role', 'role_display', 'status', 'city_id', 'province_id', 'date_joined')

    def get_city_id(self, obj):
        profile = getattr(obj, 'city_profile', None)
        return profile.city_id if profile else None

    def get_province_id(self, obj):
        profile = getattr(obj, 'province_profile', None)
        return profile.province_id if profile else None


class AccountCreateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=MANAGED_ACCOUNT_ROLES)
    name = serializers.CharField(max_length=50)
    password = serializers.CharField(min_length=6, max_length=64, write_only=True)
    phone = serializers.RegexField(r'^1[3-9]\d{9}$', required=False, allow_blank=True)
    email = serializers.EmailField(max_length=191, required=False, allow_blank=True)
    city_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    province_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)

    def validate(self, attrs):
        role = attrs['role']
        if role == UserRole.ADMIN:
            if not attrs.get('email'):
                raise serializers.ValidationError({'email': '管理员账号必须提供邮箱'})
            if User.objects.filter(email__iexact=attrs['email']).exists():
                raise serializers.ValidationError({'email': '该邮箱已被使用'})
        else:
            if not attrs.get('phone'):
                raise serializers.ValidationError({'phone': '市州/省级账号必须提供手机号'})
            if User.objects.filter(phone=attrs['phone']).exists():
                raise serializers.ValidationError({'phone': '该手机号已被注册'})
        return attrs


class AccountUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50, required=False)
    phone = serializers.RegexField(r'^1[3-9]\d{9}$', required=False)
    email = serializers.EmailField(max_length=191, required=False)
    status = serializers.ChoiceField(choices=('active', 'disabled'), required=False)
    password = serializers.CharField(min_length=6, max_length=64, write_only=True, required=False)
    city_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    province_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)


def _get_managed_account(pk: int) -> User:
    user = User.objects.filter(pk=pk, role__in=MANAGED_ACCOUNT_ROLES).first()
    if user is None:
        raise BusinessException(ResponseCode.NOT_FOUND, '账号不存在或不属于报名账号范围', status_code=404)
    return user


def _ensure_admin_role_allowed(request, target_role: str) -> None:
    """管理员角色的账号仅管理员本人可建/改/删。"""
    if target_role == UserRole.ADMIN and not is_admin(request.user):
        raise BusinessException(ResponseCode.FORBIDDEN, '管理员账号仅总管理端可操作')


class ProvinceAccountListCreateView(APIView):
    permission_classes = [ProvinceOnly]

    def get(self, request):
        queryset = User.objects.filter(role__in=MANAGED_ACCOUNT_ROLES).order_by('id')
        role = request.query_params.get('role')
        if role:
            queryset = queryset.filter(role=role)
        keyword = request.query_params.get('keyword')
        if keyword:
            queryset = queryset.filter(
                Q(name__icontains=keyword) | Q(phone__icontains=keyword) | Q(email__icontains=keyword)
            )
        return Response(paginated_response(queryset, request, AccountSerializer))

    def post(self, request):
        from django.db import transaction

        serializer = AccountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        _ensure_admin_role_allowed(request, data['role'])

        with transaction.atomic():
            if data['role'] == UserRole.ADMIN:
                user = User.objects.create_user(
                    username=data['email'], email=data['email'], name=data['name'],
                    role=UserRole.ADMIN, password=data['password'],
                )
            else:
                user = User.objects.create_user(
                    username=data['phone'], phone=data['phone'], name=data['name'],
                    role=data['role'], password=data['password'],
                )
            if data['role'] == UserRole.CITY:
                CityProfile.objects.create(user=user, name=data['name'], city_id=data.get('city_id'))
            elif data['role'] == UserRole.PROVINCE:
                ProvinceProfile.objects.create(user=user, name=data['name'], province_id=data.get('province_id'))

        write_log(RegistrationLog.Action.ACCOUNT_CREATE, request.user, None,
                  {'target_user_id': user.pk, 'role': user.role})
        return Response(AccountSerializer(user).data)


class ProvinceAccountDetailView(APIView):
    permission_classes = [ProvinceOnly]

    def put(self, request, pk: int):
        target = _get_managed_account(pk)
        _ensure_admin_role_allowed(request, target.role)
        serializer = AccountUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        changed = []
        for field in ('name', 'phone', 'email', 'status'):
            if field in data and getattr(target, field) != data[field]:
                setattr(target, field, data[field])
                changed.append(field)
        if 'password' in data:
            target.set_password(data['password'])
            changed.append('password')
        if changed:
            target.save()

        if 'city_id' in data and target.role == UserRole.CITY:
            profile = getattr(target, 'city_profile', None) or CityProfile(user=target, name=target.name)
            profile.city_id = data['city_id']
            profile.save()
            changed.append('city_id')
        if 'province_id' in data and target.role == UserRole.PROVINCE:
            profile = getattr(target, 'province_profile', None) or ProvinceProfile(user=target, name=target.name)
            profile.province_id = data['province_id']
            profile.save()
            changed.append('province_id')

        if changed:
            write_log(RegistrationLog.Action.ACCOUNT_UPDATE, request.user, None,
                      {'target_user_id': target.pk, 'role': target.role, 'changed': changed})
        return Response(AccountSerializer(target).data)

    def delete(self, request, pk: int):
        target = _get_managed_account(pk)
        _ensure_admin_role_allowed(request, target.role)
        if target.pk == request.user.pk:
            raise BusinessException(ResponseCode.PARAM_INVALID, '不能删除当前登录账号')
        role, name = target.role, target.name
        target.delete()
        write_log(RegistrationLog.Action.ACCOUNT_DELETE, request.user, None,
                  {'target_user_id': pk, 'role': role, 'name': name})
        return Response({'id': pk})


# ---------------- 报名日志 ----------------

class ProvinceLogListView(APIView):
    permission_classes = [ProvinceOnly]

    def get(self, request):
        queryset = RegistrationLog.objects.all()
        params = request.query_params
        if params.get('action'):
            queryset = queryset.filter(action=params['action'])
        if params.get('registration_id'):
            queryset = queryset.filter(registration_id=params['registration_id'])
        if params.get('actor_id'):
            queryset = queryset.filter(actor_id=params['actor_id'])
        if params.get('date_from'):
            queryset = queryset.filter(created_at__date__gte=params['date_from'])
        if params.get('date_to'):
            queryset = queryset.filter(created_at__date__lte=params['date_to'])
        return Response(paginated_response(queryset, request, RegistrationLogSerializer))
