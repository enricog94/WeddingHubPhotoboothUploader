"""Unit tests for configuration loading and validation."""

from pathlib import Path

import pytest

from weddinghub_photobooth.config import Config, ConfigError, load_config


def test_load_valid_config(temp_dir: Path):
    config_file = temp_dir / "valid_config.toml"
    config_file.write_text(
        """
        api_base_url = "https://wedding.example.com"
        device_token = "secret-token-xyz-123456"
        watch_directory = "/tmp/photos"
        db_path = "/tmp/queue.db"
        stability_delay = 3.5
        upload_timeout = 45.0
        max_retries = 5
        retry_initial_delay = 15.0
        retry_max_delay = 1800.0
        retry_jitter_factor = 0.2
        log_level = "DEBUG"
        """,
        encoding="utf-8",
    )

    cfg = load_config(config_file)
    assert cfg.api_base_url == "https://wedding.example.com"
    assert cfg.device_token == "secret-token-xyz-123456"
    assert cfg.watch_directory == Path("/tmp/photos")
    assert cfg.db_path == Path("/tmp/queue.db")
    assert cfg.stability_delay == 3.5
    assert cfg.upload_timeout == 45.0
    assert cfg.max_retries == 5
    assert cfg.retry_initial_delay == 15.0
    assert cfg.retry_max_delay == 1800.0
    assert cfg.retry_jitter_factor == 0.2
    assert cfg.log_level == "DEBUG"


def test_missing_required_fields(temp_dir: Path):
    config_file = temp_dir / "missing_fields.toml"
    config_file.write_text(
        """
        api_base_url = "https://wedding.example.com"
        # missing device_token, watch_directory, db_path
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_file)
    assert "missing required fields" in str(exc_info.value)


def test_invalid_api_url(temp_dir: Path):
    config_file = temp_dir / "invalid_url.toml"
    config_file.write_text(
        """
        api_base_url = "ftp://invalid-url.com"
        device_token = "token123"
        watch_directory = "/tmp/photos"
        db_path = "/tmp/queue.db"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_file)
    assert "Must start with http:// or https://" in str(exc_info.value)


def test_device_token_masking():
    cfg = Config(
        api_base_url="https://example.com",
        device_token="abcdef1234567890xyz",
        watch_directory=Path("/tmp"),
        db_path=Path("/tmp/db.sqlite"),
    )
    masked = cfg.masked_device_token()
    assert "abcd" in masked
    assert "0xyz" in masked
    assert "12345678" not in masked
    assert "..." in masked

    safe_dict = cfg.safe_dict()
    assert safe_dict["device_token"] == masked


def test_load_config_from_env_var(temp_dir: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = temp_dir / "env_config.toml"
    config_file.write_text(
        """
        api_base_url = "https://env.example.com"
        device_token = "env-token-12345678"
        watch_directory = "/tmp/env-photos"
        db_path = "/tmp/env-queue.db"
        """,
        encoding="utf-8",
    )

    monkeypatch.setenv("WEDDINGHUB_CONFIG_PATH", str(config_file))
    cfg = load_config()
    assert cfg.api_base_url == "https://env.example.com"
    assert cfg.device_token == "env-token-12345678"
