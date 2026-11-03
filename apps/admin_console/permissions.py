"""管理端专属权限：放行管理员与层级范围管理员。"""
from rest_framework.permissions import BasePermission


ALLOWED_ROLES = {'admin', 'province', 'city', 'school'}


class ScopedRolePermission(BasePermission):
    """放行 admin / province / city / school 四种角色，其余 403。"""

    message = '无操作权限'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        return request.user.role in ALLOWED_ROLES


# 视图层统一用这个：四种角色都能进入，具体对象权限在 services 的 apply_scope / 写操作里校验。
admin_or_scoped_permission = ScopedRolePermission
