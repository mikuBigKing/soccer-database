"""本地队列（SQLite WAL）：断电不丢，按 seq 顺序补传。

状态流转：
  pending -> sent -> acked
                \\-> pending（可重试错误，带 next_at 退避）
                \\-> dead（永久失败，保留现场数据人工处理）

同一 seq 只落一行（ESP32 重复发包不会放大队列）。
连接跨线程共享，所有语句用进程内锁串行化（串口线程写、上传线程读）。
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

STATUS_PENDING = 'pending'
STATUS_SENT = 'sent'
STATUS_ACKED = 'acked'
STATUS_DEAD = 'dead'

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seq         INTEGER NOT NULL UNIQUE,
    payload     TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_at     REAL    NOT NULL DEFAULT 0,
    last_error  TEXT    NOT NULL DEFAULT '',
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_status_seq ON outbox(status, seq);
"""


class QueueStore:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, isolation_level=None, timeout=10, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def enqueue(self, seq: int, payload: dict, next_at: float = 0) -> bool:
        """入队；seq 已存在返回 False（重复帧直接忽略）。"""
        now = time.time()
        with self._lock:
            try:
                self._conn.execute(
                    'INSERT INTO outbox (seq, payload, status, attempts, next_at, created_at, updated_at)'
                    ' VALUES (?, ?, ?, 0, ?, ?, ?)',
                    (
                        int(seq),
                        json.dumps(payload, separators=(',', ':'), ensure_ascii=False),
                        STATUS_PENDING,
                        float(next_at),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def next_item(self):
        """取下一条待上传（pending/sent 中 seq 最小者），保证云端看到的 seq 单调。"""
        with self._lock:
            row = self._conn.execute(
                'SELECT * FROM outbox WHERE status IN (?, ?) ORDER BY seq ASC LIMIT 1',
                (STATUS_PENDING, STATUS_SENT),
            ).fetchone()
            if row is None:
                return None
            item = dict(row)
        item['payload'] = json.loads(item['payload'])
        return item

    def mark_sent(self, item_id: int) -> int:
        now = time.time()
        with self._lock:
            self._conn.execute(
                'UPDATE outbox SET status = ?, attempts = attempts + 1, updated_at = ? WHERE id = ?',
                (STATUS_SENT, now, int(item_id)),
            )
            row = self._conn.execute('SELECT attempts FROM outbox WHERE id = ?', (int(item_id),)).fetchone()
            return int(row['attempts']) if row else 0

    def mark_acked(self, item_id: int, note: str = '') -> None:
        with self._lock:
            self._conn.execute(
                'UPDATE outbox SET status = ?, last_error = ?, updated_at = ? WHERE id = ?',
                (STATUS_ACKED, note[:200], time.time(), int(item_id)),
            )

    def mark_dead(self, item_id: int, reason: str) -> None:
        with self._lock:
            self._conn.execute(
                'UPDATE outbox SET status = ?, last_error = ?, updated_at = ? WHERE id = ?',
                (STATUS_DEAD, reason[:200], time.time(), int(item_id)),
            )

    def reschedule(self, item_id: int, next_at: float, error: str) -> None:
        with self._lock:
            self._conn.execute(
                'UPDATE outbox SET status = ?, next_at = ?, last_error = ?, updated_at = ? WHERE id = ?',
                (STATUS_PENDING, float(next_at), error[:200], time.time(), int(item_id)),
            )

    def stats(self) -> dict:
        with self._lock:
            counts = {status: 0 for status in (STATUS_PENDING, STATUS_SENT, STATUS_ACKED, STATUS_DEAD)}
            for row in self._conn.execute('SELECT status, COUNT(*) AS n FROM outbox GROUP BY status'):
                counts[row['status']] = int(row['n'])
            row = self._conn.execute('SELECT MAX(seq) AS seq FROM outbox').fetchone()
            counts['max_seq'] = int(row['seq'] or 0) if row else 0
            return counts

    def purge_acked(self, keep_days: int = 7) -> int:
        cutoff = time.time() - keep_days * 86400
        with self._lock:
            cursor = self._conn.execute(
                'DELETE FROM outbox WHERE status = ? AND updated_at < ?',
                (STATUS_ACKED, cutoff),
            )
            return cursor.rowcount or 0
