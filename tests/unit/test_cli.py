"""Unit tests for CLI commands."""

from pathlib import Path

import pytest

from weddinghub_photobooth import __version__
from weddinghub_photobooth.cli import (
    build_parser,
    cmd_queue,
    cmd_retry,
    cmd_status,
    cmd_test,
    cmd_version,
)
from weddinghub_photobooth.db import Database


def test_cli_version(capsys: pytest.CaptureFixture):
    parser = build_parser()
    args = parser.parse_args(["version"])
    code = cmd_version(args)
    assert code == 0
    captured = capsys.readouterr()
    assert f"weddinghub-photobooth {__version__}" in captured.out



def test_cli_status(temp_dir: Path, capsys: pytest.CaptureFixture):
    cfg_path = temp_dir / "config.toml"
    cfg_path.write_text(
        f"""
        api_base_url = "https://wedding.example.com"
        device_token = "secret123456789"
        watch_directory = "{temp_dir.as_posix()}/photos"
        db_path = "{temp_dir.as_posix()}/test.db"
        """,
        encoding="utf-8",
    )

    db = Database(temp_dir / "test.db")
    db.enqueue("/p/1.jpg", "1.jpg", "sha1", 100, "2026-09-02T20:00:00Z")

    parser = build_parser()
    args = parser.parse_args(["-c", str(cfg_path), "status"])
    code = cmd_status(args)
    assert code == 0

    captured = capsys.readouterr()
    assert "WeddingHub Photobooth Uploader Status" in captured.out
    assert "https://wedding.example.com" in captured.out
    assert "secr...6789" in captured.out
    assert "secret123456789" not in captured.out
    assert "Total Photos:       1" in captured.out
    assert "Pending:          1" in captured.out


def test_cli_queue(temp_dir: Path, capsys: pytest.CaptureFixture):
    cfg_path = temp_dir / "config.toml"
    cfg_path.write_text(
        f"""
        api_base_url = "https://wedding.example.com"
        device_token = "token12345"
        watch_directory = "{temp_dir.as_posix()}/photos"
        db_path = "{temp_dir.as_posix()}/test.db"
        """,
        encoding="utf-8",
    )

    db = Database(temp_dir / "test.db")
    db.enqueue("/p/img.jpg", "img.jpg", "shaX", 200, "2026-09-02T20:00:00Z")

    parser = build_parser()
    args = parser.parse_args(["-c", str(cfg_path), "queue"])
    code = cmd_queue(args)
    assert code == 0

    captured = capsys.readouterr()
    assert "STATUS" in captured.out
    assert "img.jpg" in captured.out
    assert "PENDING" in captured.out


def test_cli_retry(temp_dir: Path, capsys: pytest.CaptureFixture):
    cfg_path = temp_dir / "config.toml"
    cfg_path.write_text(
        f"""
        api_base_url = "https://wedding.example.com"
        device_token = "token12345"
        watch_directory = "{temp_dir.as_posix()}/photos"
        db_path = "{temp_dir.as_posix()}/test.db"
        """,
        encoding="utf-8",
    )

    db = Database(temp_dir / "test.db")
    item, _ = db.enqueue("/p/f.jpg", "f.jpg", "shaf", 100, "2026-09-02T20:00:00Z")
    db.mark_failed(item.id, "error")

    parser = build_parser()
    args = parser.parse_args(["-c", str(cfg_path), "retry"])
    code = cmd_retry(args)
    assert code == 0

    captured = capsys.readouterr()
    assert "Reset 1 item(s) to PENDING" in captured.out


def test_cli_test_success(temp_dir: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch):
    cfg_path = temp_dir / "config.toml"
    cfg_path.write_text(
        f"""
        api_base_url = "https://wedding.example.com"
        device_token = "token-pb-1"
        watch_directory = "{temp_dir.as_posix()}/photos"
        db_path = "{temp_dir.as_posix()}/test.db"
        """,
        encoding="utf-8",
    )

    from weddinghub_photobooth.api import WeddingHubApiClient

    def mock_verify_device(self):
        return {
            "status": "ok",
            "device": {
                "name": "Photobooth principale",
                "enabled": True,
            },
            "wedding": {
                "slug": "serena-enrico-2027",
                "display_name": "Serena & Enrico",
            },
        }

    monkeypatch.setattr(WeddingHubApiClient, "verify_device", mock_verify_device)

    parser = build_parser()
    args = parser.parse_args(["-c", str(cfg_path), "test"])
    code = cmd_test(args)
    assert code == 0

    captured = capsys.readouterr()
    assert "WeddingHub Photobooth Uploader" in captured.out
    assert "API.............. OK" in captured.out
    assert "Autenticazione... OK" in captured.out
    assert "Device........... Photobooth principale" in captured.out
    assert "Matrimonio....... Serena & Enrico" in captured.out
    assert "Wedding slug..... serena-enrico-2027" in captured.out
    assert "RESULT: OK" in captured.out


def test_cli_test_auth_failure(temp_dir: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch):
    cfg_path = temp_dir / "config.toml"
    cfg_path.write_text(
        f"""
        api_base_url = "https://wedding.example.com"
        device_token = "invalid-token"
        watch_directory = "{temp_dir.as_posix()}/photos"
        db_path = "{temp_dir.as_posix()}/test.db"
        """,
        encoding="utf-8",
    )

    from weddinghub_photobooth.api import AuthenticationError, WeddingHubApiClient

    def mock_verify_device(self):
        raise AuthenticationError("Invalid device token")

    monkeypatch.setattr(WeddingHubApiClient, "verify_device", mock_verify_device)

    parser = build_parser()
    args = parser.parse_args(["-c", str(cfg_path), "test"])
    code = cmd_test(args)
    assert code == 1

    captured = capsys.readouterr()
    assert "Autenticazione... FAIL" in captured.out
    assert "RESULT: FAIL" in captured.out
    assert "Authentication failed" in captured.err


def test_cli_test_disabled_device(temp_dir: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch):
    cfg_path = temp_dir / "config.toml"
    cfg_path.write_text(
        f"""
        api_base_url = "https://wedding.example.com"
        device_token = "token-disabled"
        watch_directory = "{temp_dir.as_posix()}/photos"
        db_path = "{temp_dir.as_posix()}/test.db"
        """,
        encoding="utf-8",
    )

    from weddinghub_photobooth.api import WeddingHubApiClient

    def mock_verify_device(self):
        return {
            "status": "ok",
            "device": {
                "name": "Photobooth Revoked",
                "enabled": False,
            },
            "wedding": {
                "slug": "serena-enrico-2027",
                "display_name": "Serena & Enrico",
            },
        }

    monkeypatch.setattr(WeddingHubApiClient, "verify_device", mock_verify_device)

    parser = build_parser()
    args = parser.parse_args(["-c", str(cfg_path), "test"])
    code = cmd_test(args)
    assert code == 1

    captured = capsys.readouterr()
    assert "Autenticazione... FAIL" in captured.out
    assert "RESULT: FAIL" in captured.out
    assert "Device is registered but currently disabled" in captured.err


