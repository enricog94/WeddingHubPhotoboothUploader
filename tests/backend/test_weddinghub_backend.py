"""Comprehensive backend contract and security tests for WeddingHub.

Validates the full specification from Section 17 & 13 of Milestone 0.2.0:
- Authentication & Token hashing (SHA-256)
- Init endpoint & input validation (JPEG only, SHA-256, size limits)
- Storage PUT emulation
- Complete endpoint & verification
- Server-side deduplication (already_exists)
- Idempotency on complete
- Multi-wedding isolation and security boundaries
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator

import httpx
import pytest

from tests.support.weddinghub_reference_server import WeddingHubReferenceServer


@pytest.fixture
def server() -> Generator[WeddingHubReferenceServer, None, None]:
    srv = WeddingHubReferenceServer()
    srv.start()
    yield srv
    srv.stop()



@pytest.fixture
def wedding(server: WeddingHubReferenceServer) -> dict:
    return server.state.create_wedding(
        slug="serena-enrico-2027",
        bride_name="Serena",
        groom_name="Enrico",
        auto_approve=True,
    )


@pytest.fixture
def device_token() -> str:
    return "photobooth_dev_token_secret_12345"


@pytest.fixture
def device(server: WeddingHubReferenceServer, wedding: dict, device_token: str) -> dict:
    return server.state.register_device(
        wedding_id=wedding["id"],
        name="Station Alpha (Linux PC)",
        raw_token=device_token,
        enabled=True,
    )


def test_auth_valid_token(server: WeddingHubReferenceServer, device: dict, device_token: str):
    """GET /api/photobooth/verify with valid Bearer token returns 200 and device info."""
    url = f"{server.base_url}/api/photobooth/verify"
    headers = {"Authorization": f"Bearer {device_token}"}
    resp = httpx.get(url, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["device"]["name"] == "Station Alpha (Linux PC)"
    assert data["device"]["wedding_slug"] == "serena-enrico-2027"
    assert data["device"]["enabled"] is True
    assert data["wedding"]["slug"] == "serena-enrico-2027"
    assert data["wedding"]["display_name"] == "Serena & Enrico"


    # Check that last_seen_at was updated
    dev = server.state.get_device_by_token(device_token)
    assert dev is not None
    assert dev["last_seen_at"] is not None


def test_auth_nonexistent_token(server: WeddingHubReferenceServer):
    """GET /api/photobooth/verify with unknown token returns 401."""
    url = f"{server.base_url}/api/photobooth/verify"
    headers = {"Authorization": "Bearer non_existent_token_9999"}
    resp = httpx.get(url, headers=headers)
    assert resp.status_code == 401


def test_auth_missing_header(server: WeddingHubReferenceServer):
    """GET /api/photobooth/verify without Authorization header returns 401."""
    url = f"{server.base_url}/api/photobooth/verify"
    resp = httpx.get(url)
    assert resp.status_code == 401


def test_auth_invalid_header_format(server: WeddingHubReferenceServer, device_token: str):
    """GET /api/photobooth/verify with Basic or missing Bearer prefix returns 401."""
    url = f"{server.base_url}/api/photobooth/verify"
    resp = httpx.get(url, headers={"Authorization": f"Basic {device_token}"})
    assert resp.status_code == 401

    resp2 = httpx.get(url, headers={"Authorization": device_token})
    assert resp2.status_code == 401


def test_auth_disabled_device(server: WeddingHubReferenceServer, device: dict, device_token: str):
    """Disabled device returns 403 on upload endpoints."""
    server.state.set_device_enabled(device["id"], False)

    # Init should be forbidden
    init_url = f"{server.base_url}/api/photobooth/upload/init"
    headers = {"Authorization": f"Bearer {device_token}"}
    payload = {
        "filename": "test.jpg",
        "sha256": "a" * 64,
        "size": 1024,
        "content_type": "image/jpeg",
    }
    resp = httpx.post(init_url, json=payload, headers=headers)
    assert resp.status_code == 403

    # Complete should also be forbidden
    complete_url = f"{server.base_url}/api/photobooth/upload/complete"
    resp_comp = httpx.post(
        complete_url,
        json={"upload_id": "dummy", "sha256": "a" * 64},
        headers=headers,
    )
    assert resp_comp.status_code == 403


def test_init_valid(server: WeddingHubReferenceServer, device: dict, device_token: str):
    """Valid /init returns upload_required and presigned upload URL."""
    url = f"{server.base_url}/api/photobooth/upload/init"
    headers = {"Authorization": f"Bearer {device_token}"}
    dummy_sha = hashlib.sha256(b"image_content_1").hexdigest()
    payload = {
        "filename": "IMG_0001.JPG",
        "sha256": dummy_sha,
        "size": 1500000,
        "content_type": "image/jpeg",
    }
    resp = httpx.post(url, json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "upload_required"
    assert "upload_id" in data
    assert "upload_url" in data
    assert data["upload_url"].startswith("http")
    assert "expires_at" in data


def test_init_invalid_mime_type(server: WeddingHubReferenceServer, device: dict, device_token: str):
    """Non-JPEG MIME types (e.g., PNG, MP4, HEIC) are rejected with 400."""
    url = f"{server.base_url}/api/photobooth/upload/init"
    headers = {"Authorization": f"Bearer {device_token}"}

    for bad_mime in ["image/png", "image/heic", "video/mp4", "application/pdf"]:
        payload = {
            "filename": "test.png",
            "sha256": "b" * 64,
            "size": 1024,
            "content_type": bad_mime,
        }
        resp = httpx.post(url, json=payload, headers=headers)
        assert resp.status_code == 400, f"Expected 400 for {bad_mime}"


def test_init_invalid_sha256(server: WeddingHubReferenceServer, device: dict, device_token: str):
    """Invalid SHA256 hashes are rejected with 400."""
    url = f"{server.base_url}/api/photobooth/upload/init"
    headers = {"Authorization": f"Bearer {device_token}"}

    for bad_sha in ["short", "z" * 64, "1234"]:
        payload = {
            "filename": "test.jpg",
            "sha256": bad_sha,
            "size": 1024,
            "content_type": "image/jpeg",
        }
        resp = httpx.post(url, json=payload, headers=headers)
        assert resp.status_code == 400


def test_init_invalid_size(server: WeddingHubReferenceServer, device: dict, device_token: str):
    """Zero, negative, or excessive size (>50MB) are rejected with 400."""
    url = f"{server.base_url}/api/photobooth/upload/init"
    headers = {"Authorization": f"Bearer {device_token}"}

    for bad_size in [0, -100, 60 * 1024 * 1024]:
        payload = {
            "filename": "test.jpg",
            "sha256": "c" * 64,
            "size": bad_size,
            "content_type": "image/jpeg",
        }
        resp = httpx.post(url, json=payload, headers=headers)
        assert resp.status_code == 400


def test_init_missing_fields(server: WeddingHubReferenceServer, device: dict, device_token: str):
    """Missing required fields in payload returns 400."""
    url = f"{server.base_url}/api/photobooth/upload/init"
    headers = {"Authorization": f"Bearer {device_token}"}

    resp = httpx.post(url, json={"filename": "test.jpg"}, headers=headers)
    assert resp.status_code == 400


def test_complete_valid_flow(server: WeddingHubReferenceServer, device: dict, device_token: str, wedding: dict):
    """Full 3-step upload: init -> PUT binary -> complete -> media record created."""
    headers = {"Authorization": f"Bearer {device_token}"}
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 500 + b"\xff\xd9"
    img_sha = hashlib.sha256(fake_jpeg).hexdigest()

    # Step 1: Init
    init_resp = httpx.post(
        f"{server.base_url}/api/photobooth/upload/init",
        json={
            "filename": "booth_101.jpg",
            "sha256": img_sha,
            "size": len(fake_jpeg),
            "content_type": "image/jpeg",
        },
        headers=headers,
    )
    assert init_resp.status_code == 200
    init_data = init_resp.json()
    upload_id = init_data["upload_id"]
    upload_url = init_data["upload_url"]

    # Step 2: Binary PUT
    put_resp = httpx.put(upload_url, content=fake_jpeg, headers={"Content-Type": "image/jpeg"})
    assert put_resp.status_code in (200, 204)

    # Step 3: Complete
    comp_resp = httpx.post(
        f"{server.base_url}/api/photobooth/upload/complete",
        json={"upload_id": upload_id, "sha256": img_sha},
        headers=headers,
    )
    assert comp_resp.status_code == 200
    comp_data = comp_resp.json()
    assert comp_data["status"] == "completed"
    media_id = int(comp_data["media_id"])

    # Verify media record in WeddingHub DB
    media_list = server.state.get_media_list(wedding["id"])
    assert len(media_list) == 1
    media = media_list[0]
    assert media["id"] == media_id
    assert media["source"] == "photobooth"
    assert media["status"] == "approved"
    assert media["sha256"] == img_sha
    assert media["original_filename"] == "booth_101.jpg"
    assert media["size_bytes"] == len(fake_jpeg)
    assert "weddings/serena-enrico-2027/photobooth/" in media["original_key"]


def test_complete_idempotency(server: WeddingHubReferenceServer, device: dict, device_token: str, wedding: dict):
    """Calling /complete multiple times returns the same media_id without duplicating media."""
    headers = {"Authorization": f"Bearer {device_token}"}
    fake_jpeg = b"\xff\xd8" + b"\x01" * 100 + b"\xff\xd9"
    img_sha = hashlib.sha256(fake_jpeg).hexdigest()

    init_resp = httpx.post(
        f"{server.base_url}/api/photobooth/upload/init",
        json={"filename": "idem.jpg", "sha256": img_sha, "size": len(fake_jpeg), "content_type": "image/jpeg"},
        headers=headers,
    )
    upload_id = init_resp.json()["upload_id"]
    upload_url = init_resp.json()["upload_url"]
    httpx.put(upload_url, content=fake_jpeg)

    # First complete
    resp1 = httpx.post(
        f"{server.base_url}/api/photobooth/upload/complete",
        json={"upload_id": upload_id, "sha256": img_sha},
        headers=headers,
    )
    assert resp1.status_code == 200
    media_id_1 = resp1.json()["media_id"]

    # Second complete
    resp2 = httpx.post(
        f"{server.base_url}/api/photobooth/upload/complete",
        json={"upload_id": upload_id, "sha256": img_sha},
        headers=headers,
    )
    assert resp2.status_code == 200
    media_id_2 = resp2.json()["media_id"]

    assert media_id_1 == media_id_2
    assert len(server.state.get_media_list(wedding["id"])) == 1


def test_deduplication_already_exists(server: WeddingHubReferenceServer, device: dict, device_token: str, wedding: dict):
    """If photo with same SHA-256 already exists in wedding media, /init returns already_exists."""
    headers = {"Authorization": f"Bearer {device_token}"}
    fake_jpeg = b"\xff\xd8" + b"\x02" * 100 + b"\xff\xd9"
    img_sha = hashlib.sha256(fake_jpeg).hexdigest()

    # Upload first time
    init1 = httpx.post(
        f"{server.base_url}/api/photobooth/upload/init",
        json={"filename": "photo.jpg", "sha256": img_sha, "size": len(fake_jpeg), "content_type": "image/jpeg"},
        headers=headers,
    )
    u_id = init1.json()["upload_id"]
    httpx.put(init1.json()["upload_url"], content=fake_jpeg)
    comp1 = httpx.post(
        f"{server.base_url}/api/photobooth/upload/complete",
        json={"upload_id": u_id, "sha256": img_sha},
        headers=headers,
    )
    initial_media_id = comp1.json()["media_id"]

    # Attempt to upload same photo second time
    init2 = httpx.post(
        f"{server.base_url}/api/photobooth/upload/init",
        json={"filename": "photo_copy.jpg", "sha256": img_sha, "size": len(fake_jpeg), "content_type": "image/jpeg"},
        headers=headers,
    )
    assert init2.status_code == 200
    init2_data = init2.json()
    assert init2_data["status"] == "already_exists"
    assert init2_data["media_id"] == initial_media_id

    # Verify no new media record was created
    assert len(server.state.get_media_list(wedding["id"])) == 1


def test_complete_nonexistent_session(server: WeddingHubReferenceServer, device: dict, device_token: str):
    """Calling /complete with a nonexistent upload_id returns 404."""
    url = f"{server.base_url}/api/photobooth/upload/complete"
    headers = {"Authorization": f"Bearer {device_token}"}
    resp = httpx.post(url, json={"upload_id": "nonexistent-uuid-123", "sha256": "d" * 64}, headers=headers)
    assert resp.status_code == 404


def test_complete_expired_session(server: WeddingHubReferenceServer, device: dict, device_token: str):
    """Calling /complete on an expired upload session returns 410."""
    headers = {"Authorization": f"Bearer {device_token}"}
    fake_jpeg = b"\xff\xd8" + b"\x03" * 50 + b"\xff\xd9"
    img_sha = hashlib.sha256(fake_jpeg).hexdigest()

    init_resp = httpx.post(
        f"{server.base_url}/api/photobooth/upload/init",
        json={"filename": "exp.jpg", "sha256": img_sha, "size": len(fake_jpeg), "content_type": "image/jpeg"},
        headers=headers,
    )
    u_id = init_resp.json()["upload_id"]
    httpx.put(init_resp.json()["upload_url"], content=fake_jpeg)

    # Force expiration
    server.state.expire_upload_session(u_id)

    comp_resp = httpx.post(
        f"{server.base_url}/api/photobooth/upload/complete",
        json={"upload_id": u_id, "sha256": img_sha},
        headers=headers,
    )
    assert comp_resp.status_code == 410


def test_complete_sha_mismatch(server: WeddingHubReferenceServer, device: dict, device_token: str):
    """Calling /complete with different SHA-256 than initialized returns 400."""
    headers = {"Authorization": f"Bearer {device_token}"}
    fake_jpeg = b"\xff\xd8" + b"\x04" * 50 + b"\xff\xd9"
    img_sha = hashlib.sha256(fake_jpeg).hexdigest()

    init_resp = httpx.post(
        f"{server.base_url}/api/photobooth/upload/init",
        json={"filename": "mismatch.jpg", "sha256": img_sha, "size": len(fake_jpeg), "content_type": "image/jpeg"},
        headers=headers,
    )
    u_id = init_resp.json()["upload_id"]
    httpx.put(init_resp.json()["upload_url"], content=fake_jpeg)

    # Complete with different sha
    different_sha = hashlib.sha256(b"different").hexdigest()
    comp_resp = httpx.post(
        f"{server.base_url}/api/photobooth/upload/complete",
        json={"upload_id": u_id, "sha256": different_sha},
        headers=headers,
    )
    assert comp_resp.status_code == 400


def test_multi_wedding_cross_device_isolation(server: WeddingHubReferenceServer):
    """Strict multi-wedding isolation:
    - Wedding A & Device A cannot access or complete Wedding B's sessions (403).
    - Identical photo uploaded to both weddings creates separate records without collision.
    - Storage paths are cleanly isolated by wedding slug.
    """
    wedding_a = server.state.create_wedding("wedding-a", "Alice", "Bob")
    wedding_b = server.state.create_wedding("wedding-b", "Carla", "David")

    token_a = "token_photobooth_a"
    token_b = "token_photobooth_b"
    server.state.register_device(wedding_a["id"], "Device A", token_a)
    server.state.register_device(wedding_b["id"], "Device B", token_b)

    # Same photo bytes for both weddings
    shared_jpeg = b"\xff\xd8" + b"\xee" * 200 + b"\xff\xd9"
    shared_sha = hashlib.sha256(shared_jpeg).hexdigest()

    # Device A initializes upload
    headers_a = {"Authorization": f"Bearer {token_a}"}
    init_a = httpx.post(
        f"{server.base_url}/api/photobooth/upload/init",
        json={"filename": "shared.jpg", "sha256": shared_sha, "size": len(shared_jpeg), "content_type": "image/jpeg"},
        headers=headers_a,
    )
    assert init_a.status_code == 200
    upload_id_a = init_a.json()["upload_id"]
    httpx.put(init_a.json()["upload_url"], content=shared_jpeg)

    # Device B attempts to complete Device A's session -> Must be 403 Forbidden!
    headers_b = {"Authorization": f"Bearer {token_b}"}
    unauthorized_comp = httpx.post(
        f"{server.base_url}/api/photobooth/upload/complete",
        json={"upload_id": upload_id_a, "sha256": shared_sha},
        headers=headers_b,
    )
    assert unauthorized_comp.status_code == 403, "Device B must not complete Device A's session!"

    # Device A completes legitimately
    comp_a = httpx.post(
        f"{server.base_url}/api/photobooth/upload/complete",
        json={"upload_id": upload_id_a, "sha256": shared_sha},
        headers=headers_a,
    )
    assert comp_a.status_code == 200
    media_id_a = comp_a.json()["media_id"]

    # Now Device B uploads the same photo to Wedding B
    init_b = httpx.post(
        f"{server.base_url}/api/photobooth/upload/init",
        json={"filename": "shared.jpg", "sha256": shared_sha, "size": len(shared_jpeg), "content_type": "image/jpeg"},
        headers=headers_b,
    )
    # Because deduplication is per-wedding, Wedding B must require an upload!
    assert init_b.status_code == 200
    assert init_b.json()["status"] == "upload_required"
    upload_id_b = init_b.json()["upload_id"]
    httpx.put(init_b.json()["upload_url"], content=shared_jpeg)

    comp_b = httpx.post(
        f"{server.base_url}/api/photobooth/upload/complete",
        json={"upload_id": upload_id_b, "sha256": shared_sha},
        headers=headers_b,
    )
    assert comp_b.status_code == 200
    media_id_b = comp_b.json()["media_id"]

    assert media_id_a != media_id_b

    # Check database isolation
    media_list_a = server.state.get_media_list(wedding_a["id"])
    media_list_b = server.state.get_media_list(wedding_b["id"])
    assert len(media_list_a) == 1
    assert len(media_list_b) == 1

    assert "weddings/wedding-a/photobooth/" in media_list_a[0]["original_key"]
    assert "weddings/wedding-b/photobooth/" in media_list_b[0]["original_key"]


def test_complete_size_mismatch_rejected(server: WeddingHubReferenceServer, device: dict, device_token: str):
    """POST /complete rejects upload if R2 object size does not match declared session size."""
    headers = {"Authorization": f"Bearer {device_token}"}
    declared_size = 5000
    fake_sha = hashlib.sha256(b"content").hexdigest()

    init_res = httpx.post(
        f"{server.base_url}/api/photobooth/upload/init",
        json={"filename": "shot.jpg", "sha256": fake_sha, "size": declared_size, "content_type": "image/jpeg"},
        headers=headers,
    )
    assert init_res.status_code == 200
    upload_id = init_res.json()["upload_id"]
    upload_url = init_res.json()["upload_url"]

    # Upload only 4000 bytes instead of declared 5000 bytes
    httpx.put(upload_url, content=b"x" * 4000)

    complete_res = httpx.post(
        f"{server.base_url}/api/photobooth/upload/complete",
        json={"upload_id": upload_id, "sha256": fake_sha},
        headers=headers,
    )
    assert complete_res.status_code == 400
    assert "size mismatch" in complete_res.json()["error"].lower()

    session = server.state.upload_sessions[upload_id]
    assert session["status"] == "pending"


def test_init_reuse_active_pending_session(server: WeddingHubReferenceServer, device: dict, device_token: str):
    """POST /init reuses active pending session ID if called again before completion."""
    headers = {"Authorization": f"Bearer {device_token}"}
    photo_sha = hashlib.sha256(b"reuse_test").hexdigest()

    init_res_1 = httpx.post(
        f"{server.base_url}/api/photobooth/upload/init",
        json={"filename": "shot.jpg", "sha256": photo_sha, "size": 100, "content_type": "image/jpeg"},
        headers=headers,
    )
    assert init_res_1.status_code == 200
    session_id_1 = init_res_1.json()["upload_id"]

    init_res_2 = httpx.post(
        f"{server.base_url}/api/photobooth/upload/init",
        json={"filename": "shot.jpg", "sha256": photo_sha, "size": 100, "content_type": "image/jpeg"},
        headers=headers,
    )
    assert init_res_2.status_code == 200
    session_id_2 = init_res_2.json()["upload_id"]

    assert session_id_1 == session_id_2


def test_device_disabled_after_init_rejected(server: WeddingHubReferenceServer, device: dict, device_token: str):
    """POST /complete returns 403 if device is disabled after init but before complete."""
    headers = {"Authorization": f"Bearer {device_token}"}
    content = b"\xff\xd8" + b"\x00" * 50 + b"\xff\xd9"
    sha = hashlib.sha256(content).hexdigest()

    init_res = httpx.post(
        f"{server.base_url}/api/photobooth/upload/init",
        json={"filename": "shot.jpg", "sha256": sha, "size": len(content), "content_type": "image/jpeg"},
        headers=headers,
    )
    assert init_res.status_code == 200
    upload_id = init_res.json()["upload_id"]
    httpx.put(init_res.json()["upload_url"], content=content)

    # Disable device
    device["enabled"] = False

    comp_res = httpx.post(
        f"{server.base_url}/api/photobooth/upload/complete",
        json={"upload_id": upload_id, "sha256": sha},
        headers=headers,
    )
    assert comp_res.status_code == 403
