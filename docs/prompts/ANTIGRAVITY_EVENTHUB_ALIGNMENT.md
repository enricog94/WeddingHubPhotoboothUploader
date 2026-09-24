# Antigravity execution prompt — EventHub alignment + final uploader hardening

Read these files first and treat them as source of truth:

1. `AGENT.md`
2. `docs/README.md`
3. `docs/EVENTHUB_INTEGRATION.md`
4. `docs/FINAL_HARDENING_REVIEW.md`
5. `ARCHITECTURE.md`
6. `CHANGELOG.md`
7. `VERSION`

Then inspect the current implementation and tests before editing.

The task is to implement the uploader-side work defined by the two authoritative docs.
Do not redesign the project from chat history and do not copy the legacy
`BACKEND_INTEGRATION.md` Wedding schema into new code.

Required work:

- consume the Event-first `/api/photobooth/verify` contract;
- isolate any temporary legacy `wedding` compatibility in one tested adapter;
- make CLI verification strict and display Event semantics;
- persist local path/size/`mtime_ns` observation metadata so unchanged known JPEGs are
  skipped before stability checking and hashing, including after daemon restart;
- migrate existing 0.1.0 SQLite databases additively with no queue loss;
- replace unbounded thread-per-candidate stability checks with a small bounded worker pool;
- re-check queued file size and SHA immediately before upload and safely rediscover changed
  content instead of uploading under stale metadata;
- add Python >=3.11 / venv installer preflight without installing OS packages;
- require a real TOML boolean for `allow_insecure_http`;
- support numeric and HTTP-date `Retry-After`;
- clean machine-local documentation links;
- update architecture/changelog only where behavior actually changed.

Tests must specifically prove the scenarios listed in
`docs/FINAL_HARDENING_REVIEW.md`, including a 100+ JPEG second-scan test that performs
no repeated hashing for unchanged known files and a restart-persistence test.

Run:

```bash
uv run pytest
uv run ruff check .
git diff --check
```

Do not perform browser tests.

Do not deploy EventHub, apply remote migrations, provision a real device, push, tag, or
bump the version. Keep `VERSION = 0.1.0`.

At the end report in Italian:

- starting HEAD;
- files changed;
- SQLite migration strategy;
- watcher skip strategy;
- bounded-worker design;
- changed-file-before-upload behavior;
- Event-first verify adapter behavior;
- installer/config/retry changes;
- tests added and total result;
- ruff and diff-check result;
- CHANGELOG state;
- VERSION;
- remaining work before real Linux field test.

If implementation reveals a conflict with the authoritative docs, stop that portion,
report the exact conflict and propose the smallest documentation correction instead of
silently inventing behavior.
