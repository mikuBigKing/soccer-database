"""全局审计：业务层写详细记录，中间件对未显式记录的写请求兜底。"""
import logging

logger = logging.getLogger(__name__)

WRITE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}


def client_ip(request) -> str:
    remote_addr = (request.META.get('REMOTE_ADDR') or '')[:64]
    return remote_addr


def write_audit(request, *, module: str, action: str, resource_type: str = '',
                resource_id: int = None, before=None, after=None,
                actor=None) -> None:
    """写一条审计；标记请求已记录，避免中间件重复写。"""
    try:
        from apps.admin_console.models import AuditLog

        user = actor or getattr(request, 'user', None)
        if user is not None and not getattr(user, 'is_authenticated', False):
            user = None
        AuditLog.objects.create(
            actor=user,
            actor_role=(getattr(user, 'role', '') or '')[:20] if user else '',
            module=(module or '')[:32],
            action=(action or '')[:64],
            resource_type=(resource_type or '')[:32],
            resource_id=resource_id,
            before=before,
            after=after,
            ip=client_ip(request),
            trace_id=(getattr(request, 'trace_id', '') or '')[:64],
        )
        setattr(request, '_audit_written', True)
    except Exception:  # 审计失败不能影响业务
        logger.exception('write_audit failed')


def _guess_module(path: str) -> str:
    parts = [p for p in path.split('/') if p]
    if len(parts) >= 2 and parts[0] == 'api':
        return parts[1][:32]
    return (parts[0] if parts else '')[:32]


class AuditLogMiddleware:
    """兜底：登录用户的状态变更请求若未显式写审计，则记录一条 HTTP 级审计。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            if (request.method in WRITE_METHODS
                    and response.status_code < 400
                    and not getattr(request, '_audit_written', False)):
                user = getattr(request, 'user', None)
                if user is not None and user.is_authenticated:
                    from apps.admin_console.models import AuditLog

                    path = request.path_info
                    resource_id = None
                    tail = path.rstrip('/').rsplit('/', 1)[-1]
                    if tail.isdigit():
                        resource_id = int(tail)
                    AuditLog.objects.create(
                        actor=user,
                        actor_role=(user.role or '')[:20],
                        module=_guess_module(path),
                        action=f'{request.method} {path}'[:64],
                        resource_type='http',
                        resource_id=resource_id,
                        ip=client_ip(request),
                        trace_id=(getattr(request, 'trace_id', '') or '')[:64],
                    )
        except Exception:
            logger.exception('audit middleware failed')
        return response
