"""Unit tests for structured logging and secret masking."""

import io
import logging

from weddinghub_photobooth.logging_config import (
    EVENT_UPLOAD_STARTED,
    StructuredFormatter,
    configure_logging,
    log_event,
    mask_url_secrets,
)


def test_mask_url_secrets():
    raw_url = "https://s3.eu-central-1.amazonaws.com/wedding-photos/photo123.jpg?AWSAccessKeyId=AKIAIOSFODNN7EXAMPLE&Signature=vjbyPxybdZaNmGa%2ByT272YEAiv4%3D"
    masked = mask_url_secrets(raw_url)
    assert "AKIAIOSFODNN7EXAMPLE" not in masked
    assert "Signature=" not in masked
    assert "[SIGNED_PARAMS]" in masked
    assert masked.startswith("https://s3.eu-central-1.amazonaws.com/wedding-photos/photo123.jpg?")


def test_secret_sanitizing_in_logger():
    secret_token = "ultra-secret-device-token-12345"
    logger = configure_logging(level_name="INFO", token_to_mask=secret_token)

    # Capture log output
    stream = io.StringIO()
    test_handler = logging.StreamHandler(stream)
    test_handler.setFormatter(StructuredFormatter())
    for flt in logger.handlers[0].filters:
        test_handler.addFilter(flt)
    logger.addHandler(test_handler)

    try:
        # 1. Log with exact token
        logger.info(f"Connecting to backend using token: {secret_token}")
        # 2. Log with Bearer authorization header
        logger.info("Header: Bearer abcdef1234567890secretkey")
        # 3. Log with structured event
        log_event(
            logger,
            logging.INFO,
            EVENT_UPLOAD_STARTED,
            "Starting upload for photo #1 with url: https://storage.com/upload?X-Amz-Signature=secret123",
        )

        output = stream.getvalue()

        # Token must NOT be present in log output
        assert secret_token not in output
        assert "[DEVICE_TOKEN_MASKED]" in output

        # Bearer secret must NOT be present
        assert "abcdef1234567890secretkey" not in output
        assert "Bearer [MASKED]" in output

        # Signed URL query params must be masked
        assert "X-Amz-Signature=secret123" not in output
        assert "[SIGNED_PARAMS]" in output

        # Event marker must be present
        assert f"[{EVENT_UPLOAD_STARTED}]" in output
    finally:
        logger.removeHandler(test_handler)
