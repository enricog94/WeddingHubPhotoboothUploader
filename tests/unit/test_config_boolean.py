"""Unit tests for robust config bool checking."""

from pathlib import Path

import pytest

from weddinghub_photobooth.config import ConfigError, load_config


def test_config_strict_boolean_type(temp_dir: Path):
    cfg_path = temp_dir / "config.toml"
    cfg_path.write_text(
        f"""
        api_base_url = "http://production.example.com"
        device_token = "abc12345"
        watch_directory = "{temp_dir.as_posix()}/photos"
        db_path = "{temp_dir.as_posix()}/test.db"
        allow_insecure_http = "true"  # STRING Instead of boolean
        """,
        encoding="utf-8",
    )
    
    with pytest.raises(ConfigError, match="must be a boolean, got str"):
        load_config(cfg_path)
