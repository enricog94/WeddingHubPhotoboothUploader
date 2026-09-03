"""Hashing and JPEG validation utilities."""

import hashlib
from pathlib import Path


def calculate_sha256(file_path: Path | str, chunk_size: int = 65536) -> str:
    """Calculate the SHA-256 hex digest of a file in chunks."""
    path = Path(file_path)
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def is_valid_jpeg(file_path: Path | str) -> bool:
    """Verify that a file exists, is non-empty, and has valid JPEG SOI and EOI markers."""
    path = Path(file_path)
    try:
        size = path.stat().st_size
        if size < 4:
            return False

        with open(path, "rb") as f:
            # Check Start Of Image (SOI) marker: 0xFF, 0xD8
            start = f.read(2)
            if start != b"\xff\xd8":
                return False

            # Check End Of Image (EOI) marker: 0xFF, 0xD9
            f.seek(-2, 2)
            end = f.read(2)
            if end != b"\xff\xd9":
                return False

        return True
    except (OSError, ValueError):
        return False
