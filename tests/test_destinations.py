"""
test_destinations.py — Unit tests for Todoist and Notion destination adapters.

All external API calls are mocked — no real credentials needed.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.task_parser import Task
from src.destinations.base import TaskResult


@pytest.fixture()
def sample_task() -> Task:
    return Task(
        title="Send weekly report",
        description="From the whiteboard photo.",
        due_date="2025-08-08",
        priority="high",
        labels=["work"],
        project="Work",
        confidence=0.92,
    )


# ── Notion ────────────────────────────────────────────────────────────────────

class TestNotionDestination:
    @patch("src.destinations.notion.get_settings")
    def test_create_task_success(self, mock_cfg, sample_task):
        mock_cfg.return_value = MagicMock(
            notion_token="secret-token",
            notion_database_id="db-123",
            tasks=MagicMock(confidence_threshold=0.6, needs_review_label="Needs Review"),
            destinations=MagicMock(
                notion=MagicMock(status_not_started="Not started")
            ),
        )

        fake_page = {"id": "page-abc", "url": "https://notion.so/page-abc"}

        with patch("src.destinations.notion.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.pages.create.return_value = fake_page
            mock_client_cls.return_value = mock_client

            from src.destinations.notion import NotionDestination
            dest = NotionDestination()
            result = dest.create_task(sample_task)

        assert result.success is True
        assert result.external_id == "page-abc"
        assert result.url == "https://notion.so/page-abc"

    @patch("src.destinations.notion.get_settings")
    def test_create_task_api_error_returns_failed_result(self, mock_cfg, sample_task):
        mock_cfg.return_value = MagicMock(
            notion_token="token",
            notion_database_id="db-123",
            tasks=MagicMock(confidence_threshold=0.6),
            destinations=MagicMock(
                notion=MagicMock(status_not_started="Not started")
            ),
        )

        with patch("src.destinations.notion.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.pages.create.side_effect = Exception("API rate limit")
            mock_client_cls.return_value = mock_client

            from src.destinations.notion import NotionDestination
            dest = NotionDestination()
            result = dest.create_task(sample_task)

        assert result.success is False
        assert "rate limit" in result.error

    @patch("src.destinations.notion.get_settings")
    def test_missing_token_raises(self, mock_cfg):
        mock_cfg.return_value = MagicMock(notion_token=None, notion_database_id="x")
        with pytest.raises(ValueError, match="NOTION_TOKEN"):
            from src.destinations.notion import NotionDestination
            NotionDestination()

    @patch("src.destinations.notion.get_settings")
    def test_missing_database_id_raises(self, mock_cfg):
        mock_cfg.return_value = MagicMock(notion_token="t", notion_database_id=None)
        with pytest.raises(ValueError, match="NOTION_DATABASE_ID"):
            from src.destinations.notion import NotionDestination
            NotionDestination()


# ── Todoist ───────────────────────────────────────────────────────────────────

class TestTodoistDestination:
    @patch("src.destinations.todoist.get_settings")
    def test_create_task_success(self, mock_cfg, sample_task):
        mock_cfg.return_value = MagicMock(
            todoist_api_token="secret",
            tasks=MagicMock(default_project="Inbox", confidence_threshold=0.6),
            destinations=MagicMock(
                todoist=MagicMock(default_project_name="Inbox")
            ),
        )

        fake_task = MagicMock(id="task-xyz", url="https://todoist.com/task-xyz")
        fake_project = MagicMock(id="proj-1", name="Work")

        with patch("src.destinations.todoist.TodoistAPI") as mock_api_cls:
            mock_api = MagicMock()
            mock_api.get_projects.return_value = [fake_project]
            mock_api.add_task.return_value = fake_task
            mock_api_cls.return_value = mock_api

            from src.destinations.todoist import TodoistDestination
            dest = TodoistDestination()
            result = dest.create_task(sample_task)

        assert result.success is True
        assert result.external_id == "task-xyz"

    @patch("src.destinations.todoist.get_settings")
    def test_create_task_api_error_returns_failed_result(self, mock_cfg, sample_task):
        mock_cfg.return_value = MagicMock(
            todoist_api_token="secret",
            tasks=MagicMock(default_project="Inbox", confidence_threshold=0.6),
            destinations=MagicMock(
                todoist=MagicMock(default_project_name="Inbox")
            ),
        )
        with patch("src.destinations.todoist.TodoistAPI") as mock_api_cls:
            mock_api = MagicMock()
            mock_api.get_projects.return_value = []
            mock_api.add_project.return_value = MagicMock(id="p1", name="Work")
            mock_api.add_task.side_effect = Exception("Network error")
            mock_api_cls.return_value = mock_api

            from src.destinations.todoist import TodoistDestination
            dest = TodoistDestination()
            result = dest.create_task(sample_task)

        assert result.success is False
        assert result.error is not None

    @patch("src.destinations.todoist.get_settings")
    def test_missing_token_raises(self, mock_cfg):
        mock_cfg.return_value = MagicMock(todoist_api_token=None)
        with pytest.raises(ValueError, match="TODOIST_API_TOKEN"):
            from src.destinations.todoist import TodoistDestination
            TodoistDestination()
