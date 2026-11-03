"""管理端球员 / 教练员接口（只收发，业务在 services）。"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import role_permission

from . import services
from .serializers import (
    AccountStatusSerializer,
    BatchResetPasswordSerializer,
    CoachObjectionSerializer,
    CoachPublishSerializer,
    CoachQuerySerializer,
    CoachUnpublishSerializer,
    CoachUpdateSerializer,
    MergePlayerSerializer,
    ObjectionHandleSerializer,
    ObjectionQuerySerializer,
    PlayerCreateSerializer,
    PlayerQuerySerializer,
    PlayerStatusFlagsSerializer,
    PlayerTeamSerializer,
    PlayerUpdateSerializer,
    ResetPasswordSerializer,
    SuspensionManualSerializer,
    SuspensionQuerySerializer,
    SuspensionRevokeSerializer,
)

ADMIN_ONLY = [IsAuthenticated, role_permission('admin')]


# --------------------------- 球员 ---------------------------

class PlayerListView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request):
        serializer = PlayerQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(services.list_players(request, serializer.validated_data))

    def post(self, request):
        serializer = PlayerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.create_player(request, serializer.validated_data))


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
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.reset_player_password(request, pk, serializer.validated_data))


class PlayerBatchResetPasswordView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request):
        serializer = BatchResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.batch_reset_player_password(request, serializer.validated_data))


class PlayerStatusFlagsView(APIView):
    permission_classes = ADMIN_ONLY

    def put(self, request, pk: int):
        serializer = PlayerStatusFlagsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.update_player_status_flags(request, pk, serializer.validated_data))


class PlayerAccountStatusView(APIView):
    permission_classes = ADMIN_ONLY

    def put(self, request, pk: int):
        serializer = AccountStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.update_player_account_status(request, pk, serializer.validated_data))


class PlayerRestoreView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request, pk: int):
        return Response(services.restore_player(request, pk))


class PlayerSuspensionPreviewView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request, pk: int):
        return Response(services.suspension_preview(request, pk))


class PlayerSuspensionGenerateView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request, pk: int):
        return Response(services.generate_suspension(request, pk))


class PlayerSuspensionListView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request, pk: int):
        serializer = SuspensionQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(services.list_suspensions(request, pk, serializer.validated_data))

    def post(self, request, pk: int):
        serializer = SuspensionManualSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.create_manual_suspension(request, pk, serializer.validated_data))


class SuspensionRevokeView(APIView):
    permission_classes = ADMIN_ONLY

    def put(self, request, pk: int):
        serializer = SuspensionRevokeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.revoke_suspension(request, pk, serializer.validated_data))


class PlayerTeamView(APIView):
    permission_classes = ADMIN_ONLY

    def put(self, request, pk: int):
        serializer = PlayerTeamSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.change_player_team(request, pk, serializer.validated_data))


class PlayerTeamChangesView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request, pk: int):
        return Response(services.list_team_changes(request, pk, request.query_params))


class PlayerMergeView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request):
        serializer = MergePlayerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.merge_players(request, serializer.validated_data))


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
        serializer = CoachPublishSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.publish_coach(request, pk, serializer.validated_data))


class CoachUnpublishView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request, pk: int):
        serializer = CoachUnpublishSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.unpublish_coach(request, pk, serializer.validated_data))


class CoachResetPasswordView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request, pk: int):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.reset_coach_password(request, pk, serializer.validated_data))


class CoachBatchResetPasswordView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request):
        serializer = BatchResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.batch_reset_coach_password(request, serializer.validated_data))


class CoachAccountStatusView(APIView):
    permission_classes = ADMIN_ONLY

    def put(self, request, pk: int):
        serializer = AccountStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.update_coach_account_status(request, pk, serializer.validated_data))


class CoachRestoreView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request, pk: int):
        return Response(services.restore_coach(request, pk))


# --------------------------- 公示期异议 ---------------------------

class CoachObjectionListView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request, pk: int):
        serializer = ObjectionQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(services.list_objections(request, pk, serializer.validated_data))

    def post(self, request, pk: int):
        serializer = CoachObjectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.create_objection(request, pk, serializer.validated_data))


class ObjectionHandleView(APIView):
    permission_classes = ADMIN_ONLY

    def put(self, request, pk: int):
        serializer = ObjectionHandleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.handle_objection(request, pk, serializer.validated_data))
