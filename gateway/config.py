"""网关配置：读取 config.yaml，环境变量可覆盖（便于现场灌装）。"""
import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class CloudConfig:
    base_url: str
    device_id: str
    secret: str
    match_id: int
    timeout: int = 10


@dataclass
class SerialConfig:
    port: str = 'COM3'
    baudrate: int = 115200
    timeout: float = 1.0


@dataclass
class CacheConfig:
    path: str = 'gateway/data/queue.sqlite3'
    retry_base_ms: int = 1000
    retry_max_ms: int = 60000


@dataclass
class AppConfig:
    cloud: CloudConfig
    serial: SerialConfig
    cache: CacheConfig
    log_path: str = 'gateway/logs/gateway.log'


def load_config(path: str = None) -> AppConfig:
    path = path or os.getenv('GATEWAY_CONFIG', 'gateway/config.yaml')
    with open(path, 'r', encoding='utf-8') as handle:
        raw = yaml.safe_load(handle) or {}

    cloud = raw.get('cloud') or {}
    serial = raw.get('serial') or {}
    cache = raw.get('cache') or {}

    device_id = os.getenv('DEVICE_ID') or cloud.get('device_id', '')
    secret = os.getenv('DEVICE_SECRET') or cloud.get('secret', '')
    if not device_id or not secret:
        raise ValueError('config.yaml 缺少 cloud.device_id / cloud.secret')

    base_url = os.getenv('CLOUD_BASE_URL') or cloud.get('base_url', 'http://127.0.0.1:8000')
    match_id = int(os.getenv('MATCH_ID') or cloud.get('match_id') or 0)

    return AppConfig(
        cloud=CloudConfig(
            base_url=base_url.rstrip('/'),
            device_id=device_id,
            secret=secret,
            match_id=match_id,
            timeout=int(cloud.get('timeout', 10)),
        ),
        serial=SerialConfig(
            port=os.getenv('SERIAL_PORT') or serial.get('port', 'COM3'),
            baudrate=int(serial.get('baudrate', 115200)),
            timeout=float(serial.get('timeout', 1.0)),
        ),
        cache=CacheConfig(
            path=cache.get('path', 'gateway/data/queue.sqlite3'),
            retry_base_ms=int(cache.get('retry_base_ms', 1000)),
            retry_max_ms=int(cache.get('retry_max_ms', 60000)),
        ),
        log_path=raw.get('log_path', 'gateway/logs/gateway.log'),
    )


def ensure_dirs(config: AppConfig) -> None:
    Path(config.cache.path).parent.mkdir(parents=True, exist_ok=True)
    Path(config.log_path).parent.mkdir(parents=True, exist_ok=True)
