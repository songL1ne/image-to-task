"""
test_task_parser.py — Unit tests for src/task_parser.py.

Covers JSON parsing, Pydantic validation, relative date resolution,
confidence-threshold routing, and the no-tasks path.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.extractor import ExtractionResult
from src.task_parser import ParsedImage, Task, parse_tasks
from src.utils import resolve_relative_date


# ── resolve_relative_date ─────────────────────────────────────────────────────

class TestResolveRelativeDate:
    REF = date(2025, 8, 1)  # Friday

    def test_iso_passthrough(self):
        assert resolve_relative_date("2025-09-15", self.REF) == "2025-09-15"

    def test_none_returns_none(self):
        assert resolve_relative_date(None, self.REF) is None

    def test_today(self):
        assert resolve_relative_date("today", self.REF) == "2025-08-01"

    def test_tomorrow(self):
        assert resolve_relative_date("tomorrow", self.REF) == "2025-08-02"

    def test_eod(self):
        assert resolve_relative_date("EOD", self.REF) == "2025-08-01"

    def test_next_monday(self):
        # REF is Friday (weekday 4), next Monday is +3 days
        assert resolve_relative_date("next Monday", self.REF) == "2025-08-04"

    def test_next_friday_same_weekday(self):
        # "next Friday" from a Friday should be 7 days ahead
        assert resolve_relative_date("next Friday", self.REF) == "2025-08-08"

    def test_next_week(self):
        # Monday of next calendar week
        assert resolve_relative_date("next week", self.REF) == "2025-08-04"

    def test_unknown_string_returned_as_is(self):
        assert resolve_relative_date("Q3 2025", self.REF) == "Q3 2025"


# ── Task Pydantic model ───────────────────────────────────────────────────────

class TestTaskModel:
    def test_valid_task(self):
        t = Task(title="Buy milk", confidence=0.9)
        assert t.priority == "medium"  # default
        assert t.labels == []

    def test_invalid_priority_coerced_to_medium(self):
        t = Task(title="Do thing", priority="CRITICAL", confidence=0.8)
        assert t.priority == "medium"

    def test_null_due_date_string_becomes_none(self):
        t = Task(title="Do thing", due_date="null", confidence=0.8)
        assert t.due_date is None

    def test_confidence_clamped(self):
        with pytest.raises(Exception):
            Task(title="X", confidence=1.5)


# ── parse_tasks ───────────────────────────────────────────────────────────────

_VALID_LLM_RESPONSE = json.dumps({
    "image_summary": "A to-do list with shopping and work items.",
    "image_type": "handwritten note",
    "tasks": [
        {
            "title": "Buy milk",
            "description": "Written at the top of the list.",
            "due_date": "next Friday",
            "priority": "low",
            "labels": ["shopping"],
            "project": "Personal",
            "confidence": 0.95,
        },
        {
            "title": "Send report to Alice",
            "description": "Marked as ASAP.",
            "due_date": None,
            "priority": "urgent",
            "labels": ["work"],
            "project": "Work",
            "confidence": 0.88,
        },
    ],
})

_EMPTY_TASKS_RESPONSE = json.dumps({
    "image_summary": "A landscape photo.",
    "image_type": "photograph",
    "tasks": [],
})


@pytest.fixture()
def extraction(tmp_dir) -> ExtractionResult:
    p = tmp_dir / "todo_list.png"
    p.write_bytes(b"fake")
    return ExtractionResult(
        raw_text="Buy milk\nSend report to Alice ASAP",
        image_type="handwritten note",
        method="llm",
        source_path=p,
    )


@patch("src.task_parser._call_llm_text", return_value=_VALID_LLM_RESPONSE)
@patch("src.task_parser.get_settings")
def test_parse_tasks_returns_tasks(mock_cfg, mock_llm, extraction):
    mock_cfg.return_value = MagicMock(
        llm=MagicMock(provider="openai", model="gpt-4o"),
        openai_api_key="k",
        tasks=MagicMock(confidence_threshold=0.6, needs_review_label="Needs Review"),
    )
    result = parse_tasks(extraction)
    assert isinstance(result, ParsedImage)
    assert len(result.tasks) == 2
    assert result.tasks[0].title == "Buy milk"
    assert result.tasks[1].priority == "urgent"


@patch("src.task_parser._call_llm_text", return_value=_EMPTY_TASKS_RESPONSE)
@patch("src.task_parser.get_settings")
def test_parse_tasks_no_tasks(mock_cfg, mock_llm, extraction):
    """Images with no actionable content return an empty tasks list."""
    mock_cfg.return_value = MagicMock(
        llm=MagicMock(provider="openai"),
        openai_api_key="k",
        tasks=MagicMock(confidence_threshold=0.6, needs_review_label="Needs Review"),
    )
    result = parse_tasks(extraction)
    assert result.tasks == []


@patch("src.task_parser._call_llm_text", return_value=_VALID_LLM_RESPONSE)
@patch("src.task_parser.get_settings")
def test_low_confidence_tasks_routed_to_needs_review(mock_cfg, mock_llm, extraction):
    """Tasks below the confidence threshold get the Needs Review label."""
    mock_cfg.return_value = MagicMock(
        llm=MagicMock(provider="openai"),
        openai_api_key="k",
        tasks=MagicMock(confidence_threshold=0.99, needs_review_label="Needs Review"),
    )
    result = parse_tasks(extraction)
    # All tasks have confidence < 0.99, so all should be routed
    for task in result.tasks:
        assert "Needs Review" in task.labels


@patch("src.task_parser._call_llm_text", side_effect=[
    "not valid json {{",   # first attempt fails
    _VALID_LLM_RESPONSE,   # retry succeeds
])
@patch("src.task_parser.get_settings")
def test_retry_on_invalid_json(mock_cfg, mock_llm, extraction):
    """parse_tasks retries once on JSON parse failure."""
    mock_cfg.return_value = MagicMock(
        llm=MagicMock(provider="openai"),
        openai_api_key="k",
        tasks=MagicMock(confidence_threshold=0.6, needs_review_label="Needs Review"),
    )
    result = parse_tasks(extraction)
    assert len(result.tasks) == 2


@patch("src.task_parser._call_llm_text", side_effect=["bad json", "also bad"])
@patch("src.task_parser.get_settings")
def test_returns_empty_after_two_failures(mock_cfg, mock_llm, extraction):
    """After two JSON failures, parse_tasks returns an empty ParsedImage."""
    mock_cfg.return_value = MagicMock(
        llm=MagicMock(provider="openai"),
        openai_api_key="k",
        tasks=MagicMock(confidence_threshold=0.6, needs_review_label="Needs Review"),
    )
    result = parse_tasks(extraction)
    assert result.tasks == []
