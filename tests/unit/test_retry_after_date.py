"""Unit tests for Retry-After HTTP date parsing."""

from datetime import UTC, datetime

from weddinghub_photobooth.api import parse_retry_after


def test_parse_retry_after_http_date():
    # Delta seconds
    assert parse_retry_after("45") == 45.0
    
    # HTTP Date (RFC 7231)
    # E.g. Wed, 21 Oct 2026 07:28:00 GMT
    now = datetime.now(UTC)
    future = now.timestamp() + 3600
    
    # Simulate a response from 1 hour in the future
    import email.utils
    future_date_str = email.utils.formatdate(future, usegmt=True)
    
    delay = parse_retry_after(future_date_str)
    # Should be about 3600 seconds
    assert 3590 < delay < 3610

    # Invalid dates
    assert parse_retry_after("invalid") is None
