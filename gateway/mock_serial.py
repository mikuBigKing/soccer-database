"""模拟器：无 ESP32 时按时间线灌报文。

用法（仓库根目录）：
    python gateway/mock_serial.py --stdout            # 打印报文，手工复制给串口工具
    python gateway/mock_serial.py --port COM4         # 写入真实/虚拟串口（com0com 可用）
    python gateway/mock_serial.py --direct --no-wait  # 直接写入队列，只验证云端上传
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.serial_reader import STAGE_MAP, build_line  # noqa: E402

# (stage, home, away, clock)
TIMELINE = [
    ('NS', 0, 0, 0),
    ('1H', 0, 0, 1),
    ('1H', 1, 0, 12),
    ('1H', 1, 1, 28),
    ('HT', 1, 1, 45),
    ('2H', 1, 1, 46),
    ('2H', 2, 1, 67),
    ('2H', 2, 2, 80),
    ('FT', 2, 2, 90),
]


def iter_frames(device: str, seq_start: int, interval: float):
    seq = seq_start
    for stage, home, away, clock in TIMELINE:
        payload = {
            'v': 1,
            'dev': device,
            'seq': seq,
            'h': home,
            'a': away,
            'st': stage,
            'clk': clock,
            'ts': int(time.time()),
        }
        yield payload
        seq += 1
        time.sleep(interval)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='计分板报文模拟器')
    parser.add_argument('--port', default=None, help='串口名，如 COM4')
    parser.add_argument('--baudrate', type=int, default=115200)
    parser.add_argument('--stdout', action='store_true', help='只打印报文')
    parser.add_argument('--direct', action='store_true', help='直接写入网关队列')
    parser.add_argument('--config', default='gateway/config.yaml')
    parser.add_argument('--dev', default='GW-SMOKE')
    parser.add_argument('--seq-start', type=int, default=1)
    parser.add_argument('--interval', type=float, default=1.0)
    parser.add_argument('--no-wait', action='store_true', help='不按时间线等待')
    args = parser.parse_args(argv)

    interval = 0 if args.no_wait else args.interval
    handle = None
    store = None

    if args.direct:
        from gateway.config import load_config
        from gateway.queue_store import QueueStore

        config = load_config(args.config)
        store = QueueStore(config.cache.path)
    elif not args.stdout:
        import serial  # pyserial

        if not args.port:
            print('需要 --port 或 --stdout 或 --direct', file=sys.stderr)
            return 2
        handle = serial.Serial(args.port, args.baudrate, timeout=1)

    try:
        for payload in iter_frames(args.dev, args.seq_start, interval):
            line = build_line(payload)
            if store is not None:
                if store.enqueue(payload['seq'], {
                    'seq': payload['seq'],
                    'home_score': payload['h'],
                    'away_score': payload['a'],
                    'stage': STAGE_MAP[payload['st']],
                    'clock_minute': payload['clk'],
                }):
                    print('入队 seq=%s %s %s-%s' % (payload['seq'], payload['st'], payload['h'], payload['a']))
                else:
                    print('重复 seq=%s 已跳过' % payload['seq'])
            elif handle is not None:
                handle.write(line)
                print('已发送 seq=%s %s %s-%s' % (payload['seq'], payload['st'], payload['h'], payload['a']))
            else:
                print(line.decode('utf-8').rstrip())
    finally:
        if handle is not None:
            handle.close()
        if store is not None:
            store.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
