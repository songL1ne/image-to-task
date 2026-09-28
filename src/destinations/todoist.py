"""
todoist.py — Todoist REST API v2 destination adapter.

API version: Todoist REST API v2 (https://api.todoist.com/rest/v2)
Auth:        Bearer token (TODOIST_API_TOKEN in .env)
Library:     todoist-api-python >= 2.1

Priority mapping (Todoist uses integers 1–4, inverted from intuition):
  urgent → 4  (p1 — highest in Todoist UI)
  high   → 3  (p2)
  medium → 2  (p3)
  low    → 1  (p4 — lowest)
"""
from __future__ import annotations

from typing import Optional

from todoist_api_python.api import TodoistAPI  # type: ignore

from ..config import get_settings
from ..task_parser import Task
from ..utils import get_logger, retry
from .base import TaskDestination, TaskResult

log = get_logger("destinations.todoist")

_PRIORITY_MAP = {
    "urgent": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


class TodoistDestination(TaskDestination):
    """Send tasks to Todoist via the REST API v2."""

    def __init__(self) -> None:
        cfg = get_settings()
        if not cfg.todoist_api_token:
            raise ValueError(
                "TODOIST_API_TOKEN is not set. "
                "Add it to your .env file or remove 'todoist' from destinations.active."
            )
        self._api = TodoistAPI(cfg.todoist_api_token)
        self._cfg = cfg
        self._project_cache: dict[str, str] = {}  # name → id

    # ── Helpers ───────────────────────────────────────────────────────────────

    @retry(attempts=3, backoff_base=2.0, exceptions=(Exception,))
    def _get_or_create_project(self, name: str) -> Optional[str]:
        """Return the Todoist project ID for *name*, creating it if necessary."""
        if name in self._project_cache:
            return self._project_cache[name]

        projects = self._api.get_projects()
        for proj in projects:
            self._project_cache[proj.name] = proj.id
            if proj.name == name:
                return proj.id

        # Project not found — create it
        new_proj = self._api.add_project(name=name)
        self._project_cache[new_proj.name] = new_proj.id
        log.info("Created Todoist project: %s (id=%s)", name, new_proj.id)
        return new_proj.id

    @retry(attempts=3, backoff_base=2.0, exceptions=(Exception,))
    def _ensure_labels(self, label_names: list[str]) -> list[str]:
        """Return *label_names* as-is; Todoist v2 creates labels on task creation."""
        return label_names

    # ── TaskDestination interface ─────────────────────────────────────────────

    @retry(attempts=3, backoff_base=2.0, exceptions=(Exception,))
    def create_task(self, task: Task) -> TaskResult:
        """
        Create a task in Todoist.

        Maps priority, resolves/creates the project, and attaches labels.
        The source image filename is appended to the task description.
        """
        try:
            cfg = self._cfg
            project_name = (
                task.project
                or cfg.tasks.default_project
                or cfg.destinations.todoist.default_project_name
            )
            project_id = self._get_or_create_project(project_name)

            description = task.description
            # Source image reference
            if hasattr(task, "_source_image") and task._source_image:  # type: ignore[attr-defined]
                description += f"\n\n📎 Source: {task._source_image}"

            todoist_task = self._api.add_task(
                content=task.title,
                description=description or None,
                project_id=project_id,
                due_date=task.due_date or None,
                priority=_PRIORITY_MAP.get(task.priority, 2),
                labels=task.labels if task.labels else None,
            )
            return TaskResult(
                success=True,
                external_id=todoist_task.id,
                url=todoist_task.url,
            )
        except Exception as exc:
            log.error("Todoist create_task failed: %s", exc)
            return TaskResult(success=False, error=str(exc))
