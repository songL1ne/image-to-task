"""
config.py — Load settings from config.yaml and secrets from .env.

All other modules import the singleton `settings` object from here.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env from the project root (parent of src/)
_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env")


# ── Sub-models ────────────────────────────────────────────────────────────────

class FoldersConfig(BaseModel):
    inbox: Path = Path("./inbox")
    processed: Path = Path("./processed")
    failed: Path = Path("./failed")
    logs: Path = Path("./logs")


class LLMConfig(BaseModel):
    provider: str = "openai"          # openai | anthropic | watsonx
    model: str = "gpt-4o"
    watsonx_url: str = ""
    watsonx_project_id: str = ""


class OCRConfig(BaseModel):
    enabled: bool = True


class TasksConfig(BaseModel):
    confidence_threshold: float = 0.6
    needs_review_label: str = "Needs Review"
    default_project: str = "Inbox"


class TodoistDestConfig(BaseModel):
    default_project_name: str = "Inbox"


class NotionDestConfig(BaseModel):
    status_not_started: str = "Not started"


class DestinationsConfig(BaseModel):
    active: List[str] = Field(default_factory=lambda: ["notion"])
    todoist: TodoistDestConfig = Field(default_factory=TodoistDestConfig)
    notion: NotionDestConfig = Field(default_factory=NotionDestConfig)


class ReliabilityConfig(BaseModel):
    dry_run: bool = False
    api_retry_attempts: int = 3
    api_retry_backoff_base: int = 2
    file_stability_checks: int = 3
    file_stability_interval: float = 1.0


# ── Main settings model ───────────────────────────────────────────────────────

class Settings(BaseModel):
    """Top-level settings object combining config.yaml and environment variables."""

    folders: FoldersConfig = Field(default_factory=FoldersConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
    destinations: DestinationsConfig = Field(default_factory=DestinationsConfig)
    reliability: ReliabilityConfig = Field(default_factory=ReliabilityConfig)

    # Secrets — read from environment, never from config.yaml
    openai_api_key: Optional[str] = Field(default=None)
    anthropic_api_key: Optional[str] = Field(default=None)
    watsonx_api_key: Optional[str] = Field(default=None)
    todoist_api_token: Optional[str] = Field(default=None)
    notion_token: Optional[str] = Field(default=None)
    notion_database_id: Optional[str] = Field(default=None)

    def effective_dry_run(self) -> bool:
        """Return True if dry-run is enabled via config or CLI override."""
        return self.reliability.dry_run


@lru_cache(maxsize=1)
def load_settings(config_path: Optional[str] = None) -> Settings:
    """
    Load and return the singleton Settings object.

    Reads config.yaml (or a custom path), then overlays secrets from the
    environment. Call this once from main.py; everywhere else import
    ``get_settings()``.
    """
    path = Path(config_path) if config_path else _ROOT / "config.yaml"

    raw: dict = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

    settings = Settings(
        folders=FoldersConfig(**raw.get("folders", {})),
        llm=LLMConfig(**raw.get("llm", {})),
        ocr=OCRConfig(**raw.get("ocr", {})),
        tasks=TasksConfig(**raw.get("tasks", {})),
        destinations=DestinationsConfig(
            active=raw.get("destinations", {}).get("active", ["notion"]),
            todoist=TodoistDestConfig(
                **raw.get("destinations", {}).get("todoist", {})
            ),
            notion=NotionDestConfig(
                **raw.get("destinations", {}).get("notion", {})
            ),
        ),
        reliability=ReliabilityConfig(**raw.get("reliability", {})),
        # Secrets from environment
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        watsonx_api_key=os.getenv("WATSONX_API_KEY") or None,
        todoist_api_token=os.getenv("TODOIST_API_TOKEN") or None,
        notion_token=os.getenv("NOTION_TOKEN") or None,
        notion_database_id=os.getenv("NOTION_DATABASE_ID") or None,
    )
    return settings


def get_settings() -> Settings:
    """Return the cached Settings singleton."""
    return load_settings()
