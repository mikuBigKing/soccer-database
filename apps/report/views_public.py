"""赛事报告公众端视图（免登）。"""
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import MatchReport
from .serializers import serialize_public
from .services import get_match


class PublicMatchReportView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, pk: int):
        match = get_match(pk)
        report = MatchReport.objects.filter(match=match).first()
        return Response(serialize_public(match, report))
