"""
test_storage.py — Unit tests for src/storage.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.storage import ImageStorage


@pytest.fixture()
def storage(tmp_dir) -> ImageStorage:
    return ImageStorage(tmp_dir / "test.db")


@pytest.fixture()
def fake_image(tmp_dir) -> Path:
    p = tmp_dir / "image.png"
    p.write_bytes(b"\x89PNG fake image data")
    return p


class TestImageStorage:
    def test_hash_file_is_deterministic(self, fake_image):
        h1 = ImageStorage.hash_file(fake_image)
        h2 = ImageStorage.hash_file(fake_image)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_hash_changes_with_content(self, tmp_dir):
        a = tmp_dir / "a.png"
        b = tmp_dir / "b.png"
        a.write_bytes(b"AAA")
        b.write_bytes(b"BBB")
        assert ImageStorage.hash_file(a) != ImageStorage.hash_file(b)

    def test_new_hash_is_not_duplicate(self, storage):
        assert not storage.is_duplicate("deadbeef" * 8)

    def test_recorded_hash_is_duplicate(self, storage, fake_image):
        sha = ImageStorage.hash_file(fake_image)
        storage.record(sha, fake_image, task_count=2, destination="notion")
        assert storage.is_duplicate(sha)

    def test_record_same_hash_twice_does_not_raise(self, storage, fake_image):
        sha = ImageStorage.hash_file(fake_image)
        storage.record(sha, fake_image, task_count=1)
        # Second insert should be silently ignored (ON CONFLICT DO NOTHING)
        storage.record(sha, fake_image, task_count=1)
        assert storage.is_duplicate(sha)

    def test_record_stores_metadata(self, storage, fake_image):
        sha = ImageStorage.hash_file(fake_image)
        storage.record(sha, fake_image, task_count=3, destination="notion", notes="ok")
        import sqlite3
        conn = sqlite3.connect(storage._db_path)
        row = conn.execute(
            "SELECT task_count, destination, notes FROM processed_images WHERE sha256=?",
            (sha,),
        ).fetchone()
        conn.close()
        assert row[0] == 3
        assert row[1] == "notion"
        assert row[2] == "ok"
