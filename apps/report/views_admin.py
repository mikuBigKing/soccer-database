"""赛事报告管理端视图。"""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import role_permission

from .models import MatchReport
from .serializers import serialize_admin
from .services import generate_report, get_match


class AdminMatchReportView(APIView):
    permission_classes = [role_permission('admin', 'province')]

    def get(self, request, pk: int):
        match = get_match(pk)
        report = MatchReport.objects.filter(match=match).first()
        return Response(serialize_admin(match, report))

    def post(self, request, pk: int):
        force = bool(request.data.get('force')) if isinstance(request.data, dict) else False
        report = generate_report(pk, getattr(request.user, 'pk', None), force=force)
        match = get_match(pk)
        return Response(serialize_admin(match, report))
