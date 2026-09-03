"""Data models for WeddingHub Photobooth Uploader."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class QueueStatus(str, Enum):
    PENDING = "PENDING"
    UPLOADING = "UPLOADING"
    RETRY = "RETRY"
    UPLOADED = "UPLOADED"
    FAILED = "FAILED"

    def is_active(self) -> bool:
        return self in (QueueStatus.PENDING, QueueStatus.UPLOADING, QueueStatus.RETRY)


@dataclass
class QueueItem:
    local_path: str
    filename: str
    sha256: str
    file_size: int
    created_at: str
    discovered_at: str
    status: QueueStatus = QueueStatus.PENDING
    attempt_count: int = 0
    next_attempt_at: str | None = None
    last_attempt_at: str | None = None
    uploaded_at: str | None = None
    remote_media_id: str | None = None
    last_error: str | None = None
    id: int | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any] | tuple[Any, ...]) -> "QueueItem":
        if isinstance(row, dict):
            return cls(
                id=row["id"],
                local_path=row["local_path"],
                filename=row["filename"],
                sha256=row["sha256"],
                file_size=row["file_size"],
                created_at=row["created_at"],
                discovered_at=row["discovered_at"],
                status=QueueStatus(row["status"]),
                attempt_count=row["attempt_count"],
                next_attempt_at=row["next_attempt_at"],
                last_attempt_at=row["last_attempt_at"],
                uploaded_at=row["uploaded_at"],
                remote_media_id=row["remote_media_id"],
                last_error=row["last_error"],
            )
        # Assuming standard column order from SELECT *
        return cls(
            id=row[0],
            local_path=row[1],
            filename=row[2],
            sha256=row[3],
            file_size=row[4],
            created_at=row[5],
            discovered_at=row[6],
            status=QueueStatus(row[7]),
            attempt_count=row[8],
            next_attempt_at=row[9],
            last_attempt_at=row[10],
            uploaded_at=row[11],
            remote_media_id=row[12],
            last_error=row[13],
        )


@dataclass
class InitUploadResponse:
    status: str  # "upload_required" or "already_exists"
    upload_id: str | None = None
    upload_url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    expires_at: str | None = None
    media_id: str | None = None


@dataclass
class CompleteUploadResponse:
    status: str  # "completed"
    media_id: str
