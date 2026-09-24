"""Upload worker and retry backoff engine."""

import logging
import random
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .api import (
    AuthenticationError,
    PermanentApiError,
    RateLimitError,
    TransientApiError,
    WeddingHubApiClient,
)
from .config import Config
from .db import Database
from .hashing import calculate_sha256
from .logging_config import (
    EVENT_AUTH_ERROR,
    EVENT_UPLOAD_COMPLETED,
    EVENT_UPLOAD_FAILED,
    EVENT_UPLOAD_RETRY,
    EVENT_UPLOAD_STARTED,
    log_event,
)
from .models import QueueItem

logger = logging.getLogger("weddinghub_photobooth")


def calculate_backoff_delay(
    attempt_count: int,
    initial_delay: float = 30.0,
    max_delay: float = 3600.0,
    jitter_factor: float = 0.1,
) -> float:
    """Calculate exponential backoff with jitter."""
    # attempt_count 0 -> initial_delay * 1, attempt 1 -> 2x, etc.
    exponent = max(0, min(attempt_count, 15))
    base_delay = initial_delay * (2**exponent)
    capped_delay = min(base_delay, max_delay)

    # Apply symmetric jitter
    if jitter_factor > 0:
        jitter_range = capped_delay * jitter_factor
        jitter = random.uniform(-jitter_range, jitter_range)
        capped_delay = max(5.0, capped_delay + jitter)

    return capped_delay


class UploadWorker:
    """Processes queued photos sequentially with exponential backoff."""

    def __init__(
        self,
        config: Config,
        db: Database,
        api_client: WeddingHubApiClient | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self._custom_api_client = api_client
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background upload processing thread."""
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._run_loop,
            name="UploadWorker",
            daemon=True,
        )
        self._worker_thread.start()

    def stop(self) -> None:
        """Signal worker to stop and wait for current upload completion."""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10.0)

    def process_queue_once(self) -> int:
        """Process one pending or retry-ready item. Returns 1 if processed, 0 otherwise."""
        item = self.db.fetch_next_pending()
        if item is None:
            return 0
        api_client = self._get_api_client()
        try:
            self.process_item(item, api_client)
            return 1
        finally:
            if self._custom_api_client is None:
                api_client.close()

    def _get_api_client(self) -> WeddingHubApiClient:
        if self._custom_api_client is not None:
            return self._custom_api_client
        return WeddingHubApiClient(
            api_base_url=self.config.api_base_url,
            device_token=self.config.device_token,
            timeout=self.config.upload_timeout,
        )

    def process_item(self, item: QueueItem, api_client: WeddingHubApiClient) -> None:
        """Execute the upload flow for a single item."""
        local_path = Path(item.local_path)
        if not local_path.is_file():
            err_msg = f"Original file not found on disk: {local_path}"
            self.db.mark_failed(item.id, err_msg)
            log_event(
                logger,
                logging.ERROR,
                EVENT_UPLOAD_FAILED,
                f"Photo #{item.id} failed: {err_msg}",
            )
            return

        self.db.mark_uploading(item.id)
        short_sha = item.sha256[:10]
        log_event(
            logger,
            logging.INFO,
            EVENT_UPLOAD_STARTED,
            f"Starting upload for photo #{item.id}: {item.filename} (sha256:{short_sha})",
        )

        try:
            current_stat = local_path.stat()
            if current_stat.st_size != item.file_size:
                raise ValueError("size changed")
            current_sha = calculate_sha256(local_path)
            if current_sha != item.sha256:
                raise ValueError("content changed")
        except (OSError, ValueError):
            err_msg = "LOCAL_FILE_CHANGED"
            self.db.delete_observation(str(local_path))
            self.db.mark_failed(item.id, err_msg)
            log_event(
                logger,
                logging.ERROR,
                EVENT_UPLOAD_FAILED,
                f"Photo #{item.id} changed after enqueue. Marked FAILED: {err_msg}",
            )
            return

        try:
            # Step 1: Init upload
            init_resp = api_client.init_upload(
                filename=item.filename,
                sha256=item.sha256,
                size=item.file_size,
                content_type="image/jpeg",
                captured_at=item.created_at,
            )

            if init_resp.status == "already_exists":
                media_id = init_resp.media_id or "unknown"
                self.db.mark_uploaded(item.id, remote_media_id=media_id)
                log_event(
                    logger,
                    logging.INFO,
                    EVENT_UPLOAD_COMPLETED,
                    f"Photo #{item.id} already exists on WeddingHub (media_id:{media_id})",
                )
                return

            if init_resp.status == "upload_required":
                # Step 2: Binary PUT to signed URL
                api_client.upload_binary(
                    upload_url=init_resp.upload_url,
                    file_path=local_path,
                    headers=init_resp.headers,
                )

                # Step 3: Complete upload
                complete_resp = api_client.complete_upload(
                    upload_id=init_resp.upload_id,
                    sha256=item.sha256,
                )

                self.db.mark_uploaded(item.id, remote_media_id=complete_resp.media_id)
                log_event(
                    logger,
                    logging.INFO,
                    EVENT_UPLOAD_COMPLETED,
                    f"Successfully uploaded photo #{item.id} -> WeddingHub media_id:{complete_resp.media_id}",
                )
                return

        except RateLimitError as e:
            delay = e.retry_after or calculate_backoff_delay(
                item.attempt_count,
                self.config.retry_initial_delay,
                self.config.retry_max_delay,
                self.config.retry_jitter_factor,
            )
            self._handle_retry(item, delay, f"HTTP 429 Rate limited: {e}")

        except AuthenticationError as e:
            # Critical auth failure: log clearly and delay to avoid hammering
            log_event(
                logger,
                logging.ERROR,
                EVENT_AUTH_ERROR,
                f"Authentication failed for photo #{item.id}. Check device token configuration. Details: {e}",
            )
            self._handle_retry(
                item,
                self.config.auth_error_retry_delay,
                f"Authentication failure (401/403): {e}",
            )

        except TransientApiError as e:
            delay = calculate_backoff_delay(
                item.attempt_count,
                self.config.retry_initial_delay,
                self.config.retry_max_delay,
                self.config.retry_jitter_factor,
            )
            self._handle_retry(item, delay, f"Transient network/server error: {e}")

        except PermanentApiError as e:
            self.db.mark_failed(item.id, str(e))
            log_event(
                logger,
                logging.ERROR,
                EVENT_UPLOAD_FAILED,
                f"Photo #{item.id} permanently failed: {e}",
            )

        except Exception as e:
            logger.exception(f"Unexpected error during upload of photo #{item.id}")
            delay = calculate_backoff_delay(
                item.attempt_count,
                self.config.retry_initial_delay,
                self.config.retry_max_delay,
                self.config.retry_jitter_factor,
            )
            self._handle_retry(item, delay, f"Unexpected error: {e}")

    def _handle_retry(self, item: QueueItem, delay_seconds: float, error_msg: str) -> None:
        """Schedule item for future retry or mark failed if max_retries exceeded."""
        new_attempt = item.attempt_count + 1
        if self.config.max_retries > 0 and new_attempt >= self.config.max_retries:
            self.db.mark_failed(item.id, f"Max retries ({self.config.max_retries}) exceeded: {error_msg}")
            log_event(
                logger,
                logging.ERROR,
                EVENT_UPLOAD_FAILED,
                f"Photo #{item.id} exceeded max retries: {error_msg}",
            )
            return

        next_retry = datetime.now(UTC) + timedelta(seconds=delay_seconds)
        next_retry_iso = next_retry.isoformat()
        self.db.mark_retry(item.id, next_retry_iso, error_msg)
        log_event(
            logger,
            logging.WARNING,
            EVENT_UPLOAD_RETRY,
            f"Photo #{item.id} scheduled for retry in {delay_seconds:.1f}s (attempt {new_attempt}): {error_msg}",
        )

    def _run_loop(self) -> None:
        """Main upload loop."""
        api_client = self._get_api_client()
        try:
            while not self._stop_event.is_set():
                item = self.db.fetch_next_pending()
                if item is None:
                    # Sleep short time waiting for new items
                    if self._stop_event.wait(timeout=1.0):
                        break
                    continue

                self.process_item(item, api_client)
        finally:
            if self._custom_api_client is None:
                api_client.close()
