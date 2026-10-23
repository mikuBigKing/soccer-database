"""健康检查（对应 Laravel GET /api/health）。"""
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({'status': 'up', 'time': timezone.localtime().isoformat()})
