"""市州端报名视图（role:city，管理员直通）。

- GET  /api/city/registrations                                本市报名单查询/搜索
- GET  /api/city/registrations/<id>                           报名单详情
- POST /api/city/registrations/<id>/approve                   审核通过
- POST /api/city/registrations/<id>/reject                    驳回（必填原因）
- POST /api/city/registrations/<id>/report                    单个报送省级
- POST /api/city/registrations/report                         批量报送 {ids:[...]}
- GET  /api/city/registrations/summary                        全市汇总（按学校/组别统计）
- GET  /api/city/registrations/<id>/stamped-form/download     下载盖章报名表扫描件
- GET  /api/city/athletes/<id>/enrollment-cert/download       下载球员学籍验证材料
- GET  /api/city/coaches/<id>/qualification/download          下载教练资质附件
"""
from django.db.models import Count, Q
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AthleteProfile, CoachProfile
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.pagination import paginated_response
from apps.common.permissions import is_admin, role_permission
from apps.registration.models import Registration
from apps.registration.serializers import (
    BatchReportSerializer,
    RegistrationDetailSerializer,
    RegistrationListSerializer,
    RejectSerializer,
)
from apps.registration.services import (
    RegistrationService,
    _city_id_of,
    _get_registration,
    filter_registrations,
    material_download,
)

CityOnly = role_permission('city')


def _city_queryset(request):
    queryset = Registration.objects.all()
    if is_admin(request.user):
        return queryset
    city_id = _city_id_of(request.user)
    if not city_id:
        return queryset.none()
    return queryset.filter(team__school__city_id=city_id)


def _ensure_city_registration(request, pk: int) -> Registration:
    registration = _get_registration(pk)
    RegistrationService._ensure_city_scope(request.user, registration)
    return registration


class CityRegistrationListView(APIView):
    permission_classes = [CityOnly]

    def get(self, request):
        queryset = filter_registrations(_city_queryset(request), request.query_params)
        return Response(paginated_response(queryset, request, RegistrationListSerializer))


class CityRegistrationDetailView(APIView):
    permission_classes = [CityOnly]

    def get(self, request, pk: int):
        return Response(RegistrationDetailSerializer(_ensure_city_registration(request, pk)).data)


class CityRegistrationApproveView(APIView):
    permission_classes = [CityOnly]

    def post(self, request, pk: int):
        registration = RegistrationService().city_review(request.user, _get_registration(pk), approve=True)
        return Response(RegistrationDetailSerializer(registration).data)


class CityRegistrationRejectView(APIView):
    permission_classes = [CityOnly]

    def post(self, request, pk: int):
        serializer = RejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration = RegistrationService().city_review(
            request.user, _get_registration(pk), approve=False, reason=serializer.validated_data['reason']
        )
        return Response(RegistrationDetailSerializer(registration).data)


class CityRegistrationReportView(APIView):
    permission_classes = [CityOnly]

    def post(self, request, pk: int):
        registration = RegistrationService().report(request.user, _get_registration(pk))
        return Response(RegistrationDetailSerializer(registration).data)


class CityRegistrationBatchReportView(APIView):
    permission_classes = [CityOnly]

    def post(self, request):
        serializer = BatchReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(RegistrationService().report_batch(request.user, serializer.validated_data['ids']))


class CityRegistrationSummaryView(APIView):
    """全市汇总：按学校、按组别统计报名单状态分布。"""

    permission_classes = [CityOnly]

    def get(self, request):
        queryset = _city_queryset(request)
        competition_id = request.query_params.get('competition_id')
        if competition_id:
            queryset = queryset.filter(competition_id=competition_id)

        def status_counts(rows, name_key):
            result = []
            for row in rows:
                item = {'name': row[name_key], 'total': row['total']}
                for status_value, label in Registration.Status.choices:
                    item[status_value] = row.get(status_value, 0)
                result.append(item)
            return result

        by_school_rows = (
            queryset.values('team__school__name')
            .annotate(total=Count('id'))
            .annotate(**{s: Count('id', filter=Q(status=s)) for s, _ in Registration.Status.choices})
            .order_by('team__school__name')
        )
        by_group_rows = (
            queryset.values('team__group_type')
            .annotate(total=Count('id'))
            .annotate(**{s: Count('id', filter=Q(status=s)) for s, _ in Registration.Status.choices})
            .order_by('team__group_type')
        )
        return Response({
            'by_school': status_counts(by_school_rows, 'team__school__name'),
            'by_group': status_counts(by_group_rows, 'team__group_type'),
            'total': queryset.count(),
        })


class CityStampedFormDownloadView(APIView):
    permission_classes = [CityOnly]

    def get(self, request, pk: int):
        registration = _ensure_city_registration(request, pk)
        return material_download(registration.stamped_form_path, registration.stamped_form_name)


class CityEnrollmentCertDownloadView(APIView):
    permission_classes = [CityOnly]

    def get(self, request, pk: int):
        profile = AthleteProfile.objects.filter(pk=pk).select_related('team__school').first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '球员不存在', status_code=404)
        if not is_admin(request.user) and profile.team and profile.team.school.city_id != _city_id_of(request.user):
            raise BusinessException(ResponseCode.FORBIDDEN, '仅可下载本市州球员的材料')
        return material_download(profile.enrollment_cert_path, profile.enrollment_cert_name)


class CityCoachQualificationDownloadView(APIView):
    permission_classes = [CityOnly]

    def get(self, request, pk: int):
        profile = CoachProfile.objects.filter(pk=pk).first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '教练不存在', status_code=404)
        if not is_admin(request.user):
            from apps.org.models import Team

            city_id = _city_id_of(request.user)
            if not Team.objects.filter(coach=profile.user, school__city_id=city_id).exists():
                raise BusinessException(ResponseCode.FORBIDDEN, '仅可下载本市州教练的材料')
        return material_download(profile.qualification_path, profile.qualification_name)
