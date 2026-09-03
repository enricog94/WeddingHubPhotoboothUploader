"""Command line interface for WeddingHub Photobooth Uploader."""

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .api import (
    AuthenticationError,
    PermanentApiError,
    TransientApiError,
    WeddingHubApiClient,
    WeddingHubApiError,
)
from .config import ConfigError, load_config
from .db import Database
from .logging_config import (
    EVENT_SERVICE_STARTED,
    EVENT_SERVICE_STOPPED,
    configure_logging,
    log_event,
)
from .uploader import UploadWorker
from .watcher import PhotoWatcher

logger = logging.getLogger("weddinghub_photobooth")


def cmd_version(args: argparse.Namespace) -> int:
    """Print the version and exit."""
    print(f"weddinghub-photobooth {__version__}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Report health and queue status without exposing secrets."""
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    db = Database(config.db_path)
    stats = db.get_queue_stats()

    print("=== WeddingHub Photobooth Uploader Status ===")
    print(f"Version:            {__version__}")
    print(f"API Base URL:       {config.api_base_url}")
    print(f"Device Token:       {config.masked_device_token()}")
    print(f"Watched Directory:  {config.watch_directory}")
    print(f"Queue Database:     {config.db_path}")
    print("---------------------------------------------")
    print(f"Total Photos:       {stats['total']}")
    print(f"  Pending:          {stats['pending']}")
    print(f"  Uploading:        {stats['uploading']}")
    print(f"  Retry:            {stats['retry']}")
    print(f"  Uploaded:         {stats['uploaded']}")
    print(f"  Failed:           {stats['failed']}")
    print("---------------------------------------------")
    print(f"Last Upload:        {stats['last_uploaded_at'] or 'None'}")
    print(f"Last Error:         {stats['last_error'] or 'None'}")
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    """Inspect queue items."""
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    db = Database(config.db_path)
    items = db.list_queue(limit=args.limit, status_filter=args.status)

    if not items:
        print("Queue is empty.")
        return 0

    header = f"{'ID':<6} {'STATUS':<10} {'ATTEMPTS':<9} {'FILENAME':<30} {'ERROR / INFO':<30}"
    print(header)
    print("-" * len(header))
    for it in items:
        err = it.last_error or (f"media:{it.remote_media_id}" if it.remote_media_id else "")
        if len(err) > 28:
            err = err[:25] + "..."
        fname = it.filename
        if len(fname) > 28:
            fname = fname[:25] + "..."
        print(f"{it.id:<6} {it.status.value:<10} {it.attempt_count:<9} {fname:<30} {err:<30}")
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    """Reset retriable and failed items for immediate retry."""
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    db = Database(config.db_path)
    count = db.reset_retry_queue()
    print(f"Reset {count} item(s) to PENDING for immediate processing.")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    """Test connection to WeddingHub backend and verify device token validity."""
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"[FAIL] Configuration error: {e}", file=sys.stderr)
        return 1

    print("WeddingHub Photobooth Uploader\n")

    client = WeddingHubApiClient(
        api_base_url=config.api_base_url,
        device_token=config.device_token,
        timeout=config.upload_timeout,
    )

    try:
        data = client.verify_device()
        device_info = data.get("device", {})
        wedding_info = data.get("wedding", {})
        device_name = device_info.get("name", "Photobooth")
        wedding_slug = wedding_info.get("slug") or device_info.get("wedding_slug", "Unknown")
        display_name = wedding_info.get("display_name") or wedding_slug
        enabled = device_info.get("enabled", True)

        if not enabled:
            print("API.............. OK")
            print("Autenticazione... FAIL")
            print(f"Device........... {device_name}")
            print(f"Matrimonio....... {display_name}")
            print(f"Wedding slug..... {wedding_slug}")
            print("\nRESULT: FAIL")
            print("Reason: Device is registered but currently disabled in backend.", file=sys.stderr)
            return 1

        print("API.............. OK")
        print("Autenticazione... OK")
        print(f"Device........... {device_name}")
        print(f"Matrimonio....... {display_name}")
        print(f"Wedding slug..... {wedding_slug}")
        print("\nRESULT: OK")
        return 0
    except AuthenticationError as e:
        print("API.............. OK")
        print("Autenticazione... FAIL")
        print("\nRESULT: FAIL")
        print(f"Reason: Authentication failed - {e}", file=sys.stderr)
        return 1
    except TransientApiError as e:
        print("API.............. FAIL")
        print("\nRESULT: FAIL")
        print(f"Reason: Backend connection failed - {e}", file=sys.stderr)
        return 1
    except (PermanentApiError, WeddingHubApiError) as e:
        print("API.............. FAIL")
        print("\nRESULT: FAIL")
        print(f"Reason: Verification failed - {e}", file=sys.stderr)
        return 1

    finally:
        client.close()



def cmd_run(args: argparse.Namespace) -> int:
    """Run the background daemon service."""
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Fatal configuration error: {e}", file=sys.stderr)
        return 1

    # Setup structured logger
    main_logger = configure_logging(
        level_name=config.log_level,
        log_file=config.log_file,
        token_to_mask=config.device_token,
    )

    log_event(
        main_logger,
        logging.INFO,
        EVENT_SERVICE_STARTED,
        f"WeddingHub Photobooth Uploader v{__version__} starting. Watching: {config.watch_directory}",
    )

    db = Database(config.db_path)

    # Recover any crashed in-flight items
    recovered = db.recover_in_flight()
    if recovered > 0:
        main_logger.info(f"Recovered {recovered} in-flight photo(s) from previous crash to RETRY state.")

    # Initialize components
    watcher = PhotoWatcher(
        watch_directory=config.watch_directory,
        db=db,
        stability_delay=config.stability_delay,
        scan_interval=config.scan_interval,
    )
    worker = UploadWorker(config=config, db=db)

    # Clean shutdown handling
    shutdown_requested = False

    def handle_signal(signum: int, frame: Any) -> None:
        nonlocal shutdown_requested
        if not shutdown_requested:
            shutdown_requested = True
            log_event(
                main_logger,
                logging.INFO,
                EVENT_SERVICE_STOPPED,
                f"Received signal {signum}. Initiating graceful shutdown...",
            )
            watcher.stop()
            worker.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        watcher.start()
        worker.start()

        while not shutdown_requested:
            time.sleep(0.5)

    except KeyboardInterrupt:
        handle_signal(signal.SIGINT, None)
    finally:
        watcher.stop()
        worker.stop()
        log_event(main_logger, logging.INFO, EVENT_SERVICE_STOPPED, "Service stopped successfully.")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="weddinghub-photobooth",
        description="WeddingHub Photobooth Uploader CLI and background service",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Path to TOML configuration file",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # version
    subparsers.add_parser("version", help="Show uploader version")

    # status
    subparsers.add_parser("status", help="Show service and queue health")

    # queue
    queue_parser = subparsers.add_parser("queue", help="List queue items")
    queue_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max items to list (default 50)",
    )
    queue_parser.add_argument(
        "--status",
        type=str,
        default=None,
        help="Filter by status (PENDING, UPLOADING, RETRY, UPLOADED, FAILED)",
    )

    # retry
    subparsers.add_parser("retry", help="Reset failed/retrying photos for immediate retry")

    # test
    subparsers.add_parser("test", help="Verify backend connection and device token")

    # run
    subparsers.add_parser("run", help="Run uploader service in foreground")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        # Default to status if no command given
        args.command = "status"

    commands = {
        "version": cmd_version,
        "status": cmd_status,
        "queue": cmd_queue,
        "retry": cmd_retry,
        "test": cmd_test,
        "run": cmd_run,
    }

    handler = commands.get(args.command)
    if handler:
        sys.exit(handler(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
