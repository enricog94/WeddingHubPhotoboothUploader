# Antigravity Prompt — WeddingHub Photobooth Uploader V1

You are working on the **WeddingHub Photobooth Uploader**, a Linux-side agent used by a PhotoboothProject installation.

Your task is to design and implement the first reliable V1 of the uploader.

## FIRST ACTION

Before writing code:

1. read `AGENT.md` completely;
2. inspect the repository;
3. read `CHANGELOG.md` and `VERSION`;
4. if the repository is empty, initialize the project structure described by `AGENT.md`;
5. summarize the architecture you are going to implement before editing files.

Do not deviate from `AGENT.md` without clearly explaining why.

---

# Goal

PhotoboothProject already saves completed photos locally on a Linux PC.

Create a background Linux service that watches the configured photo directory and uploads new JPEG files to WeddingHub asynchronously and reliably.

The photobooth must continue operating even if:

- internet is offline;
- WeddingHub is unavailable;
- authentication temporarily fails;
- the uploader crashes;
- Linux reboots.

No upload problem may block the normal PhotoboothProject workflow.

---

# V1 functional requirements

Implement:

### 1. Configuration

Support a config file, preferably:

```text
/etc/weddinghub-photobooth/config.toml
```

Commit `config.example.toml` but never real secrets.

Required configuration should include:

- WeddingHub API base URL;
- device token;
- watched photo directory;
- SQLite DB path;
- stability delay;
- upload timeout;
- retry parameters;
- logging configuration where needed.

### 2. Directory watcher

Watch the configured directory for new `.jpg` and `.jpeg` files.

Do not upload immediately on filesystem creation.

Verify that the file has finished being written by checking stability of size/mtime over a configurable interval.

Do not interfere with PhotoboothProject access to the file.

### 3. Persistent SQLite queue

Create a persistent local SQLite database.

Store, at minimum:

- local path;
- filename;
- SHA-256;
- file size;
- discovery time;
- status;
- retry count;
- next retry time;
- last attempt;
- upload completion time;
- WeddingHub media ID;
- last error.

Use states equivalent to:

```text
PENDING
UPLOADING
RETRY
UPLOADED
FAILED
```

On startup, recover safely from records left in `UPLOADING` after a crash.

### 4. SHA-256 / duplicate protection

Once the file is stable, calculate SHA-256.

Prevent duplicate local queue entries for the same content.

Do not use filename alone for deduplication.

### 5. WeddingHub API client

Implement the target API workflow described in `AGENT.md`:

```text
POST /api/photobooth/upload/init
binary upload to returned short-lived URL
POST /api/photobooth/upload/complete
```

Authenticate API control requests with:

```http
Authorization: Bearer <device-token>
```

The client must NOT submit or control the destination `wedding_id`.

Wedding resolution belongs to the WeddingHub backend using the device token.

If the WeddingHub backend endpoints do not exist yet, do NOT fake production behavior silently.

Instead:

- implement the client against the documented contract;
- implement a clean local mock/fake server for integration tests;
- clearly identify backend work still required.

If this repository also contains the WeddingHub backend and modifying it is explicitly in scope, implement the endpoints securely according to `AGENT.md`; otherwise keep backend changes out of this repository.

### 6. Retry / offline behavior

For connection errors, timeouts, HTTP 5xx and HTTP 429:

- retain the queue item;
- retry later;
- use exponential backoff with jitter;
- cap the delay;
- honor `Retry-After` for 429 when available.

For HTTP 401/403:

- retain the photo locally;
- clearly expose authentication/device failure;
- avoid hammering the API repeatedly.

For permanent invalid 4xx responses:

- record an actionable error;
- do not retry forever without reason.

### 7. Original files

Never delete, rename, resize or modify the original JPEG in V1.

### 8. Logging

Implement useful structured logs.

Log lifecycle events such as:

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

Never log the full device token or complete signed upload URLs.

### 9. CLI

Implement CLI commands equivalent to:

```bash
weddinghub-photobooth status
weddinghub-photobooth queue
weddinghub-photobooth retry
weddinghub-photobooth version
```

`status` should report useful health information without secrets.

### 10. systemd

Provide:

```text
systemd/weddinghub-photobooth-uploader.service
```

The service must:

- start at boot;
- restart on unexpected failure;
- run without root during normal operation where practical;
- access only the directories it needs.

Also provide installation instructions and preferably an installation helper script.

---

# Repository baseline

If missing, create:

```text
AGENT.md
README.md
CHANGELOG.md
VERSION
pyproject.toml
config.example.toml
src/weddinghub_photobooth/
tests/unit/
tests/integration/
systemd/
scripts/
```

Do not overwrite an existing `AGENT.md` with a simplified version.

---

# Versioning

Use Semantic Versioning.

For the initial implementation milestone, use:

```text
0.1.0
```

ONLY mark `0.1.0` as released when the coherent V1 milestone is actually functional and tested.

Until then, work under `[Unreleased]` in `CHANGELOG.md`.

Keep these synchronized:

- root `VERSION`;
- Python package version / project metadata;
- CLI `version` output.

Update `CHANGELOG.md` for every meaningful behavior change.

Do not create a new version for every small code edit.

---

# Tests required

Use automated tests only; no browser testing.

Implement tests covering at least:

1. new JPEG detection;
2. unsupported extension ignored;
3. growing file not queued prematurely;
4. stable file queued;
5. queue survives process restart;
6. SHA-256 duplicate detection;
7. successful API flow;
8. API idempotent `already_exists` result;
9. network timeout;
10. HTTP 500 retry;
11. HTTP 429 behavior;
12. HTTP 401/403 behavior;
13. permanent invalid 4xx behavior;
14. crash recovery from `UPLOADING`;
15. no original-file deletion/modification;
16. secrets not exposed by logging;
17. CLI status correctness.

Prefer local mocked HTTP endpoints for integration tests.

Do not repeatedly call the production WeddingHub API during development.

---

# Quality checks

At minimum execute, when configured by the project:

```text
pytest
ruff check .
mypy or pyright if configured
python package/build validation
```

Do not add browser/manual visual verification.

If a check is unavailable because the project does not use it, state that rather than installing arbitrary tooling without reason.

---

# Security constraints

Never:

- put a Supabase service-role key on the photobooth PC;
- put DB credentials in the client;
- disable TLS certificate verification;
- trust a client-provided wedding ID;
- log secrets;
- create shell commands from photo filenames;
- allow path traversal from remote/API data;
- commit a real device token.

Use server-side device-token-to-wedding resolution.

---

# What NOT to implement

Do not implement in V1:

- web GUI;
- photo gallery;
- image editing;
- resizing/thumbnails client-side;
- video uploads;
- HEIC/HEIF;
- automatic deletion;
- camera control;
- print control;
- Docker/Kubernetes/Redis/Celery;
- unrelated WeddingHub refactors.

Keep the solution small and robust.

---

# Deliverables

At the end of this task provide a concise engineering report containing:

## Files changed

List every relevant created/modified file.

## Architecture implemented

Briefly explain watcher, stability detection, queue, worker, API client, systemd and CLI.

## API contract

State exactly what WeddingHub endpoints/behavior the client expects and whether the real backend currently supports them.

## Tests

List commands executed and results.

## Version / changelog

State:

- current `VERSION`;
- changes added to `CHANGELOG.md`;
- whether this is still `[Unreleased]` or a completed release.

## Known limitations

List unresolved limitations honestly.

## Linux test procedure

Provide the exact minimal commands needed to install/run it on the target Linux photobooth PC and test with one local JPEG.

Do not require browser interaction.

## Next recommended milestone

Recommend the next smallest logical step after V1.
