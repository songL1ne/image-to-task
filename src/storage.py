"""
storage.py — SQLite-backed image deduplication and run log.

Uses the standard library ``sqlite3`` only — no ORM dependency.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .utils import get_logger

log = get_logger("storage")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_images (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256        TEXT    NOT NULL UNIQUE,
    file_path     TEXT    NOT NULL,
    processed_at  TEXT    NOT NULL,
    task_count    INTEGER NOT NULL DEFAULT 0,
    destination   TEXT,
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS idx_sha256 ON processed_images (sha256);
"""


class ImageStorage:
    """Manages the SQLite database for image deduplication and audit log."""

    def __init__(self, db_path: Path) -> None:
        """
        Initialise the storage layer.

        Args:
            db_path: Path to the SQLite database file. Created if absent.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._init_db()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def hash_file(path: Path) -> str:
        """Return the SHA-256 hex digest of a file."""
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def is_duplicate(self, sha256: str) -> bool:
        """Return True if this hash has already been processed."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_images WHERE sha256 = ?", (sha256,)
            ).fetchone()
        return row is not None

    def record(
        self,
        sha256: str,
        file_path: Path,
        task_count: int,
        destination: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """
        Persist a processed-image record.

        Args:
            sha256:       Hex digest of the image file.
            file_path:    Original path of the image.
            task_count:   Number of tasks created from this image.
            destination:  Comma-separated destination names (e.g. "notion").
            notes:        Optional free-text notes (e.g. error message).
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO processed_images
                    (sha256, file_path, processed_at, task_count, destination, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(sha256) DO NOTHING
                """,
                (
                    sha256,
                    str(file_path),
                    datetime.now(timezone.utc).isoformat(),
                    task_count,
                    destination,
                    notes,
                ),
            )
        log.debug("Recorded image %s (%d tasks)", sha256[:12], task_count)
