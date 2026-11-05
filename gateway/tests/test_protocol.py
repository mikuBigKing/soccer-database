"""网关协议 / 队列 / 签名单测。

运行（仓库根目录）：
    python -m unittest gateway.tests.test_protocol -v
"""
import hashlib
import hmac
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.cloud_client import CloudClient, REPORT_PATH  # noqa: E402
from gateway.queue_store import (  # noqa: E402
    STATUS_ACKED,
    STATUS_DEAD,
    STATUS_PENDING,
    STATUS_SENT,
    QueueStore,
)
from gateway.serial_reader import FrameError, STAGE_MAP, build_line, crc32_hex, parse_line  # noqa: E402


def make_frame(**overrides) -> bytes:
    payload = {'v': 1, 'dev': 'GW01', 'seq': 1027, 'h': 2, 'a': 1, 'st': '2H', 'clk': 67}
    payload.update(overrides)
    return build_line(payload)


class SerialProtocolTest(unittest.TestCase):
    def test_parse_valid_frame(self):
        parsed = parse_line(make_frame())
        self.assertEqual(parsed['seq'], 1027)
        self.assertEqual(parsed['home_score'], 2)
        self.assertEqual(parsed['away_score'], 1)
        self.assertEqual(parsed['stage'], 'second_half')
        self.assertEqual(parsed['clock_minute'], 67)

    def test_crc_mismatch_rejected(self):
        line = make_frame()
        body, crc = line.rsplit(b'*', 1)
        bad = body + b'*' + b'00000000' + b'\r\n'
        with self.assertRaises(FrameError) as ctx:
            parse_line(bad)
        self.assertIn('CRC', str(ctx.exception))

    def test_missing_crc_separator(self):
        with self.assertRaises(FrameError):
            parse_line(b'{"seq":1,"h":0,"a":0,"st":"NS"}')

    def test_missing_required_key(self):
        with self.assertRaises(FrameError) as ctx:
            parse_line(build_line({'seq': 1, 'h': 0, 'a': 0}))
        self.assertIn('st', str(ctx.exception))

    def test_unknown_stage(self):
        with self.assertRaises(FrameError) as ctx:
            parse_line(make_frame(st='XX'))
        self.assertIn('未知阶段', str(ctx.exception))

    def test_clock_clamped(self):
        self.assertEqual(parse_line(make_frame(clk=999))['clock_minute'], 150)
        self.assertEqual(parse_line(make_frame(clk=-5))['clock_minute'], 0)
        self.assertEqual(parse_line(build_line({'seq': 1, 'h': 0, 'a': 0, 'st': 'NS'}))['clock_minute'], 0)

    def test_bad_numeric_fields(self):
        with self.assertRaises(FrameError):
            parse_line(make_frame(seq='abc'))
        with self.assertRaises(FrameError):
            parse_line(make_frame(h=-1))

    def test_stage_map_complete(self):
        self.assertEqual(
            set(STAGE_MAP.values()),
            {'not_started', 'first_half', 'half_time', 'second_half', 'extra_time', 'penalty', 'finished'},
        )
        for code, expected in STAGE_MAP.items():
            self.assertEqual(parse_line(make_frame(st=code))['stage'], expected)

    def test_build_parse_roundtrip(self):
        payload = {'seq': 7, 'h': 3, 'a': 2, 'st': 'FT', 'clk': 90}
        line = build_line(payload)
        parsed = parse_line(line)
        self.assertEqual(parsed['stage'], 'finished')
        self.assertEqual(parsed['home_score'], 3)
        body, crc = line.strip().rsplit(b'*', 1)
        self.assertEqual(crc32_hex(body), crc.decode())


class QueueStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = QueueStore(str(Path(self.tmp.name) / 'queue.sqlite3'))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_enqueue_dedupes_seq(self):
        payload = {'seq': 1, 'home_score': 0, 'away_score': 0, 'stage': 'not_started'}
        self.assertTrue(self.store.enqueue(1, payload))
        self.assertFalse(self.store.enqueue(1, payload))
        self.assertEqual(self.store.stats()[STATUS_PENDING], 1)

    def test_next_item_follows_seq_order(self):
        self.store.enqueue(3, {'seq': 3})
        self.store.enqueue(1, {'seq': 1})
        self.store.enqueue(2, {'seq': 2})
        item = self.store.next_item()
        self.assertEqual(item['seq'], 1)

    def test_ack_advances_queue(self):
        self.store.enqueue(1, {'seq': 1})
        item = self.store.next_item()
        self.store.mark_sent(item['id'])
        self.assertEqual(self.store.stats()[STATUS_SENT], 1)
        self.store.mark_acked(item['id'])
        self.assertEqual(self.store.stats()[STATUS_ACKED], 1)
        self.assertIsNone(self.store.next_item())

    def test_reschedule_keeps_pending(self):
        self.store.enqueue(1, {'seq': 1})
        item = self.store.next_item()
        self.store.mark_sent(item['id'])
        self.store.reschedule(item['id'], 0, 'timeout')
        pending = self.store.next_item()
        self.assertEqual(pending['status'], STATUS_PENDING)
        self.assertEqual(pending['attempts'], 1)
        self.assertEqual(pending['last_error'], 'timeout')

    def test_dead_not_retried(self):
        self.store.enqueue(1, {'seq': 1})
        item = self.store.next_item()
        self.store.mark_dead(item['id'], 'code=40002')
        self.assertEqual(self.store.stats()[STATUS_DEAD], 1)
        self.assertIsNone(self.store.next_item())

    def test_reopen_keeps_queue(self):
        self.store.enqueue(1, {'seq': 1})
        self.store.close()
        self.store = QueueStore(str(Path(self.tmp.name) / 'queue.sqlite3'))
        self.assertEqual(self.store.stats()[STATUS_PENDING], 1)

    def test_cross_thread_access(self):
        """串口线程写、上传线程读：连接跨线程共享，需不抛 ProgrammingError。"""
        self.store.enqueue(1, {'seq': 1})
        seen = []

        def worker():
            item = self.store.next_item()
            seen.append(item['seq'] if item else None)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(seen, [1])


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        if self.payload is None:
            raise ValueError('not json')
        return self.payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.captured = None

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.captured = {'method': method, 'url': url, 'data': data, 'headers': headers, 'timeout': timeout}
        if self.error is not None:
            raise self.error
        return self.response


class CloudClientTest(unittest.TestCase):
    def make_client(self, session=None, base_url='http://127.0.0.1:8000'):
        return CloudClient(base_url, 'GW01', 'top-secret', 10, session=session)

    def test_sign_matches_cloud_algorithm(self):
        client = self.make_client()
        body = b'{"seq":1}'
        sign = client.sign('POST', '/api/device/live/report', '1730800123', 'nonce1', body)
        expected_str = 'POST\n/api/device/live/report\n1730800123\nnonce1\n' + hashlib.sha256(body).hexdigest()
        expected = hmac.new(b'top-secret', expected_str.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(sign, expected)

    def test_path_with_base_prefix(self):
        client = self.make_client(base_url='https://example.com/soccer/')
        self.assertEqual(client.path, '/soccer' + REPORT_PATH)
        self.assertEqual(client.url, 'https://example.com/soccer' + REPORT_PATH)

    def test_report_headers_and_body(self):
        session = FakeSession(FakeResponse(200, {'code': 0, 'msg': 'ok', 'data': {}}))
        client = self.make_client(session=session)
        result = client.report({'seq': 5, 'home_score': 1, 'away_score': 0,
                                'stage': 'first_half', 'clock_minute': 10})
        self.assertTrue(result.ok)
        captured = session.captured
        self.assertEqual(captured['headers']['X-Device-Id'], 'GW01')
        body = json.loads(captured['data'])
        self.assertEqual(body['seq'], 5)
        sign = client.sign('POST', client.path, captured['headers']['X-Timestamp'],
                           captured['headers']['X-Nonce'], captured['data'])
        self.assertEqual(sign, captured['headers']['X-Sign'])

    def test_idempotent_and_out_of_order_are_ack(self):
        for code in (40009, 40004):
            session = FakeSession(FakeResponse(200, {'code': code, 'msg': 'dup'}))
            result = self.make_client(session=session).report({'seq': 1})
            self.assertTrue(result.ok)
            self.assertEqual(result.code, code)

    def test_auth_failure_is_not_retryable(self):
        session = FakeSession(FakeResponse(400, {'code': 20004, 'msg': '签名校验失败'}))
        result = self.make_client(session=session).report({'seq': 1})
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        self.assertEqual(result.code, 20004)

    def test_match_finished_is_dead(self):
        session = FakeSession(FakeResponse(400, {'code': 40002, 'msg': '比赛已结束'}))
        result = self.make_client(session=session).report({'seq': 1})
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)

    def test_server_busy_is_retryable(self):
        session = FakeSession(FakeResponse(200, {'code': 90001, 'msg': '系统繁忙'}))
        result = self.make_client(session=session).report({'seq': 1})
        self.assertFalse(result.ok)
        self.assertTrue(result.retryable)

    def test_network_error_is_retryable(self):
        session = FakeSession(error=requests.ConnectionError('timeout'))
        result = self.make_client(session=session).report({'seq': 1})
        self.assertFalse(result.ok)
        self.assertTrue(result.retryable)

    def test_html_500_is_retryable(self):
        session = FakeSession(FakeResponse(500, None))
        result = self.make_client(session=session).report({'seq': 1})
        self.assertTrue(result.retryable)

    def test_html_400_is_dead(self):
        session = FakeSession(FakeResponse(400, None))
        result = self.make_client(session=session).report({'seq': 1})
        self.assertFalse(result.retryable)


if __name__ == '__main__':
    unittest.main()
