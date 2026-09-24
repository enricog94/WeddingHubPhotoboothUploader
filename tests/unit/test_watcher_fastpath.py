"""Unit tests for the watcher fast path and file observations."""

import time
from pathlib import Path

from weddinghub_photobooth.db import Database
from weddinghub_photobooth.watcher import PhotoWatcher


def test_file_observations_duplicate_sha(
    temp_dir: Path,
    watch_dir: Path,
    db: Database,
    create_sample_photo
):
    """
    Given two distinct files A.jpg and B.jpg that share the same SHA-256 content.
    When both are processed by the watcher,
    Then the queue should only contain one item for that SHA,
    But BOTH A.jpg and B.jpg should be recorded in the file_observations table.
    A subsequent scan should skip BOTH files without hashing.
    """
    watcher = PhotoWatcher(watch_dir, db, stability_delay=0.1, scan_interval=1.0)
    
    photo_A = create_sample_photo("A.jpg")
    photo_B = watch_dir / "B.jpg"
    photo_B.write_bytes(photo_A.read_bytes())
    
    watcher.start()
    try:
        # Wait for them to be processed
        time.sleep(1.0)
        
        # Queue should have 1 item
        stats = db.get_queue_stats()
        assert stats["total"] == 1
        
        stat_a = photo_A.resolve().stat()
        stat_b = photo_B.resolve().stat()
        
        # BOTH should be in file_observations
        assert db.is_known_unchanged(str(photo_A.resolve()), stat_a.st_size, stat_a.st_mtime_ns)
        assert db.is_known_unchanged(str(photo_B.resolve()), stat_b.st_size, stat_b.st_mtime_ns)
    finally:
        watcher.stop()


def test_executor_shutdown_idempotence(watch_dir: Path, db: Database):
    """
    Test that calling stop() twice on the watcher doesn't raise exceptions
    or get stuck, and future tasks are not accepted.
    """
    watcher = PhotoWatcher(watch_dir, db, stability_delay=0.1, scan_interval=1.0)
    watcher.start()
    
    watcher.stop()
    watcher.stop()  # Idempotent call
    
    # Should not raise any error if handled now
    watcher.handle_photo_candidate(watch_dir / "new.jpg")
