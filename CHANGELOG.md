# Changelog

All notable changes to the WeddingHub Photobooth Uploader will be documented in this file.

The project follows Semantic Versioning and a Keep-a-Changelog-style structure.

## [Unreleased]

### Added

- **Persistent Scanner Fast-Path**: Implemented a `file_observations` SQLite table to securely skip unmodified files across scanner restarts without incurring hashing/thread penalties.
- **Pre-upload Integrity Validation**: Enforced strict pre-upload validations in `UploadWorker`; detecting any file mutation (size or SHA-256) since enqueueing now aborts the upload and forces file rediscovery.
- **Installer Preflight Integrity**: Added strict python/venv validations to installation script (`scripts/install.sh`) to prevent corrupt system environments.
- **HTTP-Date Retry Support**: Enhanced `Retry-After` parsing to elegantly support both delta seconds and RFC 7231 HTTP dates.
- **`weddinghub-photobooth test` CLI command**: Validates connectivity and verifies device credentials against WeddingHub, displaying device metadata and event association.
- **Device Verification API**: Implemented `verify_device()` in `WeddingHubApiClient` (`GET /api/photobooth/verify`).
- **Batch / Step Processing**: Added `process_queue_once()` to `UploadWorker` for deterministic queue processing in tests and CLI scripts.
- **Reference Test Server (`WeddingHubReferenceServer`)**: Dedicated Python test harness (`tests/support/weddinghub_reference_server.py`) replicating PostgreSQL/Supabase and R2 storage behavior for automated integration and contract testing.
- **Backend Contract Test Suite (`tests/backend/`)**: Automated tests verifying token SHA-256 validation, device activation killswitch, strict payload validation, deduplication, and multi-wedding isolation.
- **End-to-End Integration Suite (`tests/e2e/`)**: Full flow verification from filesystem detection to Presigned PUT and media registration, offline retry resilience, and crash recovery without duplicate media.
- **Comprehensive Documentation**:
  - `BACKEND_INTEGRATION.md`: Complete API specification, OpenAPI schema, and deployment guide for WeddingHub.
  - `ARCHITECTURE.md`: Detailed architecture design, threading model, crash recovery, and multi-tenant security.

### Changed

- **Bounded Stability Threads**: Shifted unmanaged thread-per-file concurrency in `PhotoWatcher` to a `ThreadPoolExecutor` (max 4 workers) to eliminate unbounded connection spikes.
- **Event-First Contract Normalization**: Updated device verification and CLI to rigorously validate API payloads under the new generic EventHub structure (`event` replacing `wedding`).
- **Type-Safe Configuration**: Placed robust boolean type requirements on sensitive config fields like `allow_insecure_http` to catch accidental string casting.
- **CLI / Systemd Syntax**: Standardized CLI invocation to canonical `weddinghub-photobooth -c /etc/weddinghub-photobooth/config.toml <command>`, using parent argument parsing to seamlessly support `-c` before or after subcommands.
- **Presigned R2 403 Retry**: HTTP 403 on storage presigned PUT is treated as a transient/retriable expiration error (`TransientApiError`), triggering a fresh `/init` session rather than a permanent failure.
- **Session Expiry 410 Recovery**: HTTP 410 Gone on `/complete` is classified as retriable (`TransientApiError`), allowing the client to transition the item to `RETRY` and re-initiate the upload with a new session.
- **Streaming PUT Uploads**: Modified `upload_binary()` to stream binary files directly via file object with `Content-Length` header, eliminating `f.read()` memory consumption.
- **PEP 668 Compliant Linux Installer**: Refactored `scripts/install.sh` to install inside a dedicated virtual environment at `/opt/weddinghub-photobooth/venv`, updating systemd `ExecStart` and creating symlink in `/usr/local/bin`.
- **Installer Idempotency**: Existing `config.toml` and SQLite queue databases are strictly preserved across reinstalls and upgrades.

### Security

- Enforced server-side token hashing using SHA-256 for all device tokens.
- Strict multi-tenant isolation ensuring devices cannot write, read, or complete uploads for weddings other than their own.
- Remote device revocation kill-switch (`enabled = 0`).
- Enforced HTTPS for remote endpoints to protect device Bearer tokens from unencrypted exposure, restricting plain HTTP strictly to local test addresses (`localhost`, `127.0.0.1`, `::1`) or explicit dev override.

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
