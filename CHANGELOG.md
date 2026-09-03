# Changelog

All notable changes to the WeddingHub Photobooth Uploader will be documented in this file.

The project follows Semantic Versioning and a Keep-a-Changelog-style structure.

## [Unreleased]

### Added

- **`weddinghub-photobooth test` CLI command**: Validates connectivity and verifies device credentials against WeddingHub, displaying device metadata and wedding association.
- **Device Verification API**: Implemented `verify_device()` in `WeddingHubApiClient` (`GET /api/photobooth/verify`).
- **Batch / Step Processing**: Added `process_queue_once()` to `UploadWorker` for deterministic queue processing in tests and CLI scripts.
- **Reference Test Server (`WeddingHubReferenceServer`)**: Dedicated Python test harness (`tests/support/weddinghub_reference_server.py`) replicating PostgreSQL/Supabase and R2 storage behavior for automated integration and contract testing.
- **Backend Contract Test Suite (`tests/backend/`)**: Automated tests verifying token SHA-256 validation, device activation killswitch, strict payload validation, deduplication, and multi-wedding isolation.
- **End-to-End Integration Suite (`tests/e2e/`)**: Full flow verification from filesystem detection to Presigned PUT and media registration, offline retry resilience, and crash recovery without duplicate media.
- **Comprehensive Documentation**:
  - `BACKEND_INTEGRATION.md`: Complete API specification, OpenAPI schema, and deployment guide for WeddingHub.
  - `ARCHITECTURE.md`: Detailed architecture design, threading model, crash recovery, and multi-tenant security.

### Security

- Enforced server-side token hashing using SHA-256 for all device tokens.
- Strict multi-tenant isolation ensuring devices cannot write, read, or complete uploads for weddings other than their own.
- Remote device revocation kill-switch (`enabled = 0`).

## [0.1.0] - 2026-09-02

### Added


- Initial technical specification and architecture for the Linux WeddingHub Photobooth Uploader.
- Configuration loader supporting TOML files (`/etc/weddinghub-photobooth/config.toml`, custom paths, and `WEDDINGHUB_CONFIG_PATH`).
- File watcher engine using `watchdog` and periodic directory scanner to detect completed photos without interfering with PhotoboothProject.
- Two-phase file stability validator verifying size/mtime stability over configurable delay and validating JPEG SOI/EOI markers.
- Persistent SQLite queue with WAL mode, tracking `PENDING`, `UPLOADING`, `RETRY`, `UPLOADED`, and `FAILED` states.
- Automatic crash recovery transitioning abandoned `UPLOADING` records back to `RETRY` on startup.
- SHA-256 fingerprint calculation and local deduplication preventing duplicate media queueing.
- WeddingHub API client implementing the 3-step contract (`init` -> presigned binary PUT -> `complete`).
- Offline-first retry engine with exponential backoff, jitter, and support for HTTP 429 `Retry-After`.
- Actionable error handling and backoff for HTTP 401/403 authentication failures.
- Permanent error classification for 4xx invalid requests into `FAILED` state.
- Guarantee that original JPEG files are never deleted, renamed, resized, or altered.
- Structured logging with lifecycle events (`PHOTO_DISCOVERED`, `PHOTO_STABLE`, `PHOTO_QUEUED`, `UPLOAD_STARTED`, `UPLOAD_COMPLETED`, etc.).
- Secret masking filter preventing leakage of device tokens, Bearer headers, and signed URL query parameters.
- Command-line interface `weddinghub-photobooth` (`status`, `queue`, `retry`, `version`, `run`).
- Production-ready systemd unit (`weddinghub-photobooth-uploader.service`) with Linux security sandboxing.
- Linux setup script (`scripts/install.sh`) for automated deployment.
- Comprehensive automated unit and integration test suite with local mock WeddingHub server.

### Security

- Authenticates using a revocable device token via `Authorization: Bearer <device-token>`.
- Device token resolves to wedding server-side; client never chooses or overrides `wedding_id`.
- TLS certificate validation strictly enforced for all remote API calls.
- Device tokens, Bearer tokens, and signed upload URLs sanitized from all logs and status views.
- No Supabase service-role keys or database credentials stored on the photobooth PC.
