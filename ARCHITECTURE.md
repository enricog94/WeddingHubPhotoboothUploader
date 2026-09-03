# WeddingHub Photobooth Uploader — Architecture & Design

This document details the software architecture, component structure, state lifecycle, crash recovery mechanics, and security model of the **WeddingHub Photobooth Uploader**.

---

## 1. System Overview

The daemon runs on a photobooth computer (Raspberry Pi, Linux mini PC, or Windows machine). It watches a designated photo directory, detects newly saved pictures, verifies their file-write stability and JPEG integrity, and reliably synchronizes them to WeddingHub in the background with zero data loss.

```
+------------------------------------------------------------------------------------+
|                               Photobooth Machine                                   |
|                                                                                    |
|   +------------------+         +--------------------+         +----------------+   |
|   | DSLR / Software  | ------> | Watched Directory  | <------ |  PhotoWatcher  |   |
|   | (darktable, etc.)|         | (/var/photobooth)  |         | (Inotify/Poll) |   |
|   +------------------+         +--------------------+         +--------+-------+   |
|                                                                        |           |
|                                                               [Stability Check]    |
|                                                                        |           |
|                                                                        v           |
|   +------------------+         +--------------------+         +----------------+   |
|   |  UploadWorker    | <====== | SQLite DB (WAL)    | <====== | SHA-256 Hash   |   |
|   | (Sequential Loop)|         | (upload_queue)     |         | Enqueue PENDING|   |
|   +--------+---------+         +--------------------+         +----------------+   |
+------------|-----------------------------------------------------------------------+
             |
             | HTTPS (Bearer Device Token)
             v
+------------------------------------------------------------------------------------+
|                      WeddingHub Cloudflare & Supabase Stack                        |
|                                                                                    |
|   +---------------------------+                +-------------------------------+   |
|   |  Cloudflare Worker API    | =============> | Supabase PostgreSQL Database  |   |
|   |  (/api/photobooth/*)      | (Hyperdrive)   | (devices, sessions, media)    |   |
|   +-------------+-------------+                +-------------------------------+   |
|                 |                                                                  |
|                 +----------------------------> +-------------------------------+   |
|                   Direct PUT / Presigned S3    | Cloudflare R2 Bucket          |   |
|                                                | (weddings/<slug>/photobooth/) |   |
|                                                +-------------------------------+   |
+------------------------------------------------------------------------------------+
```

---

## 2. Core Components

### 2.1. `PhotoWatcher`
- Combines real-time inotify filesystem events (`watchdog`) with periodic directory polling fallback.
- Guarantees detection even if camera software moves files atomically, writes them slowly, or creates temporary locks.
- **Stability Verification**:
  - Samples file size and `mtime` across a configurable delay (`stability_delay`).
  - Verifies minimum JPEG structural markers: Starts with SOI (`0xFFD8`) and ends with EOI (`0xFFD9`).
  - Computes cryptographic SHA-256 hash.
  - Enqueues into SQLite queue with state `PENDING`.

### 2.2. `Database` (SQLite Persistence)
- Configured with `journal_mode=WAL` (Write-Ahead Logging), `synchronous=NORMAL`, and `busy_timeout=5000`.
- Supports concurrent access from multiple threads via a thread-safe mutex and connection isolation.
- Strictly deduplicates items on SHA-256 (`UNIQUE` constraint).
- Tracks attempt counts, error strings, retry scheduling, and remote media IDs.

### 2.3. `UploadWorker`
- Runs in a dedicated background worker thread.
- Processes items sequentially, preserving order and avoiding bandwidth congestion on mobile hot spots.
- Manages exponential backoff with symmetric jitter:
  $$\text{delay} = \min(\text{initial\_delay} \times 2^{\text{attempt}}, \text{max\_delay}) \pm \text{jitter}$$
- Differentiates transient errors (HTTP 429, 5xx, network drops) from permanent errors (HTTP 400, 401, 403, 404).

### 2.4. `WeddingHubApiClient`
- Modern HTTP client powered by `httpx`.
- Implements the 3-step upload protocol:
  1. `POST /api/photobooth/upload/init`: Authenticates and obtains storage URL.
  2. `PUT <upload_url>`: Streams binary JPEG bytes directly to storage.
  3. `POST /api/photobooth/upload/complete`: Registers media in PostgreSQL.
- Implements connectivity and token validation via `GET /api/photobooth/verify`.

---

## 3. Upload State Lifecycle & Crash Recovery

Each photo progresses through deterministic states:

```
                  +---------------+
                  |    PENDING    | <--------------------+
                  +-------+-------+                      |
                          |                              |
                  (Worker picks up)                      |
                          |                              |
                          v                              |
                  +---------------+                      |
         +------> |   UPLOADING   |                      |
         |        +-------+-------+                      |
         |                |                              |
    (Crash / Reboot)      +--------------+               |
         |                |              |               |
         |           (Transient)    (Permanent)     (Success)
         |                |              |               |
         |                v              v               v
         |        +---------------+ +----------+ +---------------+
         +------- |     RETRY     | |  FAILED  | |   UPLOADED    |
                  +---------------+ +----------+ +---------------+
```

### Crash Recovery Mechanism
If power is interrupted while an upload is in progress (`UPLOADING` state):
1. On next process startup, `Database.recover_in_flight()` executes before starting the worker thread.
2. Any record stuck in `UPLOADING` is automatically transitioned to `RETRY` with `last_error="Recovered from ungraceful shutdown during UPLOADING"`.
3. The photo is re-uploaded gracefully with zero duplicate entries created on WeddingHub due to server-side idempotency.

---

## 4. Multi-Tenant Security & Device Isolation

1. **Token Security**:
   - The device token is an opaque high-entropy secret.
   - The server stores only `hashlib.sha256(token).hexdigest()`.
   - The client never stores or knows the internal database `wedding_id`.
2. **Storage Isolation**:
   - R2 object keys are forced server-side to `weddings/<wedding_slug>/photobooth/<uuid>.jpg`.
   - The device has no ability to write or inspect objects in another wedding's path.
3. **Remote Revocation**:
   - Setting `enabled = 0` on `photobooth_devices` immediately halts all subsequent uploads with `403 Forbidden`.
