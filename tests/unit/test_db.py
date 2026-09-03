"""Unit tests for SQLite database queue management and crash recovery."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from weddinghub_photobooth.db import Database
from weddinghub_photobooth.models import QueueStatus


def test_enqueue_and_fetch(temp_dir: Path):
    db = Database(temp_dir / "test.db")
    item, is_new = db.enqueue(
        local_path="/photos/img1.jpg",
        filename="img1.jpg",
        sha256="abc123sha",
        file_size=1024,
        created_at="2026-09-02T20:00:00Z",
    )
    assert is_new is True
    assert item.status == QueueStatus.PENDING
    assert item.attempt_count == 0

    # Fetch next pending
    fetched = db.fetch_next_pending()
    assert fetched is not None
    assert fetched.id == item.id
    assert fetched.sha256 == "abc123sha"


def test_sha256_duplicate_detection(temp_dir: Path):
    db = Database(temp_dir / "test.db")

    item1, is_new1 = db.enqueue(
        local_path="/photos/photoA.jpg",
        filename="photoA.jpg",
        sha256="identical_sha256_hash",
        file_size=5000,
        created_at="2026-09-02T20:00:00Z",
    )
    assert is_new1 is True

    # Attempt to enqueue different filename but identical sha256
    item2, is_new2 = db.enqueue(
        local_path="/photos/photoB_copy.jpg",
        filename="photoB_copy.jpg",
        sha256="identical_sha256_hash",
        file_size=5000,
        created_at="2026-09-02T20:01:00Z",
    )
    assert is_new2 is False
    assert item2.id == item1.id
    assert item2.filename == "photoA.jpg"


def test_queue_survives_process_restart(temp_dir: Path):
    db_path = temp_dir / "persistent.db"
    db1 = Database(db_path)
    item, _ = db1.enqueue(
        local_path="/photos/persist.jpg",
        filename="persist.jpg",
        sha256="persist_sha",
        file_size=2048,
        created_at="2026-09-02T20:00:00Z",
    )

    # Re-open database instance (simulating app restart)
    db2 = Database(db_path)
    fetched = db2.get_by_id(item.id)
    assert fetched is not None
    assert fetched.sha256 == "persist_sha"
    assert fetched.status == QueueStatus.PENDING


def test_crash_recovery_from_uploading(temp_dir: Path):
    db = Database(temp_dir / "test.db")
    item, _ = db.enqueue(
        local_path="/photos/in_flight.jpg",
        filename="in_flight.jpg",
        sha256="in_flight_sha",
        file_size=4096,
        created_at="2026-09-02T20:00:00Z",
    )
    db.mark_uploading(item.id)

    # Verify status is UPLOADING
    current = db.get_by_id(item.id)
    assert current.status == QueueStatus.UPLOADING

    # Simulate startup recovery
    recovered_count = db.recover_in_flight()
    assert recovered_count == 1

    recovered_item = db.get_by_id(item.id)
    assert recovered_item.status == QueueStatus.RETRY
    assert "Recovered from ungraceful shutdown" in recovered_item.last_error
    assert recovered_item.next_attempt_at is None

    # Recovered item is immediately ready for next attempt
    ready_item = db.fetch_next_pending()
    assert ready_item is not None
    assert ready_item.id == item.id


def test_fetch_next_pending_respects_next_attempt_at(temp_dir: Path):
    db = Database(temp_dir / "test.db")
    now = datetime.now(UTC)

    # Future item (should NOT be ready)
    future_time = (now + timedelta(minutes=10)).isoformat()
    item_future, _ = db.enqueue(
        local_path="/p/future.jpg",
        filename="future.jpg",
        sha256="future_sha",
        file_size=100,
        created_at=now.isoformat(),
    )
    db.mark_retry(item_future.id, future_time, "Temporary error")

    assert db.fetch_next_pending(now.isoformat()) is None

    # Past item (SHOULD be ready)
    past_time = (now - timedelta(seconds=1)).isoformat()
    item_past, _ = db.enqueue(
        local_path="/p/past.jpg",
        filename="past.jpg",
        sha256="past_sha",
        file_size=100,
        created_at=now.isoformat(),
    )
    db.mark_retry(item_past.id, past_time, "Temporary error")

    ready = db.fetch_next_pending(now.isoformat())
    assert ready is not None
    assert ready.id == item_past.id


def test_reset_retry_queue(temp_dir: Path):
    db = Database(temp_dir / "test.db")
    item1, _ = db.enqueue(
        local_path="/p/1.jpg",
        filename="1.jpg",
        sha256="sha_1",
        file_size=100,
        created_at="2026-09-02T20:00:00Z",
    )
    db.mark_retry(item1.id, "2099-01-01T00:00:00Z", "failed once")

    item2, _ = db.enqueue(
        local_path="/p/2.jpg",
        filename="2.jpg",
        sha256="sha_2",
        file_size=100,
        created_at="2026-09-02T20:00:00Z",
    )
    db.mark_failed(item2.id, "fatal error")

    reset_count = db.reset_retry_queue()
    assert reset_count == 2

    assert db.get_by_id(item1.id).status == QueueStatus.PENDING
    assert db.get_by_id(item2.id).status == QueueStatus.PENDING


def test_queue_stats(temp_dir: Path):
    db = Database(temp_dir / "test.db")
    item1, _ = db.enqueue("/p/1.jpg", "1.jpg", "s1", 10, "2026-09-02T20:00:00Z")
    item2, _ = db.enqueue("/p/2.jpg", "2.jpg", "s2", 10, "2026-09-02T20:00:00Z")
    _item3, _ = db.enqueue("/p/3.jpg", "3.jpg", "s3", 10, "2026-09-02T20:00:00Z")

    db.mark_uploaded(item1.id, "media-123", "2026-09-02T20:05:00Z")
    db.mark_failed(item2.id, "some fatal error")

    stats = db.get_queue_stats()
    assert stats["total"] == 3
    assert stats["uploaded"] == 1
    assert stats["failed"] == 1
    assert stats["pending"] == 1
    assert stats["last_uploaded_at"] == "2026-09-02T20:05:00Z"
    assert stats["last_error"] == "some fatal error"
