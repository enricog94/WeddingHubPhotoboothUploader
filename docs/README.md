# WeddingHub Photobooth Uploader — Documentation source of truth

This repository is the source of truth for the Linux Photobooth uploader.

The product/backend has evolved from the historical WeddingHub V1 model to the
Event-first EventHub platform. New uploader work must therefore be driven by the
documents in this directory rather than by old chat history or the legacy
`BACKEND_INTEGRATION.md` contract.

## Authority and precedence

For the current EventHub alignment and pre-field hardening work:

1. `AGENT.md` — persistent repository engineering rules.
2. `docs/EVENTHUB_INTEGRATION.md` — authoritative client/server contract against EventHub.
3. `docs/FINAL_HARDENING_REVIEW.md` — authoritative bounded reliability work before field test.
4. `ARCHITECTURE.md` — current uploader architecture; update it when implementation changes the resulting architecture.
5. `CHANGELOG.md` — unreleased operational/user-visible changes.
6. `docs/prompts/ANTIGRAVITY_EVENTHUB_ALIGNMENT.md` — thin execution prompt. It is not authoritative over the two documents above.

## Legacy documentation

`BACKEND_INTEGRATION.md` describes the previous WeddingHub-specific backend model using
`wedding_id` and a `wedding` verify payload. It is retained as implementation history,
but it is superseded for new work by `docs/EVENTHUB_INTEGRATION.md`.

The repository/package/CLI names remain historical for now:

- repository: `WeddingHubPhotoboothUploader`
- package/CLI: `weddinghub-photobooth`
- API client class: `WeddingHubApiClient`

Renaming is intentionally out of scope until after real Linux field verification.

## Current baselines reviewed

Uploader baseline:

```text
d935fe191d83b2caa46c56d12962e6434c8cf9ff
fix(review): standardize CLI syntax, presigned 403 & 410 retries,
streaming PUT, HTTPS enforcement, and PEP 668 venv installer
```

EventHub implementation baseline reviewed:

```text
c0a430cdd4d76877c9c334e58acc44f4c3d00feb
perf: collapse public content database queries
```

Later EventHub documentation-only commits do not change the backend implementation
observed for this review.

## Workflow

```text
repository docs
    -> implementation
    -> automated tests
    -> architecture/changelog update
    -> review
    -> real Linux field test
    -> only then 0.2.0
```

Do not deploy EventHub, apply remote migrations, provision a production device, or bump
the uploader version as part of ordinary implementation work.
