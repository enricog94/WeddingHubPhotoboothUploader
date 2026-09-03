# WeddingHub Photobooth Uploader

A robust, offline-first background Linux service designed for **PhotoboothProject** installations. It monitors the photobooth's local photo directory, ensures completed write operations, records photos into a persistent SQLite queue, and asynchronously uploads them to **WeddingHub**.

---

## Key Principles

1. **Photobooth Priority**: The photobooth must continue taking, previewing, and printing photos without interruption. Upload failures or network outages never block local operations.
2. **Offline-First & Persistent**: Photos remain securely queued in a local SQLite database (WAL mode) until connectivity is available. State survives reboots and unexpected service interruptions.
3. **No Duplicate Media**: SHA-256 fingerprinting prevents re-uploading duplicate content locally, and the WeddingHub API guarantees server-side idempotency.
4. **Least Privilege**: The device token resolves the wedding association server-side. No Supabase service-role keys, database passwords, or administrative credentials exist on the photobooth PC.
5. **No Data Loss**: The uploader never deletes, modifies, renames, or resizes the original photo files.

---

## Requirements

- Linux PC (Debian, Ubuntu, Raspberry Pi OS, etc.)
- Python 3.11+
- PhotoboothProject saving completed JPEG photos to a local directory

---

## Installation

### 1. Automated Installation via Helper Script

A setup script is provided in `scripts/install.sh`:

```bash
sudo ./scripts/install.sh
```

This will:
- Create `/etc/weddinghub-photobooth` and install default `config.toml` (if not existing, preserving existing configurations on upgrade).
- Create dedicated virtual environment under `/opt/weddinghub-photobooth/venv` (fully compliant with PEP 668 on Debian 12+ and Ubuntu 24.04+).
- Create `/var/lib/weddinghub-photobooth` for the SQLite queue database.
- Install global symlink `/usr/local/bin/weddinghub-photobooth`.
- Install the systemd service `/etc/systemd/system/weddinghub-photobooth-uploader.service`.
- Reload `systemd`.

### 2. Manual Installation

1. Create a dedicated virtualenv and install the package:
   ```bash
   sudo mkdir -p /opt/weddinghub-photobooth
   sudo python3 -m venv /opt/weddinghub-photobooth/venv
   sudo /opt/weddinghub-photobooth/venv/bin/pip install .
   sudo ln -sf /opt/weddinghub-photobooth/venv/bin/weddinghub-photobooth /usr/local/bin/weddinghub-photobooth
   ```
2. Copy configuration:
   ```bash
   sudo mkdir -p /etc/weddinghub-photobooth /var/lib/weddinghub-photobooth
   sudo cp config.example.toml /etc/weddinghub-photobooth/config.toml
   sudo chown -R photobooth:photobooth /var/lib/weddinghub-photobooth
   sudo chmod 640 /etc/weddinghub-photobooth/config.toml
   ```
3. Edit `/etc/weddinghub-photobooth/config.toml` with your device token and photo directory:
   ```toml
   api_base_url = "https://wedding.eshome.it"
   device_token = "YOUR_ASSIGNED_DEVICE_TOKEN"
   watch_directory = "/var/lib/photobooth/photos"
   db_path = "/var/lib/weddinghub-photobooth/queue.db"
   ```
4. Install and enable the systemd service:
   ```bash
   sudo cp systemd/weddinghub-photobooth-uploader.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now weddinghub-photobooth-uploader.service
   ```

---

## CLI Usage

The `weddinghub-photobooth` CLI provides inspection and management tools. The canonical command structure accepts `-c / --config` before the subcommand:

### Health & Status
```bash
weddinghub-photobooth -c /etc/weddinghub-photobooth/config.toml status
```
Outputs service configuration (with device token safely masked), total queued, uploading, retrying, uploaded, and failed counts, plus the last upload timestamp and last error.

### Test Connectivity & Credentials
```bash
weddinghub-photobooth -c /etc/weddinghub-photobooth/config.toml test
```
Tests connectivity with the WeddingHub backend and verifies that `device_token` is valid and authorized, displaying the device ID, name, enabled status, and associated wedding slug.

### Run Foreground Daemon
```bash
weddinghub-photobooth -c /etc/weddinghub-photobooth/config.toml run
```

### Inspect Queue
```bash
weddinghub-photobooth -c /etc/weddinghub-photobooth/config.toml queue
weddinghub-photobooth -c /etc/weddinghub-photobooth/config.toml queue --limit 20 --status RETRY
```

### Force Immediate Retry
```bash
weddinghub-photobooth -c /etc/weddinghub-photobooth/config.toml retry
```
Resets all photos currently in `RETRY` or `FAILED` state back to `PENDING` so the worker processes them immediately.

### Version
```bash
weddinghub-photobooth version
```


---

## Architecture

```text
PhotoboothProject
       │  (writes JPEG photos)
       ▼
Local Directory (/var/lib/photobooth/photos)
       │
       ▼
PhotoWatcher & Stability Checker
  - Detects .jpg / .jpeg files
  - Validates size/mtime stability over stability_delay (default 2s)
  - Inspects JPEG start (0xFFD8) and end (0xFFD9) markers
       │
       ▼
Persistent SQLite Queue (queue.db, WAL mode)
  - Recovers crashed in-flight items on startup
  - SHA-256 deduplication
       │
       ▼
Upload Worker (Sequential)
       │
       ▼
WeddingHub API (httpx, TLS verified)
  1. POST /api/photobooth/upload/init (Bearer <device-token>)
     -> "already_exists" -> Marked UPLOADED immediately
     -> "upload_required" -> Presigned short-lived URL
  2. Binary PUT to upload_url
  3. POST /api/photobooth/upload/complete
       │
       ▼
Retry Engine
  - Transient errors (5xx, timeouts): exponential backoff with jitter
  - HTTP 429: honors Retry-After header
  - HTTP 401/403: auth failure backoff without hammering API
```

---

## Logging & Troubleshooting

View service logs via `journalctl`:

```bash
journalctl -u weddinghub-photobooth-uploader.service -f
```

Structured lifecycle events:
- `PHOTO_DISCOVERED`: New photo candidate detected.
- `PHOTO_STABLE`: File finished writing and verified as valid JPEG.
- `PHOTO_QUEUED`: Photo added to SQLite queue.
- `UPLOAD_STARTED`: Binary upload initiated.
- `UPLOAD_COMPLETED`: Successfully uploaded to WeddingHub.
- `UPLOAD_RETRY`: Upload deferred due to transient error or rate limit.
- `UPLOAD_FAILED`: Permanent failure recorded.
- `AUTH_ERROR`: Device token invalid/unauthorized.
- `SERVICE_STARTED` / `SERVICE_STOPPED`: Lifecycle boundaries.

---

## Documentation & Backend Integration

- **[ARCHITECTURE.md](file:///c:/Enrico/SviluppoSW/PhotoboothUploader/ARCHITECTURE.md)**: Deep dive into component design, threading, state transitions, crash recovery, and security.
- **[BACKEND_INTEGRATION.md](file:///c:/Enrico/SviluppoSW/PhotoboothUploader/BACKEND_INTEGRATION.md)**: Complete API contract, OpenAPI specification, database schema, and integration guide for WeddingHub.


