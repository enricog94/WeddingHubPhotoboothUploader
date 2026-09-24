"""Unit tests for LOCAL_FILE_CHANGED behavior."""

from pathlib import Path
from unittest.mock import Mock

from weddinghub_photobooth.api import WeddingHubApiClient
from weddinghub_photobooth.config import Config
from weddinghub_photobooth.db import Database
from weddinghub_photobooth.models import QueueStatus
from weddinghub_photobooth.uploader import UploadWorker


def test_local_file_changed_aborts_upload(
    temp_dir: Path,
    watch_dir: Path,
    create_sample_photo
):
    """
    Given an item in the queue that has a recorded SHA-256 and size.
    If the file is modified on disk before upload (e.g. size or SHA mismatch),
    Then the worker must mark it FAILED with LOCAL_FILE_CHANGED.
    The file_observation must be deleted so it can be re-discovered.
    Uploads must not be attempted.
    """
    db = Database(temp_dir / "test.db")
    photo_path = create_sample_photo("changed.jpg")
    
    # Enqueue a fake old SHA and old size
    item, _ = db.enqueue(
        local_path=str(photo_path.resolve()),
        filename=photo_path.name,
        sha256="old-sha-1234",
        file_size=10, # mismatched size
        created_at="2026-09-02T20:00:00Z"
    )
    
    # Fake observation
    db.update_observation(str(photo_path.resolve()), 10, 111111, "old-sha-1234")
    
    cfg = Config(
        api_base_url="https://example.com",
        device_token="dev-token",
        watch_directory=watch_dir,
        db_path=temp_dir / "test.db"
    )
    
    worker = UploadWorker(config=cfg, db=db)
    
    client = Mock(spec=WeddingHubApiClient)
    
    worker.process_item(item, client)
    
    # 1. API was NOT called
    client.init_upload.assert_not_called()
    client.verify_device.assert_not_called()
    
    # 2. Status is FAILED with LOCAL_FILE_CHANGED
    updated = db.get_by_id(item.id)
    assert updated.status == QueueStatus.FAILED
    assert "LOCAL_FILE_CHANGED" in updated.last_error
    
    # 3. Observation is deleted
    stat = photo_path.resolve().stat()
    assert not db.is_known_unchanged(str(photo_path.resolve()), stat.st_size, stat.st_mtime_ns)
