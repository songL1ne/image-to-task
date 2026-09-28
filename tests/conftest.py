"""
conftest.py — Shared fixtures for the image-to-task test suite.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.config import load_settings, Settings
from src.extractor import ExtractionResult
from src.task_parser import Task, ParsedImage


# ── Settings fixture ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Clear the lru_cache on load_settings between tests."""
    load_settings.cache_clear()
    yield
    load_settings.cache_clear()


@pytest.fixture()
def tmp_dir(tmp_path: Path) -> Path:
    """Return a fresh temporary directory."""
    return tmp_path


@pytest.fixture()
def sample_extraction() -> ExtractionResult:
    return ExtractionResult(
        raw_text=(
            "TODO:\n"
            "- Buy milk by Friday\n"
            "- Send report to Alice ASAP\n"
            "- Book dentist appointment next week"
        ),
        image_type="handwritten note",
        method="llm",
        source_path=Path("tests/images/todo_list.png"),
    )


@pytest.fixture()
def sample_task() -> Task:
    return Task(
        title="Buy milk",
        description="From the to-do list image.",
        due_date="2025-08-01",
        priority="medium",
        labels=["shopping"],
        project="Personal",
        confidence=0.9,
    )


@pytest.fixture()
def parsed_image(sample_task) -> ParsedImage:
    return ParsedImage(
        image_summary="A handwritten to-do list with shopping and work tasks.",
        image_type="handwritten note",
        tasks=[sample_task],
    )
