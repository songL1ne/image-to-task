"""
notion.py — Notion API destination adapter.

API version : Notion API 2022-06-28 (current stable as of 2025)
Base URL    : https://api.notion.com/v1
Auth        : Bearer token — NOTION_TOKEN in .env
Library     : notion-client >= 2.2

Expected database properties (create these in Notion before running):
  Name          — Title
  Status        — Status  (options must include "Not started")
  Due           — Date
  Priority      — Select  (options: Low, Medium, High, Urgent)
  Tags          — Multi-select
  Source Image  — Rich text

Priority mapping (our value → Notion select option name):
  urgent → Urgent
  high   → High
  medium → Medium
  low    → Low
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from notion_client import Client  # type: ignore

from ..config import get_settings
from ..task_parser import Task
from ..utils import get_logger, retry
from .base import TaskDestination, TaskResult

log = get_logger("destinations.notion")

_PRIORITY_MAP = {
    "urgent": "Urgent",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}


class NotionDestination(TaskDestination):
    """Send tasks to a Notion database via the Notion API 2022-06-28."""

    def __init__(self) -> None:
        cfg = get_settings()
        if not cfg.notion_token:
            raise ValueError(
                "NOTION_TOKEN is not set. "
                "Add it to your .env file or remove 'notion' from destinations.active."
            )
        if not cfg.notion_database_id:
            raise ValueError(
                "NOTION_DATABASE_ID is not set. "
                "Add the target database ID to your .env file."
            )
        self._client = Client(auth=cfg.notion_token)
        self._database_id = cfg.notion_database_id
        self._cfg = cfg

    # ── Property builders ─────────────────────────────────────────────────────

    @staticmethod
    def _title_prop(text: str) -> Dict[str, Any]:
        return {"title": [{"text": {"content": text}}]}

    @staticmethod
    def _rich_text_prop(text: str) -> Dict[str, Any]:
        return {"rich_text": [{"text": {"content": text[:2000]}}]}

    @staticmethod
    def _select_prop(name: str) -> Dict[str, Any]:
        return {"select": {"name": name}}

    @staticmethod
    def _multi_select_prop(names: List[str]) -> Dict[str, Any]:
        return {"multi_select": [{"name": n} for n in names]}

    @staticmethod
    def _date_prop(iso_date: Optional[str]) -> Optional[Dict[str, Any]]:
        if not iso_date:
            return None
        return {"date": {"start": iso_date}}

    @staticmethod
    def _status_prop(name: str) -> Dict[str, Any]:
        return {"status": {"name": name}}

    # ── TaskDestination interface ─────────────────────────────────────────────

    @retry(attempts=3, backoff_base=2.0, exceptions=(Exception,))
    def create_task(self, task: Task) -> TaskResult:
        """
        Create a page in the configured Notion database.

        Each task maps to one database row with the following properties:
        Name, Status, Due, Priority, Tags, Source Image.
        """
        try:
            cfg = self._cfg
            status_name = cfg.destinations.notion.status_not_started

            # Build description: task description + source image reference
            description_parts = []
            if task.description:
                description_parts.append(task.description)
            source_image = getattr(task, "_source_image", None)
            if source_image:
                description_parts.append(f"Source image: {source_image}")
            full_description = "\n\n".join(description_parts)

            properties: Dict[str, Any] = {
                "Name": self._title_prop(task.title),
                "Status": self._status_prop(status_name),
                "Priority": self._select_prop(
                    _PRIORITY_MAP.get(task.priority, "Medium")
                ),
            }

            if task.due_date:
                date_prop = self._date_prop(task.due_date)
                if date_prop:
                    properties["Due"] = date_prop

            if task.labels:
                properties["Tags"] = self._multi_select_prop(task.labels)

            if source_image:
                properties["Source Image"] = self._rich_text_prop(source_image)

            # Build page body
            page_body: Dict[str, Any] = {
                "parent": {"database_id": self._database_id},
                "properties": properties,
            }

            # Add description as page content if present
            if full_description:
                page_body["children"] = [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {"type": "text", "text": {"content": full_description[:2000]}}
                            ]
                        },
                    }
                ]

            response = self._client.pages.create(**page_body)
            page_id: str = response["id"]
            page_url: str = response.get("url", "")

            return TaskResult(success=True, external_id=page_id, url=page_url)

        except Exception as exc:
            log.error("Notion create_task failed: %s", exc)
            return TaskResult(success=False, error=str(exc))
