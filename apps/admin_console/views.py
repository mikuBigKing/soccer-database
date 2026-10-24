"""管理端球员 / 教练员接口（只收发，业务在 services）。"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import role_permission

from . import services
from .serializers import (
    CoachQuerySerializer,
    CoachUpdateSerializer,
    PlayerQuerySerializer,
    PlayerUpdateSerializer,
)

ADMIN_ONLY = [IsAuthenticated, role_permission('admin')]


# --------------------------- 球员 ---------------------------

class PlayerListView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request):
        serializer = PlayerQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(services.list_players(request, serializer.validated_data))


class PlayerDetailView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request, pk: int):
        return Response(services.get_player(pk))

    def put(self, request, pk: int):
        serializer = PlayerUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.update_player(request, pk, serializer.validated_data))

    def delete(self, request, pk: int):
        return Response(services.delete_player(request, pk))


class PlayerPublishView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request, pk: int):
        return Response(services.publish_player(request, pk))


class PlayerUnpublishView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request, pk: int):
        return Response(services.unpublish_player(request, pk))


class PlayerResetPasswordView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request, pk: int):
        return Response(services.reset_player_password(request, pk))


# --------------------------- 教练员 ---------------------------

class CoachListView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request):
        serializer = CoachQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(services.list_coaches(request, serializer.validated_data))


class CoachDetailView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request, pk: int):
        return Response(services.get_coach(pk))

    def put(self, request, pk: int):
        serializer = CoachUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.update_coach(request, pk, serializer.validated_data))

    def delete(self, request, pk: int):
        return Response(services.delete_coach(request, pk))


class CoachPublishView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request, pk: int):
        return Response(services.publish_coach(request, pk))


class CoachUnpublishView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request, pk: int):
        return Response(services.unpublish_coach(request, pk))


class CoachResetPasswordView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request, pk: int):
        return Response(services.reset_coach_password(request, pk))
