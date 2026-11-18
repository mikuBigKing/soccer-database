"""JWT 认证扩展：账号被禁用后旧 token 立即失效。"""
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class AccountDisabled(AuthenticationFailed):
    """账号已禁用（对应业务码 20003，HTTP 403）。"""

    default_detail = '账号已被禁用'
    default_code = 'account_disabled'
    status_code = 403


class ActiveUserJWTAuthentication(JWTAuthentication):
    """在 JWT 校验通过后追加账号状态校验。

    JWT 本身无状态，禁用账号后旧 token 仍能通过签名校验；
    这里在每次请求取出用户后检查 users.status / is_active，确保禁用立即生效。
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if not user.is_active or getattr(user, 'status', None) == 'disabled':
            raise AccountDisabled()
        return user
