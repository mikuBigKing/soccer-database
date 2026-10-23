"""DRF 权限类（对应 Laravel EnsureRole / EnsurePermission 中间件）。"""
from rest_framework.permissions import SAFE_METHODS, BasePermission


def is_admin(user) -> bool:
    return bool(user and user.is_authenticated and (getattr(user, 'role', None) == 'admin' or user.is_superuser))


def has_perm(user, perm: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    if is_admin(user):
        return True
    return perm in (getattr(user, 'permissions', None) or [])


class RolePermission(BasePermission):
    """角色白名单：role:admin,school 的等价物。"""

    roles = ()
    message = '无操作权限'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return is_admin(user) or getattr(user, 'role', None) in self.roles


def role_permission(*roles: str):
    """生成角色权限类：role_permission('admin', 'school')"""
    return type(f'RolePermission_{"_".join(roles)}', (RolePermission,), {'roles': tuple(roles)})


class LivePermission(BasePermission):
    """权限点白名单：permission:live:match:view 的等价物。管理员直通。"""

    perm = ''
    message = '无操作权限'

    def has_permission(self, request, view):
        return has_perm(request.user, self.perm)


def live_permission(perm: str):
    return type(f'LivePermission_{perm.replace(":", "_")}', (LivePermission,), {'perm': perm})


class ReadOnlyOrAdmin(BasePermission):
    """公众只读，写操作仅管理员。"""

    message = '公开内容仅管理员可修改'

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return is_admin(request.user)
