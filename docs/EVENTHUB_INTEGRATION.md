# EventHub integration contract

- Status: authoritative target contract for the uploader
- Uploader baseline reviewed: `d935fe191d83b2caa46c56d12962e6434c8cf9ff`
- EventHub code baseline reviewed: `c0a430cdd4d76877c9c334e58acc44f4c3d00feb`
- Deployment: out of scope until explicitly authorized

## 1. Context

The original uploader was designed against WeddingHub, where a device resolved to a
`wedding_id`. The active backend is now the Event-first EventHub platform.

The uploader remains a small Linux ingestion client. It must not become aware of EventHub
database IDs, Supabase credentials or administrative identities.

Authoritative server-side ownership is:

```text
device token
    -> photobooth device
    -> event_id
    -> Event
```

The client never sends `event_id`.

## 2. Existing EventHub capabilities that must be reused

The reviewed EventHub implementation already contains the media primitives required by a
Photobooth integration:

- Event-owned media through `event_id`;
- `media.source = 'photobooth'` as a recognized gallery category;
- `event_settings.photobooth_auto_approve`;
- R2 presigning helpers;
- the existing media preview enqueue/Jobs Worker pipeline;
- Event-scoped gallery publication;
- Web Worker + Jobs Worker separation;
- PostgreSQL through the existing worker database adapter.

The Photobooth backend must therefore be a thin authenticated ingestion adapter. It must
not introduce a second media table, preview generator, gallery implementation or storage
service.

## 3. Stable API paths

Keep the existing capability-oriented paths:

```text
GET  /api/photobooth/verify
POST /api/photobooth/upload/init
PUT  <presigned R2 URL>
POST /api/photobooth/upload/complete
```

These paths are not Wedding-specific and remain suitable for EventHub.

## 4. Device verification

### Request

```http
GET /api/photobooth/verify
Authorization: Bearer <device-token>
```

### Event-first response

```json
{
  "status": "ok",
  "device": {
    "name": "Photobooth principale",
    "enabled": true
  },
  "event": {
    "slug": "serena-enrico-2027",
    "name": "Serena & Enrico",
    "event_type": "wedding"
  }
}
```

New EventHub code must emit `event`, not `wedding`.

The uploader may temporarily accept the historical `wedding` object only inside a
small compatibility adapter so that the local reference server and older contract tests
can be migrated safely. New CLI/output semantics should be Event-first.

A successful CLI test must require a valid response contract. HTTP 200 with an empty or
incomplete JSON object is a failure.

Minimum required fields for success:

- `status == "ok"`;
- `device` is an object;
- `device.name` is a non-empty string;
- `event` is an object;
- `event.slug` is a non-empty string.

## 5. Initialize upload

### Request

```json
{
  "filename": "photo_000123.jpg",
  "sha256": "<64 lowercase hex>",
  "size": 1234567,
  "content_type": "image/jpeg",
  "captured_at": "2027-07-24T18:42:12+02:00"
}
```

`captured_at` is untrusted metadata. It never selects a tenant and does not participate
in authorization.

### New content response

```json
{
  "status": "upload_required",
  "upload_id": "<uuid>",
  "upload_url": "<short-lived presigned URL>",
  "headers": {},
  "expires_at": "<timestamp>"
}
```

### Existing content response

```json
{
  "status": "already_exists",
  "media_id": "<id>"
}
```

The backend should safely reuse a still-valid pending session for the same authenticated
device/Event/SHA where appropriate, issuing a fresh presigned URL for the same exact
storage key.

## 6. Binary upload

The uploader streams the JPEG directly to the presigned URL.

Required properties:

- no proxying of the full image body through EventHub;
- `Content-Type: image/jpeg`;
- explicit `Content-Length`;
- HTTPS for remote endpoints;
- a storage-side 403 remains retriable because the signed URL may have expired.

The presigned URL is ephemeral and must never be persisted in SQLite or logged with query
parameters.

## 7. Complete upload

### Request

```json
{
  "upload_id": "<uuid>",
  "sha256": "<same declared SHA-256>"
}
```

Before completion the backend must verify:

- authenticated device owns the session;
- authoritative Event matches the session;
- session is pending/usable and not expired;
- request SHA matches the session SHA;
- exact R2 object exists;
- exact object byte size matches the declared reservation.

The backend is not required to download and re-hash the whole R2 object. The SHA-256 is a
client-declared idempotency/content fingerprint; R2 existence and exact byte size are
server-verified.

A 410 expired session is retriable by the uploader: the next attempt starts again from
`/init`.

## 8. EventHub persistence target

New backend persistence is Event-first.

Conceptual device model:

```text
photobooth_devices
- id
- event_id
- name
- token_hash
- enabled
- created_at
- updated_at
- last_seen_at
```

Conceptual session model:

```text
photobooth_upload_sessions
- id UUID
- device_id
- event_id
- sha256
- filename
- size_bytes
- content_type
- storage_key
- status
- created_at
- expires_at
- completed_at
- media_id
```

The raw device token is never persisted. Server lookup hashes the presented bearer token
with SHA-256 and resolves the authoritative Event.

New EventHub tables/APIs must not use `wedding_id`.

## 9. Media creation

Completion creates or resolves exactly one existing Event-owned media row:

```text
event_id       = device.event_id
source         = photobooth
original_file  = client filename
original_key   = exact server-generated R2 key
mime_type      = image/jpeg
size_bytes     = verified reservation size
sha256         = declared fingerprint
status         = EventHub existing photobooth completion policy
```

EventHub already has the policy boundary:

```text
photobooth_auto_approve = true  -> approved
photobooth_auto_approve = false -> pending
```

After persistence, use the existing preview enqueueing path. Do not add client-side
thumbnail generation or Photobooth-specific server processing.

## 10. Idempotency

Required logical uniqueness:

```sql
(event_id, sha256) WHERE source = 'photobooth'
```

Expected behavior:

- repeated init after completed content -> `already_exists`;
- process crash/retry -> one media row;
- concurrent completes -> both resolve to the same media row;
- same SHA in different Events -> allowed.

## 11. Storage-key compatibility

The reviewed EventHub implementation still contains historical
`weddings/{slug}/...` R2 conventions in parts of its media pipeline.

The Photobooth integration must inspect and reuse the current EventHub key-generation and
cleanup conventions before choosing a prefix. Do not introduce a new
`events/{slug}/...` prefix merely for cosmetic terminology if the existing preview or
cleanup code would then diverge.

Changing logical terminology from Wedding to Event does not imply renaming existing R2
objects.

## 12. Security boundary

The Linux machine stores only its revocable device token.

It must not store:

- Supabase service-role keys;
- PostgreSQL credentials;
- organizer/admin sessions;
- Event IDs as authorization input.

The backend must fail closed for invalid/disabled devices and cross-device/session access.

## 13. What remains historical

The root `BACKEND_INTEGRATION.md` is the old WeddingHub contract and contains
`wedding_id`, `weddings` and a `wedding` verification object. It is useful history
but is not authoritative for new EventHub implementation.

Do not silently port that schema unchanged into EventHub.
