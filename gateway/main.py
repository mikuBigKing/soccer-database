"""本地计分网关入口：串口线程 + 上传线程 + 统计心跳。

运行（仓库根目录）：
    python gateway/main.py                 # 正式：串口 + 上传
    python gateway/main.py --no-serial     # 只跑上传（联调云端）
    python gateway/main.py --once          # 处理一条队列后退出（自检）

串口不可用时进程不退出，按 5s 重试打开端口，等待现场插线。
"""
import argparse
import logging
import os
import signal
import sys
import threading
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.cloud_client import CloudClient, FATAL_CODES
from gateway.config import ensure_dirs, load_config
from gateway.queue_store import QueueStore
from gateway.serial_reader import FrameError, parse_line

LOGGER = logging.getLogger('gateway')
STATS_INTERVAL = 60
OPEN_RETRY_SECONDS = 5
ERROR_LOG_INTERVAL = 10


def setup_logging(log_path: str, level: int = logging.INFO) -> None:
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s %(threadName)s %(message)s')
    file_handler = TimedRotatingFileHandler(
        log_path, when='midnight', backupCount=30, encoding='utf-8',
    )
    file_handler.setFormatter(fmt)
    LOGGER.setLevel(level)
    LOGGER.handlers.clear()
    LOGGER.addHandler(file_handler)
    # pythonw 下 sys.stderr 为 None，不能挂控制台 handler
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(fmt)
        LOGGER.addHandler(stream_handler)


class SerialWorker(threading.Thread):
    """读串口 -> 校验 CRC -> 入队。解析失败只计数并限速告警。"""

    def __init__(self, serial_config, store: QueueStore, stop_event: threading.Event):
        super().__init__(name='serial', daemon=True)
        self.serial_config = serial_config
        self.store = store
        self.stop_event = stop_event
        self.bad_frames = 0
        self._last_error_log = 0.0
        self._last_error = ''

    def _log_error(self, message: str) -> None:
        now = time.monotonic()
        if message != self._last_error or now - self._last_error_log >= ERROR_LOG_INTERVAL:
            self._last_error = message
            self._last_error_log = now
            LOGGER.warning('串口帧丢弃：%s（累计 %s 帧）', message, self.bad_frames)

    def run(self) -> None:
        import serial  # pyserial，延迟导入便于 --no-serial 场景

        handle = None
        while not self.stop_event.is_set():
            if handle is None:
                try:
                    handle = serial.Serial(
                        self.serial_config.port,
                        self.serial_config.baudrate,
                        timeout=self.serial_config.timeout,
                    )
                    LOGGER.info('串口已打开 %s @%s', self.serial_config.port, self.serial_config.baudrate)
                except Exception as exc:
                    LOGGER.warning('串口打开失败 %s：%s，%ss 后重试',
                                   self.serial_config.port, exc, OPEN_RETRY_SECONDS)
                    self.stop_event.wait(OPEN_RETRY_SECONDS)
                    continue

            try:
                line = handle.readline()
            except Exception as exc:
                LOGGER.warning('串口读取异常，重开端口：%s', exc)
                try:
                    handle.close()
                except Exception:
                    pass
                handle = None
                self.stop_event.wait(1)
                continue

            if not line:
                continue
            try:
                payload = parse_line(line)
            except FrameError as exc:
                self.bad_frames += 1
                self._log_error(str(exc))
                continue
            if self.store.enqueue(payload['seq'], payload):
                LOGGER.info('入队 seq=%s %s-%s stage=%s', payload['seq'],
                            payload['home_score'], payload['away_score'], payload['stage'])
            else:
                LOGGER.debug('重复 seq=%s 已忽略', payload['seq'])

        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


class UploadWorker(threading.Thread):
    """按 seq 顺序上传，失败指数退避；鉴权/结束类错误直接落 dead。"""

    def __init__(self, config, store: QueueStore, client: CloudClient,
                 stop_event: threading.Event, once: bool = False):
        super().__init__(name='upload', daemon=True)
        self.config = config
        self.store = store
        self.client = client
        self.stop_event = stop_event
        self.once = once
        self._last_stats = time.monotonic()

    def _backoff_seconds(self, attempts: int) -> float:
        base = max(1, self.config.cache.retry_base_ms) / 1000.0
        cap = max(base, self.config.cache.retry_max_ms) / 1000.0
        return min(cap, base * (2 ** min(max(attempts - 1, 0), 10)))

    def _log_stats(self) -> None:
        now = time.monotonic()
        if now - self._last_stats < STATS_INTERVAL:
            return
        self._last_stats = now
        LOGGER.info('队列统计 %s', self.store.stats())

    def run(self) -> None:
        while not self.stop_event.is_set():
            item = self.store.next_item()
            if item is None:
                self._log_stats()
                if self.once:
                    break
                self.stop_event.wait(0.5)
                continue

            wait = float(item['next_at']) - time.time()
            if wait > 0:
                self.stop_event.wait(min(wait, 1.0))
                continue

            payload = dict(item['payload'])
            if self.config.cloud.match_id:
                payload.setdefault('match_id', self.config.cloud.match_id)

            self.store.mark_sent(item['id'])
            result = self.client.report(payload)

            if result.ok:
                note = '' if result.code == 0 else 'code=%s %s' % (result.code, result.msg)
                self.store.mark_acked(item['id'], note)
                LOGGER.info('上报成功 seq=%s code=%s', item['seq'], result.code)
            elif result.retryable:
                delay = self._backoff_seconds(int(item['attempts']) + 1)
                self.store.reschedule(item['id'], time.time() + delay, result.msg)
                LOGGER.warning('上报失败 seq=%s code=%s，%ss 后重试：%s',
                               item['seq'], result.code, delay, result.msg)
            else:
                self.store.mark_dead(item['id'], 'code=%s %s' % (result.code, result.msg))
                level = logging.CRITICAL if result.code in FATAL_CODES else logging.ERROR
                LOGGER.log(level, '上报终止 seq=%s code=%s：%s（已标记 dead，请人工处理）',
                           item['seq'], result.code, result.msg)
            if self.once:
                break


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='本地计分网关')
    parser.add_argument('--config', default=None, help='配置文件路径（默认 gateway/config.yaml）')
    parser.add_argument('--no-serial', action='store_true', help='不读串口，仅上传队列')
    parser.add_argument('--once', action='store_true', help='处理一条队列后退出')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args(argv)

    os.chdir(ROOT)
    config_path = str(Path(args.config).resolve()) if args.config else 'gateway/config.yaml'
    config = load_config(config_path)
    ensure_dirs(config)
    setup_logging(config.log_path, logging.DEBUG if args.debug else logging.INFO)

    store = QueueStore(config.cache.path)
    client = CloudClient(
        config.cloud.base_url, config.cloud.device_id, config.cloud.secret, config.cloud.timeout,
    )
    stop_event = threading.Event()

    def shutdown(signum, frame):
        LOGGER.info('收到信号 %s，准备退出', signum)
        stop_event.set()

    for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, shutdown)
            except (ValueError, OSError):
                pass

    workers = [UploadWorker(config, store, client, stop_event, once=args.once)]
    if not args.no_serial:
        workers.append(SerialWorker(config.serial, store, stop_event))

    LOGGER.info('网关启动 device=%s match=%s cloud=%s serial=%s',
                config.cloud.device_id, config.cloud.match_id,
                config.cloud.base_url,
                'off' if args.no_serial else config.serial.port)
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    LOGGER.info('网关退出，队列统计 %s', store.stats())
    store.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
