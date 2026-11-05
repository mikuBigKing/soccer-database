"""串口帧解析：JSON + '*' + CRC32(8 位十六进制) + CRLF。

示例：
  {"v":1,"dev":"GW01","seq":1027,"h":2,"a":1,"st":"2H","clk":67,"ts":1730800000}*A1B2C3D4
"""
import binascii
import json
from typing import Optional

STAGE_MAP = {
    'NS': 'not_started',
    '1H': 'first_half',
    'HT': 'half_time',
    '2H': 'second_half',
    'ET': 'extra_time',
    'PK': 'penalty',
    'FT': 'finished',
}

REQUIRED_KEYS = ('seq', 'h', 'a', 'st')


class FrameError(Exception):
    """报文不合法（CRC/结构/枚举），丢弃并计数。"""


def crc32_hex(payload: bytes) -> str:
    return format(binascii.crc32(payload) & 0xFFFFFFFF, '08X')


def parse_line(line: bytes) -> dict:
    if isinstance(line, str):
        line = line.encode('utf-8')
    line = line.strip()
    if not line:
        raise FrameError('空报文')
    if b'*' not in line:
        raise FrameError('缺少 CRC 分隔符')

    body, crc_text = line.rsplit(b'*', 1)
    try:
        crc_value = int(crc_text.strip(), 16)
    except ValueError as exc:
        raise FrameError('CRC 非十六进制') from exc
    if binascii.crc32(body) & 0xFFFFFFFF != crc_value:
        raise FrameError('CRC 校验失败')

    try:
        data = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrameError('JSON 解析失败') from exc
    if not isinstance(data, dict):
        raise FrameError('报文必须是 JSON 对象')
    for key in REQUIRED_KEYS:
        if key not in data:
            raise FrameError('缺少字段 %s' % key)

    stage = STAGE_MAP.get(str(data['st']).upper())
    if stage is None:
        raise FrameError('未知阶段 %s' % data['st'])

    try:
        seq = int(data['seq'])
        home = int(data['h'])
        away = int(data['a'])
    except (TypeError, ValueError) as exc:
        raise FrameError('seq/比分必须为整数') from exc
    if seq < 1 or home < 0 or away < 0:
        raise FrameError('seq/比分取值非法')

    clock = data.get('clk')
    try:
        clock_minute = int(clock) if clock is not None else 0
    except (TypeError, ValueError) as exc:
        raise FrameError('clk 必须为整数') from exc

    return {
        'seq': seq,
        'home_score': home,
        'away_score': away,
        'stage': stage,
        'clock_minute': max(0, min(clock_minute, 150)),
    }


def build_line(payload: dict) -> bytes:
    """测试/模拟器用：按同一协议拼帧。"""
    body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return body + b'*' + crc32_hex(body).encode('ascii') + b'\r\n'
