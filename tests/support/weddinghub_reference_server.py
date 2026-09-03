"""WeddingHub Reference Server / Test Harness.

This module provides an in-process HTTP reference server simulating the
WeddingHub backend for local contract tests, E2E testing, offline simulation,
crash recovery, and multi-wedding isolation testing.

NOTE: This is a TEST HARNESS and reference implementation used strictly
for automated client test suites. It does not replace the production
Cloudflare Worker + PostgreSQL/Supabase backend.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


class WeddingHubServerState:
    """In-memory database and storage state for the reference test server."""

    def __init__(self) -> None:
        self.weddings: dict[int, dict[str, Any]] = {}
        self.devices: dict[str, dict[str, Any]] = {}  # token_hash -> device dict
        self.upload_sessions: dict[str, dict[str, Any]] = {}
        self.media_records: dict[int, dict[str, Any]] = {}
        self.storage_objects: dict[str, bytes] = {}
        self.storage_status_overrides: dict[str, int] = {}
        self.is_offline: bool = False
        self._lock = threading.Lock()

    def create_wedding(
        self,
        slug: str,
        bride_name: str,
        groom_name: str,
        auto_approve: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            w_id = len(self.weddings) + 1
            wedding = {
                "id": w_id,
                "slug": slug,
                "bride_name": bride_name,
                "groom_name": groom_name,
                "display_name": f"{bride_name} & {groom_name}".strip(" &"),
                "auto_approve": auto_approve,
                "photobooth_auto_approve": auto_approve,
            }
            self.weddings[w_id] = wedding
            return wedding

    def register_device(
        self,
        wedding_id: int,
        name: str,
        raw_token: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            d_id = len(self.devices) + 1
            device = {
                "id": d_id,
                "wedding_id": wedding_id,
                "name": name,
                "token_hash": token_hash,
                "enabled": enabled,
                "created_at": datetime.now(UTC).isoformat(),
                "last_seen_at": None,
            }
            self.devices[token_hash] = device
            return device

    def get_device_by_token(self, raw_token: str) -> dict[str, Any] | None:
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        return self.devices.get(token_hash)

    def set_device_enabled(self, device_id: int, enabled: bool) -> None:
        with self._lock:
            for dev in self.devices.values():
                if dev["id"] == device_id:
                    dev["enabled"] = enabled
                    return

    def get_media_list(self, wedding_id: int) -> list[dict[str, Any]]:
        with self._lock:
            return [m for m in self.media_records.values() if m["wedding_id"] == wedding_id]

    def expire_upload_session(self, upload_id: str) -> None:
        with self._lock:
            if upload_id in self.upload_sessions:
                self.upload_sessions[upload_id]["expires_at"] = (
                    datetime.now(UTC) - timedelta(seconds=10)
                ).isoformat()
                self.upload_sessions[upload_id]["status"] = "expired"


class ReferenceServerHandler(BaseHTTPRequestHandler):
    """HTTP request handler implementing the WeddingHub API contract."""

    server: WeddingHubReferenceServer

    def _send_json(self, status_code: int, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _get_bearer_token(self) -> str | None:
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        return None

    def _authenticate_device(self) -> dict[str, Any] | None:
        raw_token = self._get_bearer_token()
        if not raw_token:
            self._send_json(401, {"error": "Missing or invalid Authorization header"})
            return None

        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        device = self.server.state.devices.get(token_hash)
        if not device:
            self._send_json(401, {"error": "Invalid device token"})
            return None

        if not device.get("enabled", True):
            self._send_json(403, {"error": "Device is disabled"})
            return None

        device["last_seen_at"] = datetime.now(UTC).isoformat()
        return device

    def do_GET(self) -> None:
        if self.server.state.is_offline:
            self._send_json(503, {"error": "Service temporarily offline"})
            return

        if self.path == "/api/photobooth/verify":
            device = self._authenticate_device()
            if not device:
                return

            wedding = self.server.state.weddings.get(device["wedding_id"])
            if not wedding:
                self._send_json(404, {"error": "Associated wedding not found"})
                return

            self._send_json(
                200,
                {
                    "status": "ok",
                    "device": {
                        "name": device["name"],
                        "enabled": device["enabled"],
                        "wedding_slug": wedding["slug"],
                    },
                    "wedding": {
                        "slug": wedding["slug"],
                        "display_name": wedding["display_name"],
                    },
                },
            )
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.server.state.is_offline:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                self.rfile.read(content_length)
            self._send_json(503, {"error": "Service temporarily offline"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""

        if self.path == "/api/photobooth/upload/init":
            device = self._authenticate_device()
            if not device:
                return

            try:
                body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            except json.JSONDecodeError:
                self._send_json(400, {"error": "Invalid JSON"})
                return

            filename = body.get("filename")
            sha256_hash = body.get("sha256")
            size = body.get("size")
            content_type = body.get("content_type", "image/jpeg")

            if not filename or not isinstance(filename, str):
                self._send_json(400, {"error": "Missing or invalid filename"})
                return

            if not sha256_hash or not isinstance(sha256_hash, str) or not re.match(r"^[0-9a-fA-F]{64}$", sha256_hash):
                self._send_json(400, {"error": "Invalid sha256 hash"})
                return
            sha256_hash = sha256_hash.lower()

            if not isinstance(size, int) or size <= 0 or size > 50 * 1024 * 1024:
                self._send_json(400, {"error": "Size must be between 1 byte and 50MB"})
                return

            if content_type != "image/jpeg":
                self._send_json(400, {"error": "Only image/jpeg is allowed"})
                return

            wedding_id = device["wedding_id"]

            # Deduplication check: check if media already exists for this wedding
            for m in self.server.state.media_records.values():
                if m["wedding_id"] == wedding_id and m["sha256"] == sha256_hash:
                    self._send_json(200, {"status": "already_exists", "media_id": str(m["id"])})
                    return

            # Concurrency & retry check: reuse active pending session if one exists
            for s in self.server.state.upload_sessions.values():
                if (
                    s["device_id"] == device["id"]
                    and s["wedding_id"] == wedding_id
                    and s["sha256"] == sha256_hash
                    and s["status"] == "pending"
                    and datetime.fromisoformat(s["expires_at"]) > datetime.now(UTC)
                ):
                    host, port = self.server.server_address
                    upload_url = f"http://{host}:{port}/storage/{s['id']}"
                    self._send_json(
                        200,
                        {
                            "status": "upload_required",
                            "upload_id": s["id"],
                            "upload_url": upload_url,
                            "headers": {},
                            "expires_at": s["expires_at"],
                        },
                    )
                    return

            upload_id = str(uuid.uuid4())
            wedding = self.server.state.weddings[wedding_id]
            storage_key = f"weddings/{wedding['slug']}/photobooth/originals/{upload_id}.jpg"
            expires_at = datetime.now(UTC) + timedelta(minutes=15)

            session = {
                "id": upload_id,
                "device_id": device["id"],
                "wedding_id": wedding_id,
                "sha256": sha256_hash,
                "filename": filename,
                "size_bytes": size,
                "content_type": content_type,
                "storage_key": storage_key,
                "status": "pending",
                "created_at": datetime.now(UTC).isoformat(),
                "expires_at": expires_at.isoformat(),
                "completed_at": None,
                "media_id": None,
            }
            self.server.state.upload_sessions[upload_id] = session

            host, port = self.server.server_address
            upload_url = f"http://{host}:{port}/storage/{upload_id}"

            self._send_json(
                200,
                {
                    "status": "upload_required",
                    "upload_id": upload_id,
                    "upload_url": upload_url,
                    "headers": {},
                    "expires_at": expires_at.isoformat(),
                },
            )
            return

        elif self.path == "/api/photobooth/upload/complete":
            device = self._authenticate_device()
            if not device:
                return

            try:
                body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            except json.JSONDecodeError:
                self._send_json(400, {"error": "Invalid JSON"})
                return

            upload_id = body.get("upload_id")
            sha256_hash = body.get("sha256")

            if not upload_id or not sha256_hash:
                self._send_json(400, {"error": "Missing upload_id or sha256"})
                return

            session = self.server.state.upload_sessions.get(upload_id)
            if not session:
                self._send_json(404, {"error": "Upload session not found"})
                return

            # Multi-wedding and device ownership isolation check
            if session["wedding_id"] != device["wedding_id"] or session["device_id"] != device["id"]:
                self._send_json(403, {"error": "Forbidden: cross-device or cross-wedding session access"})
                return

            # Idempotency: if session is already completed, return existing media_id
            if session["status"] == "completed":
                self._send_json(200, {"status": "completed", "media_id": str(session["media_id"])})
                return

            expires_at = datetime.fromisoformat(session["expires_at"])
            if session["status"] == "expired" or datetime.now(UTC) > expires_at:
                session["status"] = "expired"
                self._send_json(410, {"error": "Upload session expired"})
                return

            if session["status"] != "pending":
                self._send_json(400, {"error": f"Session status is {session['status']}"})
                return


            if session["sha256"].lower() != sha256_hash.lower():
                self._send_json(400, {"error": "SHA256 mismatch"})
                return

            if session["storage_key"] not in self.server.state.storage_objects:
                self._send_json(400, {"error": "Binary file has not been uploaded to storage"})
                return

            storage_obj = self.server.state.storage_objects[session["storage_key"]]
            if len(storage_obj) != session["size_bytes"]:
                self._send_json(
                    400,
                    {
                        "error": f"Uploaded object size mismatch: expected {session['size_bytes']} bytes, found {len(storage_obj)} bytes"
                    },
                )
                return

            wedding = self.server.state.weddings[device["wedding_id"]]
            auto_approve = wedding.get("photobooth_auto_approve", True)
            media_status = "approved" if auto_approve else "pending"

            # Emulate PostgreSQL ON CONFLICT (wedding_id, sha256) WHERE source = 'photobooth' DO NOTHING
            existing_media = None
            for m in self.server.state.media_records.values():
                if m["wedding_id"] == device["wedding_id"] and m["sha256"].lower() == session["sha256"].lower():
                    existing_media = m
                    break

            if existing_media:
                media_id = existing_media["id"]
            else:
                media_id = len(self.server.state.media_records) + 1
                media_record = {
                    "id": media_id,
                    "uuid": str(uuid.uuid4()),
                    "wedding_id": device["wedding_id"],
                    "source": "photobooth",
                    "original_filename": session["filename"],
                    "original_key": session["storage_key"],
                    "sha256": session["sha256"],
                    "size_bytes": session["size_bytes"],
                    "mime_type": session["content_type"],
                    "status": media_status,
                    "created_at": datetime.now(UTC).isoformat(),
                    "uploaded_at": datetime.now(UTC).isoformat(),
                }
                self.server.state.media_records[media_id] = media_record

            session["status"] = "completed"
            session["completed_at"] = datetime.now(UTC).isoformat()
            session["media_id"] = media_id

            self._send_json(
                200,
                {
                    "status": "completed",
                    "media_id": str(media_id),
                },
            )
            return

        self._send_json(404, {"error": "Not found"})

    def do_PUT(self) -> None:
        if self.server.state.is_offline:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                self.rfile.read(content_length)
            self.send_response(503)
            self.end_headers()
            return

        match = re.match(r"^/storage/([^/]+)$", self.path)
        if not match:
            self.send_response(404)
            self.end_headers()
            return

        upload_id = match.group(1)
        session = self.server.state.upload_sessions.get(upload_id)
        if not session:
            self.send_response(404)
            self.end_headers()
            return

        if upload_id in self.server.state.storage_status_overrides:
            override_status = self.server.state.storage_status_overrides.pop(upload_id)
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                self.rfile.read(content_length)
            self.send_response(override_status)
            self.end_headers()
            return

        expires_at = datetime.fromisoformat(session["expires_at"])
        if session["status"] == "expired" or datetime.now(UTC) > expires_at:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                self.rfile.read(content_length)
            self.send_response(403)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_response(400)
            self.end_headers()
            return

        body = self.rfile.read(content_length)
        self.server.state.storage_objects[session["storage_key"]] = body

        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        # Silence default stderr request logging in test outputs
        pass


class WeddingHubReferenceServer(HTTPServer):
    """Threaded HTTP Reference Server simulating WeddingHub backend."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        super().__init__((host, port), ReferenceServerHandler)
        self.state = WeddingHubServerState()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    @property
    def base_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"
