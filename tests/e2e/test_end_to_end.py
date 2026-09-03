"""End-to-End integration tests for WeddingHub Photobooth Uploader.

Covers Sections 19, 20, 21 of Milestone 0.2.0:
1. Complete local end-to-end flow:
   Watcher -> File Stability -> SQLite Queue -> Init -> Presigned PUT -> Complete -> Media DB (approved, source=photobooth).
2. Offline resilience and retry:
   Backend offline -> Enqueued -> Transient error -> RETRY with backoff -> Backend online -> UPLOADED.
3. Crash recovery:
   Process killed in UPLOADING -> Startup recover_in_flight() -> RETRY -> Uploaded -> No duplicate records.
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from pathlib import Path

import pytest

from tests.support.weddinghub_reference_server import WeddingHubReferenceServer
from weddinghub_photobooth.config import Config
from weddinghub_photobooth.db import Database, QueueStatus
from weddinghub_photobooth.uploader import UploadWorker
from weddinghub_photobooth.watcher import PhotoWatcher


def create_valid_jpeg(path: Path, content: bytes = b"valid_jpeg_sample_data") -> str:
    """Create a minimal valid JPEG with SOI/EOI markers."""
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + content + b"\xff\xd9"
    path.write_bytes(jpeg_bytes)
    return hashlib.sha256(jpeg_bytes).hexdigest()


@pytest.fixture
def test_server() -> Generator[WeddingHubReferenceServer, None, None]:
    server = WeddingHubReferenceServer()
    server.start()
    yield server
    server.stop()


def test_end_to_end_full_flow(temp_dir: Path, test_server: WeddingHubReferenceServer):
    """Section 19: Full local E2E test.
    - Photo written to watched folder
    - Watcher discovers and enqueues it after stability check
    - Worker uploads photo through /init, PUT binary, /complete
    - Queue item marked UPLOADED with remote_media_id
    - Media record in WeddingHub verified (source=photobooth, status=approved)
    - Original file on disk untouched
    """
    wedding = test_server.state.create_wedding(
        slug="serena-enrico-2027",
        bride_name="Serena",
        groom_name="Enrico",
        auto_approve=True,
    )
    raw_token = "token_e2e_device_alpha"
    test_server.state.register_device(wedding["id"], "Booth Station 1", raw_token)

    photos_dir = temp_dir / "photos"
    photos_dir.mkdir()
    db_path = temp_dir / "queue.db"

    config = Config(
        api_base_url=test_server.base_url,
        device_token=raw_token,
        watch_directory=photos_dir,
        db_path=db_path,
        stability_delay=0.1,
        scan_interval=0.1,
        upload_timeout=5.0,
    )

    db = Database(db_path)
    watcher = PhotoWatcher(
        watch_directory=photos_dir,
        db=db,
        stability_delay=0.1,
        scan_interval=0.1,
    )
    worker = UploadWorker(config=config, db=db)

    # 1. Create photo in watched directory
    img_path = photos_dir / "wedding_shot_001.jpg"
    img_sha = create_valid_jpeg(img_path, b"e2e_photo_content_12345")
    initial_bytes = img_path.read_bytes()
    initial_mtime = img_path.stat().st_mtime

    # 2. Watcher detects and stabilizes photo
    watcher._verify_and_enqueue(img_path)

    # Verify item is enqueued as PENDING
    items = db.list_queue(limit=10)
    assert len(items) == 1
    assert items[0].status == QueueStatus.PENDING
    assert items[0].sha256 == img_sha
    assert items[0].filename == "wedding_shot_001.jpg"

    # 3. Worker processes queue
    processed = worker.process_queue_once()
    assert processed == 1

    # 4. Verify item updated to UPLOADED
    items_after = db.list_queue(limit=10)
    assert len(items_after) == 1
    uploaded_item = items_after[0]
    assert uploaded_item.status == QueueStatus.UPLOADED
    assert uploaded_item.remote_media_id is not None
    assert int(uploaded_item.remote_media_id) > 0

    # 5. Verify WeddingHub database
    media_records = test_server.state.get_media_list(wedding["id"])
    assert len(media_records) == 1
    media = media_records[0]
    assert media["id"] == int(uploaded_item.remote_media_id)
    assert media["source"] == "photobooth"
    assert media["status"] == "approved"
    assert media["sha256"] == img_sha
    assert media["original_filename"] == "wedding_shot_001.jpg"
    assert "weddings/serena-enrico-2027/photobooth/" in media["original_key"]

    # 6. Verify binary in storage
    assert media["original_key"] in test_server.state.storage_objects
    assert test_server.state.storage_objects[media["original_key"]] == initial_bytes

    # 7. Verify original photo on local disk is 100% intact and untouched
    assert img_path.exists()
    assert img_path.read_bytes() == initial_bytes
    assert img_path.stat().st_mtime == initial_mtime


def test_offline_and_retry_flow(temp_dir: Path, test_server: WeddingHubReferenceServer):
    """Section 20: Offline & Retry test.
    - Backend is offline when photo is taken
    - Item is enqueued and fails with transient error -> transitions to RETRY
    - Original file remains on disk
    - Backend is brought back online
    - Worker retries and succeeds -> transitions to UPLOADED
    - File still exists and is untouched
    """
    wedding = test_server.state.create_wedding(
        slug="serena-enrico-2027",
        bride_name="Serena",
        groom_name="Enrico",
    )
    raw_token = "token_e2e_offline_test"
    test_server.state.register_device(wedding["id"], "Booth Station Offline", raw_token)

    photos_dir = temp_dir / "photos_offline"
    photos_dir.mkdir()
    db_path = temp_dir / "queue_offline.db"

    config = Config(
        api_base_url=test_server.base_url,
        device_token=raw_token,
        watch_directory=photos_dir,
        db_path=db_path,
        stability_delay=0.05,
        scan_interval=0.05,
        upload_timeout=2.0,
    )

    db = Database(db_path)
    worker = UploadWorker(config=config, db=db)

    # 1. Create photo
    img_path = photos_dir / "offline_shot.jpg"
    img_sha = create_valid_jpeg(img_path, b"offline_image_bytes")
    initial_bytes = img_path.read_bytes()

    _item, _ = db.enqueue(

        local_path=str(img_path),
        filename=img_path.name,
        sha256=img_sha,
        file_size=len(initial_bytes),
        created_at="2026-09-02T22:00:00Z",
    )

    # 2. Simulate backend outage (503 Service Unavailable)
    test_server.state.is_offline = True

    # Process queue while offline
    worker.process_queue_once()

    # Verify item transitioned to RETRY
    items = db.list_queue(limit=10)
    assert len(items) == 1
    retry_item = items[0]
    assert retry_item.status == QueueStatus.RETRY
    assert retry_item.attempt_count == 1
    assert "offline" in retry_item.last_error.lower()
    assert img_path.exists()
    assert img_path.read_bytes() == initial_bytes

    # 3. Restore backend online
    test_server.state.is_offline = False

    # Reset retry timer for immediate processing
    db.reset_retry_queue()

    # 4. Worker retries and succeeds
    processed = worker.process_queue_once()
    assert processed == 1

    # Verify item is now UPLOADED
    items_done = db.list_queue(limit=10)
    assert len(items_done) == 1
    assert items_done[0].status == QueueStatus.UPLOADED
    assert items_done[0].remote_media_id is not None

    # Verify media in backend
    media_records = test_server.state.get_media_list(wedding["id"])
    assert len(media_records) == 1
    assert media_records[0]["sha256"] == img_sha

    # Verify file is still intact
    assert img_path.exists()
    assert img_path.read_bytes() == initial_bytes


def test_crash_recovery_flow(temp_dir: Path, test_server: WeddingHubReferenceServer):
    """Section 21: Crash recovery test.
    - Process crashes while photo is in UPLOADING state
    - On restart, recover_in_flight() resets it to RETRY
    - Photo is reprocessed and completes upload
    - Zero duplicate media created in WeddingHub
    """
    wedding = test_server.state.create_wedding(
        slug="serena-enrico-2027",
        bride_name="Serena",
        groom_name="Enrico",
    )
    raw_token = "token_crash_recovery_test"
    test_server.state.register_device(wedding["id"], "Booth Station Crash", raw_token)

    photos_dir = temp_dir / "photos_crash"
    photos_dir.mkdir()
    db_path = temp_dir / "queue_crash.db"

    config = Config(
        api_base_url=test_server.base_url,
        device_token=raw_token,
        watch_directory=photos_dir,
        db_path=db_path,
    )

    db = Database(db_path)
    worker = UploadWorker(config=config, db=db)

    # 1. Enqueue photo
    img_path = photos_dir / "crash_shot.jpg"
    img_sha = create_valid_jpeg(img_path, b"crash_recovery_bytes")
    item, _ = db.enqueue(
        local_path=str(img_path),
        filename=img_path.name,
        sha256=img_sha,
        file_size=img_path.stat().st_size,
        created_at="2026-09-02T22:30:00Z",
    )

    # 2. Simulate abrupt crash during upload: item stuck in UPLOADING
    db.mark_uploading(item.id)
    items_crashed = db.list_queue(status_filter="UPLOADING")
    assert len(items_crashed) == 1
    assert items_crashed[0].status == QueueStatus.UPLOADING

    # 3. Simulate daemon restart: recover_in_flight() is called
    recovered_count = db.recover_in_flight()
    assert recovered_count == 1

    # Verify item moved to RETRY
    items_retrying = db.list_queue(limit=10)
    assert len(items_retrying) == 1
    assert items_retrying[0].status == QueueStatus.RETRY
    assert "recovered" in items_retrying[0].last_error.lower()

    # 4. Reset retry queue and execute worker cycle
    db.reset_retry_queue()
    processed = worker.process_queue_once()
    assert processed == 1

    # Verify item is now UPLOADED
    items_final = db.list_queue(limit=10)
    assert len(items_final) == 1
    assert items_final[0].status == QueueStatus.UPLOADED


    # Verify WeddingHub has exactly 1 media record
    media_records = test_server.state.get_media_list(wedding["id"])
    assert len(media_records) == 1
    assert media_records[0]["sha256"] == img_sha
