"""运动员端视图（role:athlete）。"""
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.permissions import role_permission

from .services import AthleteService

AthleteOnly = role_permission('athlete')


def _competition_id(request):
    raw = request.query_params.get('competition_id')
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise BusinessException(ResponseCode.PARAM_INVALID, 'competition_id 必须为整数')


class ProfileUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50, required=False)
    jersey_no = serializers.IntegerField(min_value=0, max_value=99, required=False, allow_null=True)
    position = serializers.CharField(max_length=20, required=False, allow_null=True, allow_blank=True)
    id_card = serializers.CharField(max_length=18, required=False, allow_null=True, allow_blank=True)


class AthleteProfileView(APIView):
    permission_classes = [AthleteOnly]

    def get(self, request):
        return Response(AthleteService().profile(request.user))

    def put(self, request):
        serializer = ProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(AthleteService().update_profile(request.user, serializer.validated_data))


class AthleteMatchListView(APIView):
    permission_classes = [AthleteOnly]

    def get(self, request):
        status_value = request.query_params.get('status')
        if status_value and status_value not in ('not_started', 'live', 'finished'):
            return Response({'list': [], 'team_id': None})
        return Response(AthleteService().matches(
            request.user, status_value, competition_id=_competition_id(request)
        ))


class AthleteStatsView(APIView):
    permission_classes = [AthleteOnly]

    def get(self, request):
        return Response(AthleteService().stats(request.user, competition_id=_competition_id(request)))


class AthleteStandingsView(APIView):
    permission_classes = [AthleteOnly]

    def get(self, request):
        return Response(AthleteService().standings(request.user, competition_id=_competition_id(request)))
