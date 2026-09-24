# WeddingHub Photobooth Uploader — AGENT.md

## Repository source of truth

Before changing uploader behavior, read in this order:

1. `docs/README.md` — documentation authority and legacy/superseded material;
2. `docs/EVENTHUB_INTEGRATION.md` — authoritative EventHub client/server contract;
3. `docs/FINAL_HARDENING_REVIEW.md` — bounded reliability work required before field test;
4. `ARCHITECTURE.md`, `CHANGELOG.md`, and `VERSION` for current implementation state.

Do not use chat history as the source of truth when these repository documents exist.
The historical root `BACKEND_INTEGRATION.md` is superseded for new EventHub work and must
not be copied into new V2 backend code.

The active backend domain is Event-first. New integration behavior must not require the
Linux client to choose or store an `event_id`; the device token resolves Event ownership
server-side.

---

## Project identity

Project: `WeddingHub Photobooth Uploader`

Purpose: a small, robust Linux background agent that watches the folder where PhotoboothProject saves completed photos and uploads them asynchronously to WeddingHub without ever blocking the photobooth shooting/printing workflow.

The uploader must be designed first for Enrico & Serena's wedding, but the architecture must already support multiple weddings and multiple rentable photobooth devices.

---

## Core principles

1. **Photobooth operation has priority over upload.**
   Upload failures, network outages, WeddingHub outages, authentication errors or database errors must never block shooting, preview, printing or local photo storage.

2. **Offline-first.**
   A photo that cannot be uploaded immediately must remain queued locally and be retried later.

3. **No data loss.**
   Never delete or alter the original photo unless explicitly implemented in a future version.

4. **Persistent queue.**
   Upload state must survive process crashes, reboots and network outages.

5. **Idempotency / no duplicate media.**
   The same local photo must not generate duplicate WeddingHub media entries after retries or restarts.

6. **Least privilege.**
   The Linux device must never contain Supabase service-role credentials, database passwords or other privileged backend credentials.

7. **Device authentication.**
   The client authenticates using a revocable device token associated server-side with a specific wedding and photobooth device.

8. **Simple operations.**
   The software must be easy to install, start, stop, inspect and troubleshoot on Debian/Linux using systemd.

---

# V1 Scope

Implement only what is needed for a reliable first production-grade uploader.

## Included

- Linux service.
- Watch one configurable directory.
- Detect newly created `.jpg` / `.jpeg` files.
- Ensure the file is completely written before queueing/uploading it.
- SQLite persistent queue.
- Asynchronous upload worker.
- Retry with exponential backoff.
- SHA-256 file fingerprinting.
- Idempotent upload flow.
- Device token authentication.
- WeddingHub upload API integration.
- Structured local logging.
- systemd service.
- CLI/status commands useful for troubleshooting.
- Unit/integration tests that do not require browser interaction.

## Explicitly excluded from V1

- GUI.
- Browser dashboard.
- Video upload.
- HEIC/HEIF upload.
- Automatic image resizing on the Linux client.
- Thumbnail generation on the client.
- Automatic photo deletion.
- Bidirectional synchronization.
- Remote control of PhotoboothProject.
- Printing logic.
- Photobooth camera control.
- Editing PhotoboothProject itself unless strictly necessary and explicitly requested.

WeddingHub/backend remains responsible for image processing, previews/thumbnails and media presentation.

---

# Preferred technology

Preferred language: **Python 3.11+**.

Keep dependencies minimal.

Suggested components:

- filesystem events: `watchdog` or Linux/inotify-compatible solution;
- HTTP: `httpx`;
- persistence: Python standard `sqlite3` unless a strong reason exists otherwise;
- config: TOML (`tomllib`) or environment variables;
- logging: standard Python logging with structured, human-readable output;
- tests: `pytest`.

Do not introduce a framework unless it materially improves reliability.

---

# Runtime architecture

Conceptual pipeline:

```text
PhotoboothProject
      |
      v
Local output directory
      |
      v
File watcher
      |
      v
File stability validation
      |
      v
Persistent SQLite queue
      |
      v
Upload worker
      |
      v
WeddingHub Photobooth API
      |
      v
Object storage + media DB
```

The watcher and uploader must be logically decoupled.

A slow or unavailable WeddingHub must not prevent newly created photos from entering the local queue.

---

# Queue model

Persist at least the following fields:

- `id`
- `local_path`
- `filename`
- `sha256`
- `file_size`
- `created_at`
- `discovered_at`
- `status`
- `attempt_count`
- `next_attempt_at`
- `last_attempt_at`
- `uploaded_at`
- `remote_media_id`
- `last_error`

Recommended states:

```text
PENDING
UPLOADING
RETRY
UPLOADED
FAILED
```

On service startup, records left in `UPLOADING` because of a crash must safely return to a retriable state.

`FAILED` is reserved for conditions requiring human intervention or for a configurable retry limit. Network and server 5xx errors normally belong to `RETRY`.

---

# File stability

Do not assume a filesystem create event means a JPEG is complete.

Before enqueue/upload, confirm that the file is stable. A simple acceptable strategy is:

1. file exists;
2. size > 0;
3. read size/mtime;
4. wait a short configurable stability interval;
5. verify size/mtime have not changed;
6. optionally verify JPEG can be opened/read enough to confirm it is not truncated.

Never hold an exclusive lock that interferes with PhotoboothProject.

---

# Duplicate prevention

Calculate SHA-256 after the file is stable.

Use the SHA-256 as the primary content fingerprint.

The local database should enforce uniqueness where appropriate.

The WeddingHub API must also support idempotency. A repeated request for an already registered content/device combination must not create a second media record.

Do not rely on filename alone.

---

# Authentication and tenant isolation

The client config contains a device token.

Example conceptual config:

```toml
api_base_url = "https://wedding.eshome.it"
watch_directory = "/var/lib/photobooth/photos"
device_token = "..."
```

Do NOT store:

- Supabase service role keys;
- DB credentials;
- WeddingHub super-admin credentials.

Server-side, the device token resolves:

```text
photobooth_device -> wedding_id
```

The client must not be allowed to choose an arbitrary `wedding_id` to upload into.

The wedding association is authoritative on the server.

Tokens must be revocable.

Do not log the full token.

---

# API contract — target design

Use a two-phase flow unless implementation constraints strongly justify an equivalent secure design.

## 1. Initialize upload

```http
POST /api/photobooth/upload/init
Authorization: Bearer <device-token>
Content-Type: application/json
```

Suggested request:

```json
{
  "filename": "photo_000123.jpg",
  "sha256": "...",
  "size": 1234567,
  "content_type": "image/jpeg",
  "captured_at": "2027-07-24T18:42:12+02:00"
}
```

Suggested response for a new file:

```json
{
  "status": "upload_required",
  "upload_id": "...",
  "upload_url": "...",
  "headers": {},
  "expires_at": "..."
}
```

Suggested response when content is already registered:

```json
{
  "status": "already_exists",
  "media_id": "..."
}
```

## 2. Upload binary

Upload directly using the returned short-lived URL.

The URL must not expose privileged storage credentials.

## 3. Complete upload

```http
POST /api/photobooth/upload/complete
Authorization: Bearer <device-token>
Content-Type: application/json
```

Suggested request:

```json
{
  "upload_id": "...",
  "sha256": "..."
}
```

Suggested response:

```json
{
  "status": "completed",
  "media_id": "..."
}
```

Server-created WeddingHub media should include a source marker such as:

```text
source = photobooth
```

For the Enrico & Serena wedding, photobooth media may be configured server-side to become immediately approved, but this behavior must be configuration/policy driven rather than hardcoded in the Linux agent.

---

# HTTP/error behavior

Handle these classes explicitly.

## Network unavailable / timeout

- queue remains intact;
- status -> `RETRY`;
- exponential backoff;
- no busy loops.

## HTTP 5xx

- considered temporary;
- status -> `RETRY`.

## HTTP 429

- honor `Retry-After` when present;
- otherwise use backoff.

## HTTP 401 / 403

- do not continuously hammer the API;
- mark authentication/device problem clearly in logs/status;
- use a long retry delay or `FAILED`/blocked state appropriate to implementation;
- retain all photos locally.

## HTTP 4xx caused by invalid file/request

- record a clear error;
- avoid infinite retry if the condition is permanently invalid.

## Process crash during upload

- restart must recover safely;
- the API idempotency mechanism must prevent duplicated WeddingHub media.

---

# Retry policy

Implement exponential backoff with a cap and jitter.

Example policy (configurable):

```text
30 s
1 min
2 min
5 min
10 min
30 min
60 min max
```

Exact values can be adjusted, but retries must not flood WeddingHub.

---

# Configuration

Preferred production config path:

```text
/etc/weddinghub-photobooth/config.toml
```

Provide a committed example config:

```text
config.example.toml
```

Never commit a real token.

Recommended settings:

- API base URL
- device token
- watch directory
- SQLite database path
- log path or logging mode
- file stability delay
- upload timeout
- max concurrency
- retry policy

V1 should default to **one upload at a time** unless benchmarks demonstrate a need for more.

Reliability is more important than raw throughput.

---

# Linux/systemd

Provide a production-ready systemd unit.

Suggested service name:

```text
weddinghub-photobooth-uploader.service
```

Requirements:

- starts automatically at boot;
- restarts automatically on unexpected failure;
- runs as a dedicated unprivileged Linux user where practical;
- has read access to the PhotoboothProject output folder;
- has write access only to its own DB/log/runtime paths;
- no root requirement during normal execution.

Use sensible hardening directives where compatible with access to the watched folder.

---

# CLI / operational commands

Provide a simple CLI such as:

```bash
weddinghub-photobooth status
weddinghub-photobooth queue
weddinghub-photobooth retry
weddinghub-photobooth version
```

`status` should communicate at least:

- service/app version;
- configured API URL (not token);
- watched directory;
- queued count;
- retry count;
- failed count;
- uploaded count;
- last successful upload time;
- last error summary.

Never print secrets.

---

# Logging

Logs must be useful during an actual wedding.

For each photo, log enough to follow the lifecycle without exposing secrets.

Example events:

```text
PHOTO_DISCOVERED
PHOTO_STABLE
PHOTO_QUEUED
UPLOAD_STARTED
UPLOAD_RETRY
UPLOAD_COMPLETED
UPLOAD_FAILED
AUTH_ERROR
SERVICE_STARTED
SERVICE_STOPPED
```

Include filename / queue id / shortened hash when useful.

Never log full authentication tokens or signed upload URLs at normal log levels.

---

# Repository structure

Prefer a clear structure similar to:

```text
/
├── AGENT.md
├── README.md
├── CHANGELOG.md
├── VERSION
├── pyproject.toml
├── config.example.toml
├── src/
│   └── weddinghub_photobooth/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── db.py
│       ├── models.py
│       ├── watcher.py
│       ├── uploader.py
│       ├── api.py
│       ├── hashing.py
│       └── logging_config.py
├── tests/
│   ├── unit/
│   └── integration/
├── systemd/
│   └── weddinghub-photobooth-uploader.service
└── scripts/
    └── install.sh
```

Adjust if implementation warrants it, but preserve separation of concerns.

---

# Testing requirements

Do not use browser-based tests.

Normal verification should be automated CLI-level testing.

At minimum test:

1. detecting a newly created JPEG;
2. ignoring unsupported files;
3. not queueing a still-growing file prematurely;
4. queue persistence across restart;
5. successful upload;
6. duplicate local file detection;
7. idempotent retry after simulated crash;
8. network timeout;
9. HTTP 500;
10. HTTP 429;
11. HTTP 401/403;
12. malformed/permanent HTTP 4xx;
13. process restart with records in `UPLOADING`;
14. no original-file deletion;
15. no secrets emitted in logs;
16. CLI status correctness.

Use mocked/local HTTP services for automated tests where appropriate.

Do not perform repeated real uploads against the production WeddingHub endpoint during ordinary development.

When real Linux integration testing is explicitly requested, test with a dedicated development device token and a small set of test JPEGs.

---

# Development workflow

Before making changes:

1. inspect the current repository state;
2. read this `AGENT.md`;
3. read `CHANGELOG.md`;
4. read `VERSION`;
5. understand existing architecture before modifying it.

For every meaningful change:

- keep scope focused;
- avoid unrelated refactors;
- update tests;
- update documentation when behavior changes;
- update `CHANGELOG.md` under `[Unreleased]`;
- do not silently change API contracts.

At the end of each task report:

1. files changed;
2. behavior implemented;
3. tests executed and result;
4. known limitations;
5. whether `CHANGELOG.md` was updated;
6. proposed next step.

---

# Versioning

Use Semantic Versioning:

```text
MAJOR.MINOR.PATCH
```

Examples:

- `0.1.0`: first working development milestone;
- `0.2.0`: new backward-compatible feature;
- `0.2.1`: bug fix;
- `1.0.0`: production-ready stable release for wedding use.

During early development remain on `0.x.y`.

The authoritative current version is stored in the root `VERSION` file.

Code/package version must stay synchronized with `VERSION`.

Do not increment the version for every commit.

Increment version only when a coherent milestone/release is completed.

---

# CHANGELOG discipline

Use a Keep-a-Changelog-like format.

Every meaningful user-visible or operational change goes first under:

```markdown
## [Unreleased]
```

Use categories when applicable:

```text
Added
Changed
Fixed
Security
Removed
```

When releasing a milestone:

1. move relevant entries from `[Unreleased]` into a new version section;
2. add the release date in ISO format `YYYY-MM-DD`;
3. increment `VERSION`;
4. synchronize package version;
5. leave a fresh empty `[Unreleased]` section.

Never rewrite previous released changelog entries unless correcting a factual typo.

---

# Git discipline

Do not force-push or rewrite history unless explicitly requested.

Prefer small coherent commits.

Recommended commit prefixes:

```text
feat:
fix:
test:
docs:
chore:
security:
```

Examples:

```text
feat: add persistent SQLite upload queue
fix: recover interrupted uploads after restart
test: cover offline retry backoff
docs: document systemd installation
```

Do not commit generated secrets, production tokens, databases containing real wedding photos, or test credentials.

---

# Security requirements

Treat the photobooth computer as a potentially accessible event device.

Therefore:

- minimize stored secrets;
- device token must be revocable;
- never expose admin/session credentials;
- validate TLS certificates;
- never disable TLS verification in production;
- sanitize filenames/server input;
- protect against path traversal;
- enforce wedding isolation server-side;
- upload URLs must be short-lived;
- avoid executing shell commands derived from filenames;
- dependencies should be pinned/controlled through the project package metadata.

---

# Non-goals / avoid overengineering

Do not add Kubernetes, Docker, Redis, RabbitMQ, Celery or a remote queue for V1.

Do not introduce distributed-system complexity where SQLite + systemd is sufficient.

Do not implement a web UI unless explicitly requested.

Do not redesign WeddingHub unrelated areas.

---

# Definition of Done — V1

V1 is complete when, on the target Linux photobooth machine:

1. the service starts automatically;
2. a JPEG placed/generated in the watched PhotoboothProject directory is detected;
3. the photo is safely persisted in the local queue;
4. with connectivity available, the photo reaches WeddingHub;
5. WeddingHub records it under the wedding linked to the device token;
6. media is marked `source=photobooth`;
7. a reboot during pending work does not lose the photo;
8. an internet outage does not lose the photo;
9. retry after a partial/crashed upload does not create duplicates;
10. the original JPEG remains untouched locally;
11. logs and CLI allow an operator to understand uploader health;
12. automated tests pass.
