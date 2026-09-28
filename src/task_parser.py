"""
task_parser.py — Turn extracted image text into validated Task objects.

Pipeline:
  1. Build a strict prompt from the ExtractionResult.
  2. Call the LLM; parse and validate the JSON response with Pydantic.
  3. Retry once on invalid JSON.
  4. Resolve relative dates against the image file's mtime.
  5. Route low-confidence tasks to the "Needs Review" label.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .config import get_settings
from .extractor import ExtractionResult
from .utils import get_logger, resolve_relative_date

log = get_logger("task_parser")


# ── Pydantic models ───────────────────────────────────────────────────────────

class Task(BaseModel):
    """A single actionable task extracted from an image."""

    title: str = Field(..., description="Short imperative title, starts with a verb.")
    description: str = Field(default="", description="Context from the image.")
    due_date: Optional[str] = Field(default=None, description="ISO date or null.")
    priority: str = Field(default="medium", description="low|medium|high|urgent")
    labels: List[str] = Field(default_factory=list)
    project: Optional[str] = Field(default=None)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, v: str) -> str:
        allowed = {"low", "medium", "high", "urgent"}
        v = v.lower()
        if v not in allowed:
            return "medium"
        return v

    @field_validator("due_date", mode="before")
    @classmethod
    def _validate_due_date(cls, v) -> Optional[str]:
        if v in (None, "", "null"):
            return None
        return str(v)


class ParsedImage(BaseModel):
    """Full LLM response for one image."""
    image_summary: str = ""
    image_type: str = ""
    tasks: List[Task] = Field(default_factory=list)


# ── Prompt builder ────────────────────────────────────────────────────────────

_TASK_SCHEMA = """
{
  "image_summary": "string",
  "image_type": "string",
  "tasks": [
    {
      "title": "string (short, starts with a verb)",
      "description": "string (include relevant context from the image)",
      "due_date": "YYYY-MM-DD or null",
      "priority": "low | medium | high | urgent",
      "labels": ["string"],
      "project": "string or null",
      "confidence": 0.0
    }
  ]
}
"""

def _build_prompt(extraction: ExtractionResult) -> str:
    return (
        "You are a task extraction assistant. "
        "Below is text extracted from an image, along with its type.\n\n"
        f"Image type: {extraction.image_type}\n"
        f"Extracted text:\n{extraction.raw_text}\n\n"
        "Extract ALL actionable tasks from this content. "
        "For each task, assign a confidence score (0.0–1.0) reflecting how "
        "certain you are it is a real, intended task. "
        "If there are no actionable items, return an empty tasks array.\n\n"
        "Respond with ONLY valid JSON matching this schema — no markdown, "
        "no explanation, no extra keys:\n"
        f"{_TASK_SCHEMA}"
    )


# ── LLM call (text-only) ──────────────────────────────────────────────────────

def _call_llm_text(prompt: str) -> str:
    """Call the configured LLM with a plain text prompt; return raw string."""
    cfg = get_settings()
    provider = cfg.llm.provider.lower()

    if provider == "openai":
        from openai import OpenAI  # type: ignore
        client = OpenAI(api_key=cfg.openai_api_key)
        resp = client.chat.completions.create(
            model=cfg.llm.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
        )
        return resp.choices[0].message.content or ""

    if provider == "anthropic":
        import anthropic  # type: ignore
        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        resp = client.messages.create(
            model=cfg.llm.model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text or ""

    if provider == "watsonx":
        from openai import OpenAI  # type: ignore
        base_url = cfg.llm.watsonx_url or "https://us-south.ml.cloud.ibm.com"
        client = OpenAI(
            api_key=cfg.watsonx_api_key,
            base_url=f"{base_url.rstrip('/')}/ml/v1/text/chat?version=2024-05-01",
        )
        resp = client.chat.completions.create(
            model=cfg.llm.model,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"project_id": cfg.llm.watsonx_project_id},
            max_tokens=2048,
        )
        return resp.choices[0].message.content or ""

    raise ValueError(f"Unknown LLM provider: {provider!r}")


# ── JSON extraction helper ────────────────────────────────────────────────────

def _extract_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON from LLM output."""
    # Remove ```json ... ``` or ``` ... ``` fences
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    return json.loads(cleaned)


# ── Date resolution ───────────────────────────────────────────────────────────

def _reference_date(source_path: Path) -> date:
    """Use the image file's mtime as the reference date for relative dates."""
    try:
        mtime = source_path.stat().st_mtime
        return datetime.fromtimestamp(mtime).date()
    except Exception:
        return date.today()


# ── Public entry point ────────────────────────────────────────────────────────

def parse_tasks(extraction: ExtractionResult) -> ParsedImage:
    """
    Turn an :class:`ExtractionResult` into a validated :class:`ParsedImage`.

    Steps:
      - Calls the LLM with a strict JSON prompt.
      - Retries once on JSON parse failure.
      - Validates the response with Pydantic.
      - Resolves relative due dates.
      - Routes low-confidence tasks to the "Needs Review" label.

    Args:
        extraction: Output from :func:`extractor.extract`.

    Returns:
        A :class:`ParsedImage` with zero or more :class:`Task` objects.
    """
    cfg = get_settings()
    prompt = _build_prompt(extraction)
    ref_date = _reference_date(extraction.source_path)

    raw_response = ""
    for attempt in range(1, 3):  # max 2 attempts
        try:
            raw_response = _call_llm_text(prompt)
            data = _extract_json(raw_response)
            parsed = ParsedImage(**data)
            break
        except (json.JSONDecodeError, Exception) as exc:
            if attempt == 2:
                log.error(
                    "Failed to parse LLM response after 2 attempts: %s\nRaw: %s",
                    exc, raw_response[:500],
                )
                return ParsedImage(
                    image_summary="Parse error",
                    image_type=extraction.image_type,
                    tasks=[],
                )
            log.warning("JSON parse failed (attempt %d): %s — retrying.", attempt, exc)

    # Post-process tasks
    threshold = cfg.tasks.confidence_threshold
    needs_review = cfg.tasks.needs_review_label

    for task in parsed.tasks:
        # Resolve relative dates
        task.due_date = resolve_relative_date(task.due_date, reference=ref_date)

        # Route low-confidence tasks
        if task.confidence < threshold:
            if needs_review not in task.labels:
                task.labels.append(needs_review)
            if task.project is None:
                task.project = needs_review
            log.debug(
                "Task '%s' below confidence threshold (%.2f) — routed to Needs Review.",
                task.title, task.confidence,
            )

    if not parsed.tasks:
        log.info("No actionable tasks found in %s.", extraction.source_path.name)

    return parsed
