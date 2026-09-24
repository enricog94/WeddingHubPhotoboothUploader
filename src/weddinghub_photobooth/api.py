"""WeddingHub Photobooth API client implementing the upload contract."""

from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx

from .models import CompleteUploadResponse, InitUploadResponse


class WeddingHubApiError(Exception):
    """Base exception for all API errors."""


class TransientApiError(WeddingHubApiError):
    """Retriable error: connection error, timeout, 5xx server error."""


class RateLimitError(TransientApiError):
    """429 Too Many Requests, may carry a retry_after delay in seconds."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AuthenticationError(WeddingHubApiError):
    """401 Unauthorized or 403 Forbidden device authentication error."""


class PermanentApiError(WeddingHubApiError):
    """4xx client error (other than 401/403/429) that should not be retried infinitely."""


def parse_retry_after(header_value: str | None) -> float | None:
    """Parse HTTP Retry-After header as seconds if present."""
    if not header_value:
        return None
    try:
        val = float(header_value)
        return max(0.0, val)
    except ValueError:
        from datetime import UTC, datetime
        from email.utils import parsedate_to_datetime

        try:
            dt = parsedate_to_datetime(header_value)
            now = datetime.now(UTC)
            diff = (dt - now).total_seconds()
            return max(0.0, diff)
        except (TypeError, ValueError):
            return None


def normalize_verify_response(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy 'wedding' payloads to Event-first 'event' payload."""
    if "wedding" in data and "event" not in data:
        w = data["wedding"]
        data["event"] = {
            "slug": w.get("slug", ""),
            "name": w.get("display_name", ""),
            "event_type": "wedding",
        }
    return data


class WeddingHubApiClient:
    """Client for the WeddingHub Photobooth upload workflow."""

    def __init__(
        self,
        api_base_url: str,
        device_token: str,
        timeout: float = 30.0,
        verify_tls: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.device_token = device_token
        self.timeout = timeout
        self.verify_tls = verify_tls
        self._client = client or httpx.Client(timeout=timeout, verify=verify_tls)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.device_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _handle_response_status(self, response: httpx.Response, action_name: str) -> None:
        """Translate HTTP status codes to specific domain exceptions."""
        status = response.status_code
        if 200 <= status < 300:
            return

        body_preview = response.text[:250] if response.text else ""
        error_msg = f"{action_name} failed: HTTP {status} - {body_preview}"

        if status == 429:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            raise RateLimitError(error_msg, retry_after=retry_after)
        if status in (401, 403):
            raise AuthenticationError(error_msg)
        if status == 410:
            # Session expired (e.g. upload took longer than TTL or backend cleaned up session).
            # This is recoverable: the client transitions the item to RETRY so it can start fresh from /init.
            raise TransientApiError(error_msg)
        if 500 <= status < 600:
            raise TransientApiError(error_msg)
        if 400 <= status < 500:
            raise PermanentApiError(error_msg)

        raise WeddingHubApiError(error_msg)

    def init_upload(
        self,
        filename: str,
        sha256: str,
        size: int,
        content_type: str = "image/jpeg",
        captured_at: str | None = None,
    ) -> InitUploadResponse:
        """Step 1: Request upload session or detect existing media."""
        url = f"{self.api_base_url}/api/photobooth/upload/init"
        payload = {
            "filename": filename,
            "sha256": sha256,
            "size": size,
            "content_type": content_type,
            "captured_at": captured_at,
        }

        try:
            resp = self._client.post(url, json=payload, headers=self._auth_headers())
            self._handle_response_status(resp, "init_upload")
            data = resp.json()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            raise TransientApiError(f"init_upload network error: {e}") from e
        except ValueError as e:
            raise PermanentApiError(f"Invalid JSON returned from init_upload: {e}") from e

        resp_status = data.get("status")
        if resp_status == "already_exists":
            media_id = data.get("media_id")
            if not media_id:
                raise PermanentApiError("init_upload returned status 'already_exists' without media_id")
            return InitUploadResponse(status="already_exists", media_id=str(media_id))

        if resp_status == "upload_required":
            upload_id = data.get("upload_id")
            upload_url = data.get("upload_url")
            if not upload_id or not upload_url:
                raise PermanentApiError(
                    "init_upload returned 'upload_required' without upload_id or upload_url"
                )
            return InitUploadResponse(
                status="upload_required",
                upload_id=str(upload_id),
                upload_url=str(upload_url),
                headers=data.get("headers", {}),
                expires_at=data.get("expires_at"),
            )

        raise PermanentApiError(f"Unknown status '{resp_status}' in init_upload response")

    def upload_binary(
        self,
        upload_url: str,
        file_path: Path | str,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Step 2: Stream binary photo to the short-lived presigned upload URL."""
        path = Path(file_path)
        if not path.is_file():
            raise PermanentApiError(f"Local file does not exist: {path}")

        file_size = path.stat().st_size
        req_headers = {
            "Content-Type": "image/jpeg",
            "Content-Length": str(file_size),
        }
        if headers:
            req_headers.update(headers)

        try:
            with open(path, "rb") as f:
                resp = self._client.put(
                    upload_url,
                    content=f,
                    headers=req_headers,
                )
            # Binary upload endpoint may return 200 or 201 or 204
            if not (200 <= resp.status_code < 300):
                if resp.status_code == 403:
                    # Presigned URL may have expired or access was denied by storage.
                    # This must be treated as retriable (not permanent file error) so that
                    # the uploader retries and requests a fresh presigned URL from /init.
                    raise TransientApiError(
                        f"Presigned upload URL expired or rejected (HTTP 403): {resp.text[:200]}"
                    )
                if 500 <= resp.status_code < 600:
                    raise TransientApiError(
                        f"Binary upload server error: HTTP {resp.status_code}"
                    )
                if resp.status_code == 429:
                    raise RateLimitError(
                        "Binary upload rate limited",
                        retry_after=parse_retry_after(resp.headers.get("Retry-After")),
                    )
                raise PermanentApiError(
                    f"Binary upload failed: HTTP {resp.status_code} - {resp.text[:200]}"
                )
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            raise TransientApiError(f"Binary upload network error: {e}") from e

    def complete_upload(self, upload_id: str, sha256: str) -> CompleteUploadResponse:
        """Step 3: Notify WeddingHub backend that binary upload has finished."""
        url = f"{self.api_base_url}/api/photobooth/upload/complete"
        payload = {
            "upload_id": upload_id,
            "sha256": sha256,
        }

        try:
            resp = self._client.post(url, json=payload, headers=self._auth_headers())
            self._handle_response_status(resp, "complete_upload")
            data = resp.json()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            raise TransientApiError(f"complete_upload network error: {e}") from e
        except ValueError as e:
            raise PermanentApiError(f"Invalid JSON returned from complete_upload: {e}") from e

        if data.get("status") != "completed" or not data.get("media_id"):
            raise PermanentApiError(
                f"complete_upload response missing 'completed' status or media_id: {data}"
            )

        return CompleteUploadResponse(status="completed", media_id=str(data["media_id"]))

    def verify_device(self) -> dict[str, Any]:
        """Verify device token validity and backend reachability via GET /api/photobooth/verify."""
        url = f"{self.api_base_url}/api/photobooth/verify"
        try:
            resp = self._client.get(url, headers=self._auth_headers())
            self._handle_response_status(resp, "verify_device")
            data = resp.json()
            return normalize_verify_response(data)
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            raise TransientApiError(
                f"Connection error to WeddingHub API at {self.api_base_url}: {e}"
            ) from e
        except ValueError as e:
            raise PermanentApiError(f"Invalid JSON response from verify endpoint: {e}") from e

