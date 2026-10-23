"""trace_id 与访问日志中间件（对应 Laravel TraceIdMiddleware / ApiLogMiddleware）。"""
import logging
import time
import uuid

api_logger = logging.getLogger('api')


class TraceIDMiddleware:
    HEADER = 'X-Request-ID'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        trace_id = request.headers.get(self.HEADER) or uuid.uuid4().hex
        request.trace_id = trace_id
        response = self.get_response(request)
        response[self.HEADER] = trace_id
        return response


class ApiLogMiddleware:
    """记录方法、路径、状态码、耗时、trace_id。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        response = self.get_response(request)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if request.path.startswith('/api/'):
            api_logger.info(
                '%s %s status=%s cost=%sms trace_id=%s',
                request.method,
                request.path,
                getattr(response, 'status_code', '-'),
                elapsed_ms,
                getattr(request, 'trace_id', ''),
            )
        return response
