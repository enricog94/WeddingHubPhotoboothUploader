"""Unit tests for file hashing and JPEG marker validation."""

import hashlib
from pathlib import Path

from weddinghub_photobooth.hashing import calculate_sha256, is_valid_jpeg


def test_calculate_sha256(temp_dir: Path):
    data = b"WeddingHub Photobooth Uploader Test Content" * 1000
    file_path = temp_dir / "sample.dat"
    file_path.write_bytes(data)

    expected = hashlib.sha256(data).hexdigest()
    computed = calculate_sha256(file_path, chunk_size=256)
    assert computed == expected


def test_is_valid_jpeg_success(temp_dir: Path, sample_jpeg_bytes: bytes):
    jpg_path = temp_dir / "valid.jpg"
    jpg_path.write_bytes(sample_jpeg_bytes)
    assert is_valid_jpeg(jpg_path) is True


def test_is_valid_jpeg_non_jpeg(temp_dir: Path):
    png_path = temp_dir / "test.png"
    png_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR...")
    assert is_valid_jpeg(png_path) is False


def test_is_valid_jpeg_truncated(temp_dir: Path, sample_jpeg_bytes: bytes):
    truncated_path = temp_dir / "truncated.jpg"
    # Cut off the last 2 bytes (EOI 0xFFD9)
    truncated_path.write_bytes(sample_jpeg_bytes[:-2])
    assert is_valid_jpeg(truncated_path) is False


def test_is_valid_jpeg_empty(temp_dir: Path):
    empty_path = temp_dir / "empty.jpg"
    empty_path.write_bytes(b"")
    assert is_valid_jpeg(empty_path) is False


def test_is_valid_jpeg_non_existent(temp_dir: Path):
    missing_path = temp_dir / "missing.jpg"
    assert is_valid_jpeg(missing_path) is False
