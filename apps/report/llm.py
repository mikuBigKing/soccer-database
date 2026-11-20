"""Qwen3 客户端：OpenAI 兼容协议 + JSON 容错 + mock 模式。"""
import json
import logging
import re
import time

from django.conf import settings

from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException

logger = logging.getLogger('business')

REQUIRED_KEYS = ('narrative', 'chapters', 'key_moments', 'highlights', 'confidence')

MOCK_PAYLOAD = {
    'narrative': '本场比赛在雨中开打，双方以稳守为主。上半场第 12 分钟主队率先打破僵局，'
                 '客队随后通过边路连续施压扳回一城。下半场节奏加快，主队在最后阶段完成反超，'
                 '最终以一场逆转拿下比赛。',
    'chapters': [
        {'title': '上半场：僵局与首开纪录', 'content': '开场阶段双方均试探为主，主队依靠中路的连续传递取得先机，第 12 分钟完成破门。',
         'minute_range': '1-45', 'emotion': '紧张'},
        {'title': '下半场：反超与收尾', 'content': '易边后客队体能下降，主队加强边路冲击并在终场前完成制胜一击。',
         'minute_range': '46-90', 'emotion': '高燃'},
    ],
    'key_moments': [
        {'minute': 12, 'type': 'goal', 'description': '主队中路渗透后完成推射破门', 'impact': '打破僵局'},
        {'minute': 78, 'type': 'goal', 'description': '主队快速反击完成门前包抄', 'impact': '完成反超'},
    ],
    'highlights': ['雨中作战，双方传球失误偏多', '主队最后 15 分钟两次反击转化进球'],
    'data_notes': ['本报告为 mock 模式生成，文本不代表真实比赛'],
    'confidence': 80,
}


def _extract_json(text: str) -> dict:
    text = (text or '').strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    start, end = text.find('{'), text.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError('模型返回内容无法解析为 JSON')


def _validate(payload: dict) -> dict:
    missing = [k for k in REQUIRED_KEYS if k not in payload]
    if missing:
        raise ValueError(f'模型返回缺少字段: {",".join(missing)}')
    if not isinstance(payload.get('chapters'), list) or not isinstance(payload.get('key_moments'), list):
        raise ValueError('模型返回的 chapters / key_moments 结构不正确')
    confidence = payload.get('confidence')
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 100:
        raise ValueError('模型返回的 confidence 超出 0-100')
    payload.setdefault('highlights', [])
    payload.setdefault('data_notes', [])
    payload['confidence'] = int(confidence)
    return payload


def chat_json(system: str, user: str, mock: dict = None) -> tuple[dict, dict]:
    """调用 Qwen3 并解析 JSON。

    返回 (payload, usage)，usage 含 model / input_tokens / output_tokens / duration_ms。
    失败统一抛 50009，不向上层泄漏上游响应体。
    """
    if settings.REPORT_LLM_MOCK:
        return _validate(dict(mock or MOCK_PAYLOAD)), {
            'model': 'mock', 'input_tokens': 0, 'output_tokens': 0, 'duration_ms': 0,
        }

    try:
        from openai import OpenAI
    except ImportError:
        raise BusinessException(ResponseCode.AI_SERVICE_FAILED, 'AI 服务依赖未安装（openai）')

    client = OpenAI(
        api_key=settings.REPORT_LLM_API_KEY,
        base_url=settings.REPORT_LLM_BASE_URL,
        timeout=settings.REPORT_LLM_TIMEOUT,
        max_retries=0,  # 重试交给下面的循环，避免上游超时叠加成数分钟
    )

    last_error = None
    for attempt, temperature in enumerate((settings.REPORT_LLM_TEMPERATURE, 0.2)):
        started = time.perf_counter()
        try:
            kwargs = {
                'model': settings.REPORT_LLM_MODEL,
                'temperature': temperature,
                'max_tokens': settings.REPORT_LLM_MAX_TOKENS,
                'messages': [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': user},
                ],
                # DashScope 支持 json_object（不支持 json_schema），配合 Prompt 硬约束
                'response_format': {'type': 'json_object'},
            }
            if settings.REPORT_LLM_DISABLE_THINKING:
                kwargs['extra_body'] = {'enable_thinking': False}
            response = client.chat.completions.create(**kwargs)

            content = response.choices[0].message.content
            payload = _validate(_extract_json(content))
            usage = {
                'model': settings.REPORT_LLM_MODEL,
                'input_tokens': getattr(response.usage, 'prompt_tokens', None),
                'output_tokens': getattr(response.usage, 'completion_tokens', None),
                'duration_ms': int((time.perf_counter() - started) * 1000),
            }
            return payload, usage
        except ValueError as exc:
            last_error = exc
            logger.warning('report llm parse failed attempt=%s: %s', attempt + 1, exc)
        except Exception as exc:  # 网络 / 鉴权 / 限流 / 上游 5xx
            last_error = exc
            logger.warning('report llm call failed attempt=%s: %s', attempt + 1, type(exc).__name__)

    raise BusinessException(ResponseCode.AI_SERVICE_FAILED, 'AI 报告生成失败：%s' % type(last_error).__name__)
