"""云端上报客户端：HMAC-SHA256 签名 + 错误分类。

签名串（与云端 `DeviceAuthService` 完全一致）：
    f"{method}\\n{path}\\n{timestamp}\\n{nonce}\\n{sha256(body_bytes)}"
"""
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

REPORT_PATH = '/api/device/live/report'

# 云端业务码分类
ACK_CODES = {0, 40009, 40004}  # 0 成功；40009 重复；40004 乱序（云端已有更新的状态）
RETRY_CODES = {90001}          # 系统繁忙
DEAD_CODES = {40001, 40002, 30001, 10001}  # 比分回退/比赛结束/比赛不存在/报文非法
FATAL_CODES = {20004}          # 设备鉴权失败，重试无意义


@dataclass
class ReportResult:
    ok: bool
    retryable: bool
    code: int = 0
    msg: str = ''
    data: dict = field(default_factory=dict)


class CloudClient:
    def __init__(self, base_url: str, device_id: str, secret: str, timeout: int = 10, session=None):
        self.base_url = base_url.rstrip('/')
        self.device_id = device_id
        self.secret = secret
        self.timeout = timeout
        self.session = session or requests

        parsed = urlparse(self.base_url)
        self._prefix = parsed.path.rstrip('/')
        self.path = self._prefix + REPORT_PATH
        self.url = self.base_url + REPORT_PATH

    def sign(self, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
        body_hash = hashlib.sha256(body).hexdigest()
        sign_str = '%s\n%s\n%s\n%s\n%s' % (method, path, timestamp, nonce, body_hash)
        return hmac.new(self.secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()

    def report(self, payload: dict) -> ReportResult:
        body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        headers = {
            'Content-Type': 'application/json',
            'X-Device-Id': self.device_id,
            'X-Timestamp': timestamp,
            'X-Nonce': nonce,
            'X-Sign': self.sign('POST', self.path, timestamp, nonce, body),
        }
        try:
            response = self.session.request(
                'POST', self.url, data=body, headers=headers, timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return ReportResult(ok=False, retryable=True, code=-1, msg='网络异常：%s' % exc)
        except Exception as exc:  # 会话被替换或底层异常，一律按可重试处理
            return ReportResult(ok=False, retryable=True, code=-1, msg='上报异常：%s' % exc)

        try:
            envelope = response.json()
        except ValueError:
            retryable = response.status_code >= 500 or response.status_code == 429
            return ReportResult(
                ok=False, retryable=retryable, code=response.status_code,
                msg='响应非 JSON（HTTP %s）' % response.status_code,
            )

        if not isinstance(envelope, dict):
            return ReportResult(ok=False, retryable=True, code=-2, msg='响应结构异常')

        code = envelope.get('code')
        try:
            code = int(code)
        except (TypeError, ValueError):
            code = -2
        msg = str(envelope.get('msg') or '')
        data = envelope.get('data') or {}

        if code in ACK_CODES:
            return ReportResult(ok=True, retryable=False, code=code, msg=msg, data=data)
        if code in FATAL_CODES:
            return ReportResult(ok=False, retryable=False, code=code, msg=msg, data=data)
        if code in DEAD_CODES:
            return ReportResult(ok=False, retryable=False, code=code, msg=msg, data=data)
        if code in RETRY_CODES or response.status_code >= 500:
            return ReportResult(ok=False, retryable=True, code=code, msg=msg, data=data)
        # 4xx 且非已知业务码：报文/配置问题，重试无意义
        return ReportResult(ok=False, retryable=False, code=code, msg=msg, data=data)
