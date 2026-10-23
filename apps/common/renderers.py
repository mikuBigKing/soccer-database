"""统一响应渲染器。

所有接口输出五字段：
    {"code": 0, "msg": "success", "data": ..., "success": true, "trace_id": "xxx"}
与 Laravel 侧 Result::ok / Result::error 的口径完全一致。
"""
from rest_framework.renderers import JSONRenderer

from .codes import ResponseCode, message_of


def envelope(code: int, msg: str = None, data=None, trace_id: str = '') -> dict:
    return {
        'code': int(code),
        'msg': msg if msg is not None else message_of(code),
        'data': data,
        'success': int(code) == ResponseCode.SUCCESS,
        'trace_id': trace_id or '',
    }


class UnifiedJSONRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        renderer_context = renderer_context or {}
        request = renderer_context.get('request')
        response = renderer_context.get('response')

        # 已封装的响应直接透传，避免二次包裹
        if isinstance(data, dict) and 'code' in data and 'trace_id' in data:
            return super().render(data, accepted_media_type, renderer_context)

        trace_id = getattr(request, 'trace_id', '') if request is not None else ''
        status_code = getattr(response, 'status_code', 200)
        payload = envelope(ResponseCode.SUCCESS if status_code < 400 else status_code, data=data, trace_id=trace_id)
        return super().render(payload, accepted_media_type, renderer_context)
