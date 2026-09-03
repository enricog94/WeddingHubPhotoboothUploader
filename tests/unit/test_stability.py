"""Unit tests for directory watcher and file stability verification."""

import threading
import time
from pathlib import Path

from weddinghub_photobooth.db import Database
from weddinghub_photobooth.models import QueueStatus
from weddinghub_photobooth.watcher import PhotoWatcher


def test_unsupported_extensions_ignored(watch_dir: Path, db: Database):
    watcher = PhotoWatcher(watch_dir, db, stability_delay=0.05, scan_interval=0.1)

    # Create unsupported files
    (watch_dir / "test.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (watch_dir / "notes.txt").write_text("not a photo")
    (watch_dir / "photo.raw").write_bytes(b"raw data")

    watcher.scan_directory()
    time.sleep(0.1)

    stats = db.get_queue_stats()
    assert stats["total"] == 0


def test_stable_photo_queued(watch_dir: Path, db: Database, sample_jpeg_bytes: bytes):
    watcher = PhotoWatcher(watch_dir, db, stability_delay=0.05, scan_interval=0.1)

    photo_path = watch_dir / "completed_photo.jpg"
    photo_path.write_bytes(sample_jpeg_bytes)

    watcher.scan_directory()

    # Wait briefly for background stability worker to finish
    max_wait = 2.0
    start = time.time()
    while time.time() - start < max_wait:
        stats = db.get_queue_stats()
        if stats["total"] > 0:
            break
        time.sleep(0.05)

    item = db.fetch_next_pending()
    assert item is not None
    assert item.filename == "completed_photo.jpg"
    assert item.status == QueueStatus.PENDING
    assert item.file_size == len(sample_jpeg_bytes)


def test_growing_file_not_queued_prematurely(
    watch_dir: Path,
    db: Database,
    sample_jpeg_bytes: bytes,
):
    watcher = PhotoWatcher(watch_dir, db, stability_delay=0.1, scan_interval=0.2)

    photo_path = watch_dir / "growing.jpg"

    # Start by writing only half of the JPEG bytes (incomplete)
    photo_path.write_bytes(sample_jpeg_bytes[: len(sample_jpeg_bytes) // 2])

    watcher.handle_photo_candidate(photo_path)

    # Simulate ongoing camera/renderer writing by appending chunks every 0.05s
    def slow_writer():
        for i in range(3):
            time.sleep(0.04)
            with open(photo_path, "ab") as f:
                f.write(b"interim-data-chunk")

        # Finally write the complete valid JPEG
        time.sleep(0.05)
        photo_path.write_bytes(sample_jpeg_bytes)

    writer_thread = threading.Thread(target=slow_writer)
    writer_thread.start()
    writer_thread.join()

    # Wait for stability check to complete on the now-complete file
    max_wait = 3.0
    start = time.time()
    while time.time() - start < max_wait:
        if db.get_queue_stats()["total"] > 0:
            break
        time.sleep(0.05)

    item = db.fetch_next_pending()
    assert item is not None
    assert item.filename == "growing.jpg"
    assert item.file_size == len(sample_jpeg_bytes)
