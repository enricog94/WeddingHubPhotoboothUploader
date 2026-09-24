"""Directory watcher and file stability verification."""

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .db import Database
from .hashing import calculate_sha256, is_valid_jpeg
from .logging_config import (
    EVENT_PHOTO_DISCOVERED,
    EVENT_PHOTO_QUEUED,
    EVENT_PHOTO_STABLE,
    log_event,
)

logger = logging.getLogger("weddinghub_photobooth")

VALID_EXTENSIONS = {".jpg", ".jpeg"}


class PhotoHandler(FileSystemEventHandler):
    """Handles filesystem creation and modification events for photos."""

    def __init__(self, on_photo_event: Callable[[Path], None]) -> None:
        super().__init__()
        self.on_photo_event = on_photo_event

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            p = Path(event.src_path)
            if p.suffix.lower() in VALID_EXTENSIONS:
                self.on_photo_event(p)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            p = Path(event.src_path)
            if p.suffix.lower() in VALID_EXTENSIONS:
                self.on_photo_event(p)


class PhotoWatcher:
    """Watches the photo directory and validates file stability before enqueueing."""

    def __init__(
        self,
        watch_directory: Path | str,
        db: Database,
        stability_delay: float = 2.0,
        scan_interval: float = 10.0,
    ) -> None:
        self.watch_directory = Path(watch_directory)
        self.db = db
        self.stability_delay = max(0.5, stability_delay)
        self.scan_interval = max(1.0, scan_interval)
        self._stop_event = threading.Event()
        self._active_checks: set[Path] = set()
        self._active_checks_lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._observer: Observer | None = None
        self._scan_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start both real-time inotify observer and periodic fallback scanner."""
        self.watch_directory.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="PhotoWatcherWorker")

        # Run initial scan first to discover any existing files
        self.scan_directory()

        # Start watchdog observer
        event_handler = PhotoHandler(self.handle_photo_candidate)
        self._observer = Observer()
        self._observer.schedule(event_handler, str(self.watch_directory), recursive=False)
        self._observer.start()

        # Start periodic scan thread
        self._scan_thread = threading.Thread(
            target=self._scan_loop,
            name="PhotoWatcher-ScanLoop",
            daemon=True,
        )
        self._scan_thread.start()

    def stop(self) -> None:
        """Stop watcher and scanner threads."""
        self._stop_event.set()
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=5.0)
            except (RuntimeError, OSError) as e:
                logger.warning(f"Error stopping filesystem observer: {e}")
        if self._scan_thread and self._scan_thread.is_alive():
            self._scan_thread.join(timeout=5.0)

    def handle_photo_candidate(self, path: Path) -> None:
        """Trigger stability verification in a background thread."""
        resolved = path.resolve()

        try:
            stat = resolved.stat()
            if self.db.is_known_unchanged(str(resolved), stat.st_size, stat.st_mtime_ns):
                return
        except OSError:
            return

        with self._active_checks_lock:
            if resolved in self._active_checks:
                return
            self._active_checks.add(resolved)

        if self._executor and not self._stop_event.is_set():
            try:
                self._executor.submit(self._verify_and_enqueue, resolved)
            except RuntimeError:
                pass

    def _verify_and_enqueue(self, path: Path) -> None:
        """Verify file stability, inspect JPEG structure, hash, and persist in SQLite."""
        try:
            if not self._wait_for_stability(path):
                return

            # Compute SHA-256 hash
            sha256 = calculate_sha256(path)
            short_sha = sha256[:10]
            stat = path.stat()
            file_size = stat.st_size
            created_at = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()

            item, is_new = self.db.enqueue(
                local_path=str(path),
                filename=path.name,
                sha256=sha256,
                file_size=file_size,
                created_at=created_at,
            )

            if is_new:
                log_event(
                    logger,
                    logging.INFO,
                    EVENT_PHOTO_QUEUED,
                    f"Queued photo #{item.id}: {path.name} ({file_size} bytes, sha256:{short_sha})",
                )
            else:
                logger.debug(
                    f"Photo already queued (id #{item.id}, status {item.status.value}): {path.name} (sha256:{short_sha})"
                )

            # Save observation to skip future unneeded scans
            self.db.update_observation(str(path), file_size, stat.st_mtime_ns, sha256)
        except Exception:
            logger.exception(f"Error validating/enqueueing {path.name}")
        finally:
            with self._active_checks_lock:
                self._active_checks.discard(path)

    def _wait_for_stability(self, path: Path, max_attempts: int = 30) -> bool:
        """Verify size/mtime stability over stability_delay and valid JPEG markers."""
        if not path.is_file() or path.suffix.lower() not in VALID_EXTENSIONS:
            return False

        log_event(
            logger,
            logging.DEBUG,
            EVENT_PHOTO_DISCOVERED,
            f"Photo discovered: {path.name}",
        )

        attempts = 0
        while not self._stop_event.is_set() and attempts < max_attempts:
            attempts += 1
            try:
                if not path.exists():
                    return False

                initial_stat = path.stat()
                initial_size = initial_stat.st_size
                initial_mtime = initial_stat.st_mtime

                # If file is empty, it is definitely still being written or initialized
                if initial_size == 0:
                    if self._stop_event.wait(self.stability_delay):
                        return False
                    continue

                if self._stop_event.wait(self.stability_delay):
                    return False

                if not path.exists():
                    return False

                second_stat = path.stat()
                second_size = second_stat.st_size
                second_mtime = second_stat.st_mtime

                if initial_size == second_size and initial_mtime == second_mtime:
                    # File size and mtime are stable. Now verify JPEG header/trailer
                    if is_valid_jpeg(path):
                        log_event(
                            logger,
                            logging.INFO,
                            EVENT_PHOTO_STABLE,
                            f"Photo stable: {path.name} ({second_size} bytes)",
                        )
                        return True
                    else:
                        logger.debug(f"File {path.name} is stable in size but invalid JPEG markers. Retrying...")
            except (OSError, FileNotFoundError):
                pass

        if attempts >= max_attempts:
            logger.warning(f"File {path.name} did not stabilize after {max_attempts} attempts.")
        return False

    def scan_directory(self) -> None:
        """Scan the watch directory for any unqueued JPEG files."""
        if not self.watch_directory.is_dir():
            return
        try:
            for entry in self.watch_directory.iterdir():
                if entry.is_file() and entry.suffix.lower() in VALID_EXTENSIONS:
                    self.handle_photo_candidate(entry)
        except OSError as e:
            logger.error(f"Error during directory scan of {self.watch_directory}: {e}")

    def _scan_loop(self) -> None:
        """Periodic background scanner loop."""
        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self.scan_interval):
                break
            self.scan_directory()
