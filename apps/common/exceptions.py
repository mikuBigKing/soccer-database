"""统一异常与业务异常。

- BusinessException：业务规则失败，直接带业务码
- DRF 异常：映射到统一码段
"""
import logging

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from .codes import ResponseCode, message_of
from .renderers import envelope


class BusinessException(Exception):
    """业务规则异常。

    默认 HTTP 400；携带业务码，响应体 code 为业务码。
    """

    def __init__(self, code: ResponseCode = ResponseCode.SYSTEM_ERROR, msg: str = None, status_code: int = None):
        self.code = code
        self.msg = msg or message_of(int(code))
        self.status_code = status_code or status.HTTP_400_BAD_REQUEST
        super().__init__(self.msg)


CODE_MAP = {
    exceptions.NotAuthenticated: ResponseCode.UNAUTHENTICATED,
    exceptions.AuthenticationFailed: ResponseCode.CREDENTIALS_INVALID,
    exceptions.PermissionDenied: ResponseCode.FORBIDDEN,
    exceptions.NotFound: ResponseCode.NOT_FOUND,
    exceptions.ValidationError: ResponseCode.PARAM_INVALID,
    exceptions.MethodNotAllowed: ResponseCode.PARAM_INVALID,
    exceptions.Throttled: ResponseCode.SYSTEM_ERROR,
}

DEFAULT_MSG = {
    ResponseCode.UNAUTHENTICATED: '未登录或登录已过期',
    ResponseCode.CREDENTIALS_INVALID: '账号或密码错误',
    ResponseCode.FORBIDDEN: '无操作权限',
    ResponseCode.NOT_FOUND: '资源不存在',
    ResponseCode.PARAM_INVALID: '参数校验失败',
}


def _flatten_error(data) -> str:
    if isinstance(data, dict):
        if 'detail' in data:
            return str(data['detail'])
        for key, value in data.items():
            msg = _flatten_error(value)
            if msg:
                return f'{key}: {msg}' if not str(key).isdigit() else msg
        return ''
    if isinstance(data, list) and data:
        return _flatten_error(data[0])
    return str(data) if data is not None else ''


def custom_exception_handler(exc, context):
    request = context.get('request')
    trace_id = getattr(request, 'trace_id', '') if request else ''

    if isinstance(exc, BusinessException):
        return Response(
            envelope(exc.code, exc.msg, None, trace_id),
            status=exc.status_code,
        )

    response = exception_handler(exc, context)
    if response is None:
        # 未预期异常：统一包成 90001，避免向调用方抛出 HTML 错误页
        logging.getLogger('exception').exception('unhandled api exception: %s', exc)
        return Response(
            envelope(ResponseCode.SYSTEM_ERROR, '系统繁忙，请稍后重试', None, trace_id),
            status=500,
        )

    code = ResponseCode.SYSTEM_ERROR
    for exc_class, mapped in CODE_MAP.items():
        if isinstance(exc, exc_class):
            code = mapped
            break

    msg = _flatten_error(getattr(response, 'data', None)) or DEFAULT_MSG.get(code, message_of(int(code)))
    response.data = envelope(code, msg, None, trace_id)
    return response
