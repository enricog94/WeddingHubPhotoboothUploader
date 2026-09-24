# Final pre-field hardening review

- Status: authoritative implementation work before real Linux field test
- Baseline: `d935fe191d83b2caa46c56d12962e6434c8cf9ff`
- Version during this work: keep `0.1.0`
- Scope: reliability and EventHub contract alignment only

This review is based on the actual code currently on `main`, not on the earlier V1
engineering report.

## P0 — periodic scanner repeats expensive work

### Current behavior

`PhotoWatcher.scan_directory()` calls `handle_photo_candidate()` for every JPEG on every
fallback scan.

`handle_photo_candidate()` suppresses only paths currently present in
`_active_checks`.

After `_verify_and_enqueue()` completes, the path is removed from `_active_checks`.
On a later scan the same unchanged JPEG is therefore stability-checked, read and SHA-256
hashed again before SQLite deduplication discovers the existing SHA.

This is functionally correct but operationally wrong for a long-running booth.

With hundreds or thousands of JPEGs, every periodic scan can generate avoidable file I/O,
hashing, threads and database lookups on the same machine responsible for camera preview
and printing.

### Required behavior

Persist enough observation metadata to skip a known unchanged path before stability
checking and hashing.

Recommended persisted identity:

```text
local_path
observed_size
observed_mtime_ns
sha256
```

An unchanged known path:

```text
same local_path
+ same size
+ same mtime_ns
=> skip immediately
```

A changed path:

```text
same path
+ different size or mtime_ns
=> validate again
```

This optimization must survive daemon restart.

Do not replace SHA-256 content deduplication. Local observation metadata is a fast
pre-filter; SHA remains the content identity.

### Implementation direction

Keep all persistence inside the existing SQLite `queue.db`, but **do not store path
observation state on `upload_queue` itself**.

`upload_queue` is content-oriented and enforces one row per SHA-256. Two different local
paths can legitimately contain identical bytes and therefore collapse to the same queue
row. If path/size/mtime observation metadata lives on that row, only one of those paths
can be represented correctly and the other path can be re-hashed forever.

Use a dedicated additive table in the same database, conceptually:

```sql
CREATE TABLE IF NOT EXISTS file_observations (
    local_path TEXT PRIMARY KEY,
    observed_size INTEGER NOT NULL,
    observed_mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
```

The exact column names may differ, but the ownership must remain path-oriented.

Required behavior:

- query `file_observations` by canonical/resolved `local_path`;
- same size + same `mtime_ns` => skip immediately;
- changed size or `mtime_ns` => perform normal stability/JPEG/SHA processing;
- after a stable file has been hashed/enqueued (including a duplicate SHA already present
  in `upload_queue`), upsert that path's observation row;
- clearing one path observation must not mutate or invalidate the content queue row or
  observations for other paths.

This is an additive SQLite schema migration: existing `upload_queue` data and state stay
untouched. A pre-hardening 0.1.0 database may perform one normal re-evaluation of existing
files after upgrade to populate `file_observations`; subsequent scans and restarts must
use the fast path.

Do not drop/recreate `upload_queue`.

### Required tests

- first scan hashes/queues a new file;
- second scan of unchanged file performs no second SHA calculation;
- 100+ known unchanged JPEGs are skipped without hashing on the second scan;
- same path with changed size is reconsidered;
- same path with same size but changed `mtime_ns` is reconsidered;
- close/reopen `Database` and create a new `PhotoWatcher`: unchanged known files remain skipped;
- existing 0.1.0-style database upgrades without data loss.

## P0 — unbounded thread-per-candidate model

### Current behavior

Every accepted candidate creates:

```python
threading.Thread(...).start()
```

The `_active_checks` set suppresses duplicate checks for the same path but does not bound
the number of different paths processed concurrently.

An initial scan of a large directory can therefore create hundreds of stability threads.

### Required behavior

Use bounded stability work.

Preferred implementation:

- `ThreadPoolExecutor` with a small fixed `max_workers`; or
- one internal candidate queue with a fixed number of worker threads.

Default target: 2–4 stability workers.

Do not make binary uploads concurrent; the upload worker remains sequential.

Shutdown must stop accepting new work and cleanly drain/cancel bounded stability work
without hanging systemd.

### Required tests

- many candidates never exceed configured worker concurrency;
- duplicate events for the same path still schedule one active validation;
- watcher shutdown completes cleanly.

## P1 — strict verify contract

### Current behavior

`cmd_test()` uses permissive fallbacks:

- device name defaults to `Photobooth`;
- wedding/event identity can become `Unknown`;
- missing enabled value defaults to `True`.

A malformed HTTP 200 response can therefore look healthy.

### Required behavior

Consume the EventHub response defined in `docs/EVENTHUB_INTEGRATION.md`.

Success requires:

- `status == "ok"`;
- valid `device.name`;
- valid `event.slug`.

The CLI should say `Evento`, not assume every target is a Wedding.

Temporary legacy `wedding` compatibility may exist only inside one response-normalizing
adapter and must have explicit tests.

### Required tests

- `{}` -> failure;
- missing device -> failure;
- empty device name -> failure;
- missing event -> failure;
- missing event slug -> failure;
- non-ok status -> failure;
- valid EventHub payload -> success;
- explicit legacy payload compatibility -> success only if intentionally retained.

## P1 — queued fingerprint can diverge from uploaded bytes

### Current behavior

The queue stores SHA-256 and size at discovery time, but `UploadWorker.process_item()`
later uploads whatever bytes currently exist at `local_path`.

The init request still sends the original queued `sha256` and `file_size`.

If a file changes after enqueue, the bytes sent to R2 may no longer match the declared
fingerprint. A size change is likely detected by backend completion; a same-size content
change is not, because the backend deliberately does not re-hash the full R2 object.

### Required behavior

Immediately before calling `init_upload`/PUT:

1. stat the file;
2. require current size == queued size;
3. calculate SHA-256;
4. require current SHA == queued SHA.

Do not upload changed bytes under the old fingerprint.

Changed content must not be lost. On a mismatch, mark the stale queue item with an
explicit terminal error such as `LOCAL_FILE_CHANGED`, delete only that path's observation
row, and do not call init/PUT. The periodic scanner/watchdog can then rediscover the
current bytes and enqueue them under their real SHA. If those bytes already exist by SHA,
normal queue deduplication applies and the path observation must still be recorded.

Avoid direct coupling from UploadWorker back into PhotoWatcher and avoid endless loops when
software repeatedly mutates the same file.

### Required tests

- unchanged file uploads normally;
- changed-size file is not uploaded using old metadata;
- same-size changed-content file is not uploaded using old SHA;
- current changed content can subsequently be discovered/queued;
- original file remains untouched.

## P1 — Linux installer preflight

Before creating the venv, `scripts/install.sh` must explicitly verify:

- `python3` exists;
- runtime version is >= 3.11;
- `python3 -m venv` is usable.

If venv support is unavailable, fail with an actionable Debian/Ubuntu hint such as:

```text
sudo apt install python3-venv
```

Do not automatically install OS packages.

Do not overwrite existing config or queue DB.

## P2 — strict TOML boolean

Current code uses:

```python
bool(data.get("allow_insecure_http", False))
```

A TOML string such as:

```toml
allow_insecure_http = "false"
```

is truthy in Python.

Require the parsed value to be an actual `bool`, otherwise raise `ConfigError`.

## P2 — Retry-After HTTP-date

`parse_retry_after()` currently accepts numeric delta-seconds only.

Support both HTTP forms:

```text
Retry-After: 120
Retry-After: Wed, 24 Sep 2026 12:00:00 GMT
```

HTTP dates in the past may return 0. Invalid values return `None` and fall back to the
normal exponential backoff.

Use deterministic tests by injecting or passing the reference time rather than depending
on wall-clock timing.

## P3 — repository documentation hygiene

The README currently contains local Windows `file:///c:/...` documentation links.
Replace them with repository-relative Markdown links.

Search committed docs for other machine-local paths that are not intentionally shown as
example installation paths.

## Explicit non-goals

Do not use this hardening pass to add:

- a GUI;
- video/HEIC;
- image resizing on the client;
- upload concurrency;
- local cleanup/deletion;
- PhotoboothProject control;
- package/repository branding rename;
- production deployment.

## Verification gate

Run:

```bash
uv run pytest
uv run ruff check .
git diff --check
```

No browser tests.

Keep `VERSION = 0.1.0`.

Record meaningful changes under `CHANGELOG.md -> [Unreleased]`.

## Field-test gate

Do not release 0.2.0 until the real Linux machine proves:

- systemd starts at boot;
- EventHub device verification succeeds;
- a real PhotoboothProject JPEG is detected automatically;
- R2 upload succeeds;
- EventHub creates one `source=photobooth` media row;
- preview processing succeeds;
- gallery behavior matches `photobooth_auto_approve`;
- network outage preserves/retries queue;
- crash/reboot recovery creates no duplicates;
- local original remains unchanged.
