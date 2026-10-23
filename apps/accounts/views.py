"""认证视图：注册 / 统一登录 / 管理员登录 / 登出 / me。"""
from django.contrib.auth import get_user_model
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException

from .models import UserRole, UserStatus
from .serializers import (
    AdminLoginSerializer,
    LoginSerializer,
    RegisterSerializer,
    UserSerializer,
)

User = get_user_model()


def _issue_tokens(user) -> dict:
    refresh = RefreshToken.for_user(user)
    return {
        'token': str(refresh.access_token),
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'token_type': 'Bearer',
        'user': UserSerializer(user).data,
    }


class RegisterView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(_issue_tokens(user))


class LoginView(APIView):
    """非管理员统一登录（phone + password）。"""

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data['phone']
        password = serializer.validated_data['password']

        user = User.objects.filter(phone=phone).first()
        if user is None or not user.check_password(password):
            raise BusinessException(ResponseCode.CREDENTIALS_INVALID)
        if user.role == UserRole.ADMIN:
            raise BusinessException(ResponseCode.FORBIDDEN, '管理员请使用管理员登录入口')
        if user.status == UserStatus.DISABLED:
            raise BusinessException(ResponseCode.ACCOUNT_DISABLED)

        return Response(_issue_tokens(user))


class AdminLoginView(APIView):
    """管理员独立登录（email + password）。"""

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = AdminLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        password = serializer.validated_data['password']

        user = User.objects.filter(email__iexact=email).first()
        if user is None or not user.check_password(password):
            raise BusinessException(ResponseCode.CREDENTIALS_INVALID)
        if not (user.role == UserRole.ADMIN or user.is_superuser):
            raise BusinessException(ResponseCode.FORBIDDEN, '该账号不是管理员')
        if user.status == UserStatus.DISABLED:
            raise BusinessException(ResponseCode.ACCOUNT_DISABLED)

        return Response(_issue_tokens(user))


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh = request.data.get('refresh')
        if refresh:
            try:
                RefreshToken(refresh).blacklist()
            except Exception:
                # 已失效或非法的 refresh 不影响登出结果
                pass
        return Response({'logged_out': True})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)
