"""Persistent SQLite queue database management."""

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import QueueItem, QueueStatus


def utc_now_iso() -> str:
    """Return current UTC time in ISO8601 format."""
    return datetime.now(UTC).isoformat()


class Database:
    """Thread-safe SQLite database manager for the photo upload queue."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def init_db(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS upload_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE,
                    file_size INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    last_attempt_at TEXT,
                    uploaded_at TEXT,
                    remote_media_id TEXT,
                    last_error TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_upload_queue_status_next
                ON upload_queue (status, next_attempt_at);
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_upload_queue_sha256
                ON upload_queue (sha256);
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_observations (
                    local_path TEXT PRIMARY KEY,
                    observed_size INTEGER NOT NULL,
                    observed_mtime_ns INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def is_known_unchanged(self, local_path: str, size: int, mtime_ns: int) -> bool:
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM file_observations WHERE local_path = ? AND observed_size = ? AND observed_mtime_ns = ?",
                (local_path, size, mtime_ns),
            )
            return cursor.fetchone() is not None

    def update_observation(self, local_path: str, size: int, mtime_ns: int, sha256: str) -> None:
        last_seen = utc_now_iso()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO file_observations (local_path, observed_size, observed_mtime_ns, sha256, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(local_path) DO UPDATE SET
                    observed_size=excluded.observed_size,
                    observed_mtime_ns=excluded.observed_mtime_ns,
                    sha256=excluded.sha256,
                    last_seen_at=excluded.last_seen_at
                """,
                (local_path, size, mtime_ns, sha256, last_seen),
            )
            conn.commit()

    def delete_observation(self, local_path: str) -> None:
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM file_observations WHERE local_path = ?", (local_path,))
            conn.commit()

    def enqueue(
        self,
        local_path: str,
        filename: str,
        sha256: str,
        file_size: int,
        created_at: str,
        discovered_at: str | None = None,
    ) -> tuple[QueueItem, bool]:
        """Enqueue a photo if not already present.

        Returns (QueueItem, is_new). If already present, returns (existing_item, False).
        """
        disc_at = discovered_at or utc_now_iso()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM upload_queue WHERE sha256 = ?", (sha256,))
            row = cursor.fetchone()
            if row:
                return QueueItem.from_row(dict(row)), False

            cursor.execute(
                """
                INSERT INTO upload_queue (
                    local_path, filename, sha256, file_size, created_at,
                    discovered_at, status, attempt_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    local_path,
                    filename,
                    sha256,
                    file_size,
                    created_at,
                    disc_at,
                    QueueStatus.PENDING.value,
                ),
            )
            new_id = cursor.lastrowid
            conn.commit()

            cursor.execute("SELECT * FROM upload_queue WHERE id = ?", (new_id,))
            created_row = cursor.fetchone()
            return QueueItem.from_row(dict(created_row)), True

    def get_by_sha256(self, sha256: str) -> QueueItem | None:
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM upload_queue WHERE sha256 = ?", (sha256,))
            row = cursor.fetchone()
            return QueueItem.from_row(dict(row)) if row else None

    def get_by_id(self, item_id: int) -> QueueItem | None:
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM upload_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            return QueueItem.from_row(dict(row)) if row else None

    def fetch_next_pending(self, now_iso: str | None = None) -> QueueItem | None:
        """Fetch the next item ready for upload in FIFO order."""
        current_time = now_iso or utc_now_iso()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM upload_queue
                WHERE status IN ('PENDING', 'RETRY')
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY id ASC
                LIMIT 1
                """,
                (current_time,),
            )
            row = cursor.fetchone()
            return QueueItem.from_row(dict(row)) if row else None

    def mark_uploading(self, item_id: int) -> bool:
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE upload_queue
                SET status = ?
                WHERE id = ?
                """,
                (QueueStatus.UPLOADING.value, item_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_uploaded(
        self,
        item_id: int,
        remote_media_id: str,
        uploaded_at_iso: str | None = None,
    ) -> bool:
        ts = uploaded_at_iso or utc_now_iso()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE upload_queue
                SET status = ?,
                    uploaded_at = ?,
                    remote_media_id = ?,
                    last_error = NULL
                WHERE id = ?
                """,
                (QueueStatus.UPLOADED.value, ts, remote_media_id, item_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_retry(
        self,
        item_id: int,
        next_attempt_at_iso: str,
        error: str,
        last_attempt_at_iso: str | None = None,
    ) -> bool:
        ts = last_attempt_at_iso or utc_now_iso()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE upload_queue
                SET status = ?,
                    attempt_count = attempt_count + 1,
                    next_attempt_at = ?,
                    last_attempt_at = ?,
                    last_error = ?
                WHERE id = ?
                """,
                (QueueStatus.RETRY.value, next_attempt_at_iso, ts, error, item_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_failed(
        self,
        item_id: int,
        error: str,
        last_attempt_at_iso: str | None = None,
    ) -> bool:
        ts = last_attempt_at_iso or utc_now_iso()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE upload_queue
                SET status = ?,
                    attempt_count = attempt_count + 1,
                    last_attempt_at = ?,
                    last_error = ?
                WHERE id = ?
                """,
                (QueueStatus.FAILED.value, ts, error, item_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def recover_in_flight(self) -> int:
        """Reset items left in UPLOADING state due to process crash back to RETRY."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE upload_queue
                SET status = ?,
                    next_attempt_at = NULL,
                    last_error = 'Recovered from ungraceful shutdown during UPLOADING'
                WHERE status = ?
                """,
                (QueueStatus.RETRY.value, QueueStatus.UPLOADING.value),
            )
            conn.commit()
            return cursor.rowcount

    def reset_retry_queue(self) -> int:
        """Reset all RETRY and FAILED items to PENDING for immediate processing."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE upload_queue
                SET status = ?,
                    next_attempt_at = NULL
                WHERE status IN (?, ?)
                """,
                (
                    QueueStatus.PENDING.value,
                    QueueStatus.RETRY.value,
                    QueueStatus.FAILED.value,
                ),
            )
            conn.commit()
            return cursor.rowcount

    def get_queue_stats(self) -> dict[str, Any]:
        """Aggregate stats for CLI status reporting."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, count(*) FROM upload_queue GROUP BY status")
            counts = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute(
                "SELECT uploaded_at FROM upload_queue WHERE status = ? ORDER BY uploaded_at DESC LIMIT 1",
                (QueueStatus.UPLOADED.value,),
            )
            last_upload_row = cursor.fetchone()
            last_upload = last_upload_row[0] if last_upload_row else None

            cursor.execute(
                "SELECT last_error FROM upload_queue WHERE last_error IS NOT NULL ORDER BY id DESC LIMIT 1"
            )
            last_err_row = cursor.fetchone()
            last_error = last_err_row[0] if last_err_row else None

            cursor.execute("SELECT count(*) FROM upload_queue")
            total = cursor.fetchone()[0]

            return {
                "total": total,
                "pending": counts.get(QueueStatus.PENDING.value, 0),
                "uploading": counts.get(QueueStatus.UPLOADING.value, 0),
                "retry": counts.get(QueueStatus.RETRY.value, 0),
                "uploaded": counts.get(QueueStatus.UPLOADED.value, 0),
                "failed": counts.get(QueueStatus.FAILED.value, 0),
                "last_uploaded_at": last_upload,
                "last_error": last_error,
            }

    def list_queue(
        self,
        limit: int = 50,
        status_filter: str | None = None,
    ) -> list[QueueItem]:
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            if status_filter:
                cursor.execute(
                    "SELECT * FROM upload_queue WHERE status = ? ORDER BY id ASC LIMIT ?",
                    (status_filter.upper(), limit),
                )
            else:
                cursor.execute(
                    "SELECT * FROM upload_queue ORDER BY id ASC LIMIT ?",
                    (limit,),
                )
            rows = cursor.fetchall()
            return [QueueItem.from_row(dict(r)) for r in rows]
