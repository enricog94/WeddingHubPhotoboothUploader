"""WeddingHub Photobooth Uploader

A reliable, offline-first background uploader for PhotoboothProject installations.
"""

from pathlib import Path

__all__ = ["__version__"]


def _get_version() -> str:
    version_file = Path(__file__).resolve().parent.parent.parent / "VERSION"
    if version_file.is_file():
        try:
            return version_file.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return "0.1.0"


__version__ = _get_version()


