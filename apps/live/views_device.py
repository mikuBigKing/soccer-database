"""计分网关上报接口：HMAC 设备鉴权，不走 JWT。"""
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException

from .serializers import DeviceScoreReportSerializer
from .services import DeviceAuthService, ScoreboardService


class DeviceScoreReportView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        device = DeviceAuthService().authenticate(request)
        serializer = DeviceScoreReportSerializer(data=request.data)
        if not serializer.is_valid():
            first_error = next(iter(serializer.errors.values()))
            raise BusinessException(ResponseCode.PARAM_INVALID, '报文格式不合法：%s' % first_error[0])
        return Response(ScoreboardService().apply(device, dict(serializer.validated_data)))
