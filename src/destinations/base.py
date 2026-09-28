"""
base.py — Abstract TaskDestination and shared data models.

All destination adapters must subclass TaskDestination and implement
``create_task(task: Task) -> TaskResult``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..task_parser import Task


@dataclass
class TaskResult:
    """Outcome of a single task creation attempt."""
    success: bool
    external_id: Optional[str] = None   # ID assigned by the destination service
    url: Optional[str] = None           # Deep link to the created task (if available)
    error: Optional[str] = None


class TaskDestination(ABC):
    """
    Abstract base class for task destination adapters.

    Implement this to add a new destination (e.g. Asana, Linear, Jira).
    Register it in ``main.py`` alongside Todoist and Notion.
    """

    @abstractmethod
    def create_task(self, task: Task) -> TaskResult:
        """
        Create a task in the destination service.

        Args:
            task: A validated :class:`~task_parser.Task` object.

        Returns:
            A :class:`TaskResult` with success status and any IDs/URLs.
        """
        ...

    # Optional hook — destinations may override for graceful shutdown
    def close(self) -> None:  # noqa: B027
        """Release any held resources (e.g. HTTP sessions)."""
