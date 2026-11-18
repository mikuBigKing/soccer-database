"""认证视图：注册 / 统一登录 / 管理员登录 / 刷新 / 登出 / me / 修改密码。"""
from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
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
        if not user.is_active or user.status == UserStatus.DISABLED:
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
        if not user.is_active or user.status == UserStatus.DISABLED:
            raise BusinessException(ResponseCode.ACCOUNT_DISABLED)

        return Response(_issue_tokens(user))


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh = request.data.get('refresh')
        if refresh:
            try:
                token = RefreshToken(refresh)
            except TokenError:
                # 已失效或非法的 refresh 不影响登出结果
                token = None
            if token is not None:
                # 只允许拉黑自己的 refresh token，避免越权注销他人会话
                if str(token.get('user_id')) != str(request.user.pk):
                    raise BusinessException(ResponseCode.FORBIDDEN, '无权注销其他账号的会话')
                token.blacklist()
        return Response({'logged_out': True})


class RefreshTokenView(APIView):
    """用 refresh token 换取新的 access token（前端 401 前主动续期）。"""

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        raw = (request.data.get('refresh') or '').strip()
        if not raw:
            raise BusinessException(ResponseCode.PARAM_INVALID, '缺少 refresh token')
        try:
            token = RefreshToken(raw)
        except TokenError as exc:
            raise BusinessException(
                ResponseCode.UNAUTHENTICATED, '登录已过期，请重新登录', status_code=401
            ) from exc

        user = User.objects.filter(pk=token.get('user_id')).first()
        if user is None:
            raise BusinessException(ResponseCode.UNAUTHENTICATED, '账号不存在', status_code=401)
        if not user.is_active or user.status == UserStatus.DISABLED:
            raise BusinessException(ResponseCode.ACCOUNT_DISABLED)

        access = str(token.access_token)
        return Response({
            'token': access,
            'access': access,
            'refresh': str(token),
            'token_type': 'Bearer',
        })


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(max_length=64, write_only=True)
    new_password = serializers.CharField(min_length=6, max_length=64, write_only=True)

    def validate_new_password(self, value):
        if value == self.initial_data.get('old_password'):
            raise serializers.ValidationError('新密码不能与原密码相同')
        return value


class ChangePasswordView(APIView):
    """登录用户修改自己的密码。"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(serializer.validated_data['old_password']):
            raise BusinessException(ResponseCode.CREDENTIALS_INVALID, '原密码不正确')
        user.set_password(serializer.validated_data['new_password'])
        user.save(update_fields=['password'])
        return Response({'changed': True})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)
