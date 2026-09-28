"""
utils.py — Shared utilities: structured logging, retry decorator, date resolver.
"""
from __future__ import annotations

import logging
import logging.handlers
import re
import time
from datetime import date, timedelta
from functools import wraps
from pathlib import Path
from typing import Callable, Optional, Type, Tuple, TypeVar

F = TypeVar("F", bound=Callable)


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logger(log_dir: Path, level: int = logging.INFO) -> logging.Logger:
    """
    Configure the root application logger.

    Writes structured lines to both stdout and a rotating file at
    ``log_dir/app.log`` (10 MB per file, 5 backups).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)-20s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    root = logging.getLogger("image_to_task")
    root.setLevel(level)
    if not root.handlers:
        root.addHandler(file_handler)
        root.addHandler(console_handler)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``image_to_task`` namespace."""
    return logging.getLogger(f"image_to_task.{name}")


# ── Retry decorator ───────────────────────────────────────────────────────────

def retry(
    attempts: int = 3,
    backoff_base: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """
    Decorator: retry *attempts* times with exponential backoff.

    Args:
        attempts:     Total number of tries (including the first).
        backoff_base: Base delay in seconds; doubles each retry.
        exceptions:   Exception types to catch and retry on.
    """
    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            logger = get_logger("retry")
            delay = backoff_base
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    if attempt == attempts:
                        raise
                    logger.warning(
                        "Attempt %d/%d failed for %s: %s — retrying in %.1fs",
                        attempt, attempts, fn.__qualname__, exc, delay,
                    )
                    time.sleep(delay)
                    delay *= 2
        return wrapper  # type: ignore[return-value]
    return decorator


# ── Relative date resolver ────────────────────────────────────────────────────

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def resolve_relative_date(
    text: Optional[str],
    reference: Optional[date] = None,
) -> Optional[str]:
    """
    Convert natural-language date strings to ISO 8601 (YYYY-MM-DD).

    Handles:
      - Already-formatted dates (YYYY-MM-DD) — returned unchanged.
      - "today", "tomorrow"
      - "next <weekday>" (e.g. "next Friday")
      - "EOD" / "end of day" — treated as today
      - "next week" — Monday of next week
      - Anything unrecognised — returned as-is (caller may log a warning).

    Args:
        text:      The raw date string from the LLM output.
        reference: The anchor date (defaults to today).

    Returns:
        ISO date string, or None if *text* is None/empty.
    """
    if not text:
        return None

    ref = reference or date.today()
    normalised = text.strip().lower()

    # Already ISO format
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalised):
        return normalised

    if normalised in ("today", "eod", "end of day"):
        return ref.isoformat()

    if normalised == "tomorrow":
        return (ref + timedelta(days=1)).isoformat()

    if normalised == "next week":
        # Monday of next calendar week
        days_ahead = 7 - ref.weekday()
        return (ref + timedelta(days=days_ahead)).isoformat()

    # "next <weekday>"
    match = re.fullmatch(r"next\s+(\w+)", normalised)
    if match:
        day_name = match.group(1)
        if day_name in _WEEKDAYS:
            target = _WEEKDAYS[day_name]
            days_ahead = (target - ref.weekday() + 7) % 7 or 7
            return (ref + timedelta(days=days_ahead)).isoformat()

    # Return unchanged — caller should log a warning
    return text
