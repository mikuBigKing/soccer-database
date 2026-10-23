"""提交方（教练/校级）报名视图。

- GET  /api/competitions                          赛事列表（登录即可）
- GET  /api/registrations                         我的报名单
- POST /api/registrations                         创建报名单（草稿）
- GET  /api/registrations/<id>                    报名单详情
- POST /api/registrations/<id>/submit             提交报名（驳回后可重新提交）
- POST /api/registrations/<id>/stamped-form       上传盖章报名表扫描件
- POST /api/registrations/<id>/athletes/<aid>/enrollment-cert   上传球员学籍验证材料
- POST /api/registrations/<id>/coach-qualification              上传教练资质附件
"""
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AthleteProfile, CoachProfile
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.pagination import paginated_response
from apps.common.permissions import role_permission
from apps.registration.models import Competition, Registration, RegistrationLog
from apps.registration.serializers import (
    CompetitionSerializer,
    RegistrationDetailSerializer,
    RegistrationListSerializer,
)
from apps.registration.services import (
    MaterialService,
    RegistrationService,
    _get_registration,
    _submitter_team_ids,
    filter_registrations,
    write_log,
)

SubmitterOnly = role_permission('coach', 'school')


class CreateRegistrationSerializer(serializers.Serializer):
    competition_id = serializers.IntegerField(min_value=1)
    team_id = serializers.IntegerField(min_value=1)


class CompetitionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = Competition.objects.all()
        status_value = request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        return Response(paginated_response(queryset, request, CompetitionSerializer))


class MyRegistrationListCreateView(APIView):
    permission_classes = [SubmitterOnly]

    def get(self, request):
        team_ids = _submitter_team_ids(request.user)
        queryset = Registration.objects.filter(team_id__in=team_ids)
        queryset = filter_registrations(queryset, request.query_params)
        return Response(paginated_response(queryset, request, RegistrationListSerializer))

    def post(self, request):
        serializer = CreateRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration = RegistrationService().create_draft(
            request.user,
            serializer.validated_data['competition_id'],
            serializer.validated_data['team_id'],
        )
        return Response(RegistrationDetailSerializer(registration).data)


class MyRegistrationDetailView(APIView):
    permission_classes = [SubmitterOnly]

    def get(self, request, pk: int):
        registration = _get_registration(pk)
        if registration.team_id not in _submitter_team_ids(request.user):
            raise BusinessException(ResponseCode.FORBIDDEN, '仅可查看本校/本队的报名单')
        return Response(RegistrationDetailSerializer(registration).data)


class RegistrationSubmitView(APIView):
    permission_classes = [SubmitterOnly]

    def post(self, request, pk: int):
        registration = RegistrationService().submit(request.user, _get_registration(pk))
        return Response(RegistrationDetailSerializer(registration).data)


class StampedFormUploadView(APIView):
    permission_classes = [SubmitterOnly]

    def post(self, request, pk: int):
        uploaded = request.FILES.get('file')
        if uploaded is None:
            raise BusinessException(ResponseCode.PARAM_INVALID, '请上传文件')
        registration = RegistrationService().upload_stamped_form(request.user, _get_registration(pk), uploaded)
        return Response(RegistrationDetailSerializer(registration).data)


class EnrollmentCertUploadView(APIView):
    """上传某球员的学籍验证材料（材料挂在 AthleteProfile 上）。"""

    permission_classes = [SubmitterOnly]

    def post(self, request, pk: int, athlete_id: int):
        uploaded = request.FILES.get('file')
        if uploaded is None:
            raise BusinessException(ResponseCode.PARAM_INVALID, '请上传文件')
        registration = _get_registration(pk)
        if registration.team_id not in _submitter_team_ids(request.user):
            raise BusinessException(ResponseCode.FORBIDDEN, '仅可操作本校/本队的报名单')
        profile = AthleteProfile.objects.filter(pk=athlete_id, team=registration.team).first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '该球员不在此球队中', status_code=404)

        service = MaterialService()
        path, name = service.save(f'athletes/{profile.pk}', uploaded, MaterialService.SCAN_EXTS)
        service.remove_quietly(profile.enrollment_cert_path)
        profile.enrollment_cert_path = path
        profile.enrollment_cert_name = name
        profile.save(update_fields=['enrollment_cert_path', 'enrollment_cert_name', 'updated_at'])
        write_log(RegistrationLog.Action.MATERIAL_UPLOAD, request.user, registration,
                  {'material': 'enrollment_cert', 'athlete_id': profile.pk, 'name': name})
        return Response(RegistrationDetailSerializer(registration).data)


class CoachQualificationUploadView(APIView):
    """上传球队教练的资质附件（材料挂在 CoachProfile 上）。"""

    permission_classes = [SubmitterOnly]

    def post(self, request, pk: int):
        uploaded = request.FILES.get('file')
        if uploaded is None:
            raise BusinessException(ResponseCode.PARAM_INVALID, '请上传文件')
        registration = _get_registration(pk)
        if registration.team_id not in _submitter_team_ids(request.user):
            raise BusinessException(ResponseCode.FORBIDDEN, '仅可操作本校/本队的报名单')
        profile = CoachProfile.objects.filter(user=registration.team.coach).first()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '该球队教练尚无档案', status_code=404)

        service = MaterialService()
        path, name = service.save(f'coaches/{profile.pk}', uploaded, MaterialService.SCAN_EXTS)
        service.remove_quietly(profile.qualification_path)
        profile.qualification_path = path
        profile.qualification_name = name
        profile.save(update_fields=['qualification_path', 'qualification_name', 'updated_at'])
        write_log(RegistrationLog.Action.MATERIAL_UPLOAD, request.user, registration,
                  {'material': 'coach_qualification', 'coach_id': profile.pk, 'name': name})
        return Response(RegistrationDetailSerializer(registration).data)
