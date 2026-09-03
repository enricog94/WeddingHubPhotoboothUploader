"""Integration tests with a local mock HTTP server simulating WeddingHub."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

from weddinghub_photobooth.api import WeddingHubApiClient
from weddinghub_photobooth.config import Config
from weddinghub_photobooth.db import Database
from weddinghub_photobooth.models import QueueStatus
from weddinghub_photobooth.uploader import UploadWorker


class MockWeddingHubHandler(BaseHTTPRequestHandler):
    """Configurable mock HTTP server for testing the WeddingHub upload contract."""

    # Behavior control flags
    init_mode: str = "normal"  # "normal", "already_exists", "500", "429", "401", "400"
    upload_mode: str = "normal"  # "normal", "500", "429"
    complete_mode: str = "normal"  # "normal", "500"
    uploaded_files: ClassVar[list[bytes]] = []
    received_requests: ClassVar[list[dict[str, Any]]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress default server console output

    def address_string(self) -> str:
        return "127.0.0.1"

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8")) if body else {}

    def do_POST(self) -> None:
        self.close_connection = True
        auth = self.headers.get("Authorization", "")
        self.received_requests.append({"path": self.path, "auth": auth})
        _data = self._read_json()

        # Check authentication simulation
        if self.init_mode == "401":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Unauthorized: Invalid device token"}')
            return

        if self.path == "/api/photobooth/upload/init":

            if self.init_mode == "500":
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'{"error": "Internal Server Error"}')
                return

            if self.init_mode == "429":
                self.send_response(429)
                self.send_header("Retry-After", "45")
                self.end_headers()
                self.wfile.write(b'{"error": "Rate limit exceeded"}')
                return

            if self.init_mode == "400":
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error": "Bad request: invalid parameters"}')
                return

            if self.init_mode == "already_exists":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({
                        "status": "already_exists",
                        "media_id": "media-id-existing-999",
                    }).encode("utf-8")
                )
                return

            # Normal flow
            base_url = f"http://{self.server.server_address[0]}:{self.server.server_address[1]}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({
                    "status": "upload_required",
                    "upload_id": "session-upload-uuid-1234",
                    "upload_url": f"{base_url}/storage/upload/test-photo.jpg",
                    "headers": {"x-custom-upload": "true"},
                    "expires_at": "2027-01-01T00:00:00Z",
                }).encode("utf-8")
            )

        elif self.path == "/api/photobooth/upload/complete":
            if self.complete_mode == "500":
                self.send_response(500)
                self.end_headers()
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({
                    "status": "completed",
                    "media_id": "media-id-newly-uploaded-888",
                }).encode("utf-8")
            )

        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self) -> None:
        self.close_connection = True
        length = int(self.headers.get("Content-Length", 0))
        content = self.rfile.read(length)
        self.uploaded_files.append(content)

        if self.upload_mode == "500":
            self.send_response(500)
            self.end_headers()
            return

        self.send_response(200)
        self.end_headers()


def run_mock_server() -> tuple[HTTPServer, threading.Thread, str]:
    server = HTTPServer(("127.0.0.1", 0), MockWeddingHubHandler)
    host, port = server.server_address
    url = f"http://{host}:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, url


def test_full_successful_upload_flow(
    temp_dir: Path,
    watch_dir: Path,
    create_sample_photo,
    sample_jpeg_bytes: bytes,
):
    server, _thread, base_url = run_mock_server()
    MockWeddingHubHandler.init_mode = "normal"
    MockWeddingHubHandler.upload_mode = "normal"
    MockWeddingHubHandler.complete_mode = "normal"
    MockWeddingHubHandler.uploaded_files.clear()
    MockWeddingHubHandler.received_requests.clear()

    try:
        db = Database(temp_dir / "test.db")
        photo_path = create_sample_photo("wedding_photo_01.jpg")
        original_bytes = photo_path.read_bytes()
        original_mtime = photo_path.stat().st_mtime

        # Enqueue item
        item, _ = db.enqueue(
            local_path=str(photo_path),
            filename=photo_path.name,
            sha256="sha256-test-photo",
            file_size=len(sample_jpeg_bytes),
            created_at="2026-09-02T20:00:00Z",
        )

        cfg = Config(
            api_base_url=base_url,
            device_token="dev-token-abc",
            watch_directory=watch_dir,
            db_path=temp_dir / "test.db",
            upload_timeout=5.0,
        )

        worker = UploadWorker(config=cfg, db=db)
        client = WeddingHubApiClient(api_base_url=base_url, device_token="dev-token-abc")
        worker.process_item(item, client)

        # 1. Check database status is UPLOADED
        updated = db.get_by_id(item.id)
        assert updated.status == QueueStatus.UPLOADED
        assert updated.remote_media_id == "media-id-newly-uploaded-888"
        assert updated.uploaded_at is not None

        # 2. Check binary reached server
        assert len(MockWeddingHubHandler.uploaded_files) == 1
        assert MockWeddingHubHandler.uploaded_files[0] == sample_jpeg_bytes

        # 3. Check Authorization header was sent with device token
        init_req = next(r for r in MockWeddingHubHandler.received_requests if r["path"] == "/api/photobooth/upload/init")
        assert init_req["auth"] == "Bearer dev-token-abc"

        # 4. Strict requirement: original photo is NEVER modified, resized, or deleted
        assert photo_path.exists()
        assert photo_path.read_bytes() == original_bytes
        assert photo_path.stat().st_mtime == original_mtime
    finally:
        server.shutdown()


def test_idempotent_already_exists_flow(
    temp_dir: Path,
    watch_dir: Path,
    create_sample_photo,
):
    server, _thread, base_url = run_mock_server()
    MockWeddingHubHandler.init_mode = "already_exists"
    MockWeddingHubHandler.uploaded_files.clear()

    try:
        db = Database(temp_dir / "test.db")
        photo_path = create_sample_photo("wedding_photo_02.jpg")

        item, _ = db.enqueue(
            local_path=str(photo_path),
            filename=photo_path.name,
            sha256="sha256-existing-photo",
            file_size=100,
            created_at="2026-09-02T20:00:00Z",
        )

        cfg = Config(
            api_base_url=base_url,
            device_token="dev-token-abc",
            watch_directory=watch_dir,
            db_path=temp_dir / "test.db",
        )

        worker = UploadWorker(config=cfg, db=db)
        client = WeddingHubApiClient(api_base_url=base_url, device_token="dev-token-abc")
        worker.process_item(item, client)

        updated = db.get_by_id(item.id)
        assert updated.status == QueueStatus.UPLOADED
        assert updated.remote_media_id == "media-id-existing-999"
        # No binary upload occurred
        assert len(MockWeddingHubHandler.uploaded_files) == 0
    finally:
        server.shutdown()


def test_http_500_retry_behavior(
    temp_dir: Path,
    watch_dir: Path,
    create_sample_photo,
):
    server, _thread, base_url = run_mock_server()
    MockWeddingHubHandler.init_mode = "500"

    try:
        db = Database(temp_dir / "test.db")
        photo_path = create_sample_photo("photo_500.jpg")

        item, _ = db.enqueue(
            local_path=str(photo_path),
            filename=photo_path.name,
            sha256="sha256-500",
            file_size=100,
            created_at="2026-09-02T20:00:00Z",
        )

        cfg = Config(
            api_base_url=base_url,
            device_token="dev-token-abc",
            watch_directory=watch_dir,
            db_path=temp_dir / "test.db",
            retry_initial_delay=10.0,
            retry_max_delay=60.0,
            retry_jitter_factor=0.0,
        )

        worker = UploadWorker(config=cfg, db=db)
        client = WeddingHubApiClient(api_base_url=base_url, device_token="dev-token-abc")
        worker.process_item(item, client)

        updated = db.get_by_id(item.id)
        assert updated.status == QueueStatus.RETRY
        assert updated.attempt_count == 1
        assert updated.next_attempt_at is not None
        assert "500" in updated.last_error
    finally:
        server.shutdown()


def test_http_429_retry_after(
    temp_dir: Path,
    watch_dir: Path,
    create_sample_photo,
):
    server, _thread, base_url = run_mock_server()
    MockWeddingHubHandler.init_mode = "429"

    try:
        db = Database(temp_dir / "test.db")
        photo_path = create_sample_photo("photo_429.jpg")

        item, _ = db.enqueue(
            local_path=str(photo_path),
            filename=photo_path.name,
            sha256="sha256-429",
            file_size=100,
            created_at="2026-09-02T20:00:00Z",
        )

        cfg = Config(
            api_base_url=base_url,
            device_token="dev-token-abc",
            watch_directory=watch_dir,
            db_path=temp_dir / "test.db",
        )

        worker = UploadWorker(config=cfg, db=db)
        client = WeddingHubApiClient(api_base_url=base_url, device_token="dev-token-abc")
        worker.process_item(item, client)

        updated = db.get_by_id(item.id)
        assert updated.status == QueueStatus.RETRY
        assert "429" in updated.last_error
        assert updated.next_attempt_at is not None
    finally:
        server.shutdown()


def test_http_401_auth_error_handling(
    temp_dir: Path,
    watch_dir: Path,
    create_sample_photo,
):
    server, _thread, base_url = run_mock_server()
    MockWeddingHubHandler.init_mode = "401"

    try:
        db = Database(temp_dir / "test.db")
        photo_path = create_sample_photo("photo_401.jpg")

        item, _ = db.enqueue(
            local_path=str(photo_path),
            filename=photo_path.name,
            sha256="sha256-401",
            file_size=100,
            created_at="2026-09-02T20:00:00Z",
        )

        cfg = Config(
            api_base_url=base_url,
            device_token="bad-token",
            watch_directory=watch_dir,
            db_path=temp_dir / "test.db",
            auth_error_retry_delay=120.0,
        )

        worker = UploadWorker(config=cfg, db=db)
        client = WeddingHubApiClient(api_base_url=base_url, device_token="bad-token")
        worker.process_item(item, client)

        updated = db.get_by_id(item.id)
        assert updated.status == QueueStatus.RETRY
        assert "Authentication failure" in updated.last_error
        # Local photo must be retained
        assert photo_path.exists()
    finally:
        server.shutdown()


def test_permanent_400_marks_failed(
    temp_dir: Path,
    watch_dir: Path,
    create_sample_photo,
):
    server, _thread, base_url = run_mock_server()
    MockWeddingHubHandler.init_mode = "400"

    try:
        db = Database(temp_dir / "test.db")
        photo_path = create_sample_photo("photo_400.jpg")

        item, _ = db.enqueue(
            local_path=str(photo_path),
            filename=photo_path.name,
            sha256="sha256-400",
            file_size=100,
            created_at="2026-09-02T20:00:00Z",
        )

        cfg = Config(
            api_base_url=base_url,
            device_token="dev-token-abc",
            watch_directory=watch_dir,
            db_path=temp_dir / "test.db",
        )

        worker = UploadWorker(config=cfg, db=db)
        client = WeddingHubApiClient(api_base_url=base_url, device_token="dev-token-abc")
        worker.process_item(item, client)

        updated = db.get_by_id(item.id)
        assert updated.status == QueueStatus.FAILED
        assert "Bad request" in updated.last_error
        # Local photo must be retained
        assert photo_path.exists()
    finally:
        server.shutdown()


def test_network_timeout_retry(
    temp_dir: Path,
    watch_dir: Path,
    create_sample_photo,
):
    # Point to a non-routable blackhole IP to trigger timeout
    db = Database(temp_dir / "test.db")
    photo_path = create_sample_photo("photo_timeout.jpg")

    item, _ = db.enqueue(
        local_path=str(photo_path),
        filename=photo_path.name,
        sha256="sha256-timeout",
        file_size=100,
        created_at="2026-09-02T20:00:00Z",
    )

    cfg = Config(
        api_base_url="http://10.255.255.1",
        device_token="dev-token-abc",
        watch_directory=watch_dir,
        db_path=temp_dir / "test.db",
        upload_timeout=0.2,  # Very short timeout for fast test
    )

    worker = UploadWorker(config=cfg, db=db)
    client = WeddingHubApiClient(api_base_url="http://10.255.255.1", device_token="dev-token-abc", timeout=0.2)
    worker.process_item(item, client)

    updated = db.get_by_id(item.id)
    assert updated.status == QueueStatus.RETRY
    assert updated.attempt_count == 1
    assert "network" in updated.last_error.lower() or "timeout" in updated.last_error.lower()
