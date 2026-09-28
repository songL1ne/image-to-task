"""
watcher.py — Folder monitoring and per-file pipeline orchestration.

Responsibilities:
  - Watch the inbox folder with watchdog for new image files.
  - Wait for file stability (size unchanged across N checks).
  - On startup, process any pre-existing images in the inbox.
  - Run the full pipeline: dedup → extract → parse → send.
  - Move processed files to ./processed/YYYY-MM-DD/ or ./failed/.
  - Write a JSON sidecar next to every moved file.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler  # type: ignore
from watchdog.observers import Observer  # type: ignore

from .config import get_settings
from .extractor import ExtractionResult, extract
from .storage import ImageStorage
from .task_parser import ParsedImage, parse_tasks
from .utils import get_logger

log = get_logger("watcher")

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".tiff", ".bmp"}


# ── Pipeline result ───────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    file: str
    sha256: str
    extraction_method: str
    image_type: str
    image_summary: str
    task_count: int
    tasks_created: List[dict]
    dry_run: bool
    error: Optional[str] = None


# ── File stability check ──────────────────────────────────────────────────────

def _wait_for_stable(path: Path, checks: int, interval: float) -> bool:
    """
    Block until the file size is unchanged across *checks* consecutive reads.

    Returns True when stable, False if the file disappears while waiting.
    """
    prev_size = -1
    stable_count = 0
    while stable_count < checks:
        if not path.exists():
            return False
        size = path.stat().st_size
        if size == prev_size:
            stable_count += 1
        else:
            stable_count = 0
            prev_size = size
        time.sleep(interval)
    return True


# ── Sidecar writer ────────────────────────────────────────────────────────────

def _write_sidecar(dest_dir: Path, stem: str, data: dict) -> None:
    sidecar = dest_dir / f"{stem}.json"
    with open(sidecar, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)


# ── Core pipeline ─────────────────────────────────────────────────────────────

def process_image(
    path: Path,
    storage: ImageStorage,
    destinations,          # List[TaskDestination] — imported lazily to avoid circular
    dry_run: bool = False,
) -> PipelineResult:
    """
    Run the full pipeline for a single image file.

    Steps:
      1. Hash → dedup check.
      2. Extract text + image type.
      3. Parse tasks.
      4. Send to destinations (or dry-run log).
      5. Record in storage.
      6. Return PipelineResult.
    """
    cfg = get_settings()
    sha256 = ImageStorage.hash_file(path)

    if storage.is_duplicate(sha256):
        log.info("Skipping duplicate image: %s (%s)", path.name, sha256[:12])
        return PipelineResult(
            file=path.name, sha256=sha256,
            extraction_method="skipped", image_type="duplicate",
            image_summary="Duplicate — already processed.",
            task_count=0, tasks_created=[], dry_run=dry_run,
        )

    # Extraction
    log.info("Extracting from %s …", path.name)
    extraction: ExtractionResult = extract(path)
    if extraction.error and not extraction.raw_text:
        raise RuntimeError(f"Extraction failed: {extraction.error}")

    # Task parsing
    log.info("Parsing tasks from %s …", path.name)
    parsed: ParsedImage = parse_tasks(extraction)

    tasks_created = []
    dest_names = []

    for task in parsed.tasks:
        for dest in destinations:
            dest_names.append(type(dest).__name__)
            if dry_run:
                log.info(
                    "[DRY RUN] Would create task: %s (priority=%s, due=%s)",
                    task.title, task.priority, task.due_date,
                )
                tasks_created.append(
                    {"title": task.title, "destination": type(dest).__name__, "dry_run": True}
                )
            else:
                result = dest.create_task(task)
                log.info(
                    "Created task '%s' in %s (id=%s)",
                    task.title, type(dest).__name__, result.external_id,
                )
                tasks_created.append({
                    "title": task.title,
                    "destination": type(dest).__name__,
                    "external_id": result.external_id,
                    "url": result.url,
                })

    storage.record(
        sha256=sha256,
        file_path=path,
        task_count=len(tasks_created),
        destination=",".join(set(dest_names)) if dest_names else None,
    )

    return PipelineResult(
        file=path.name,
        sha256=sha256,
        extraction_method=extraction.method,
        image_type=extraction.image_type,
        image_summary=parsed.image_summary,
        task_count=len(tasks_created),
        tasks_created=tasks_created,
        dry_run=dry_run,
    )


def _handle_file(
    path: Path,
    storage: ImageStorage,
    destinations,
    dry_run: bool,
) -> None:
    """Orchestrate pipeline, file moves, and sidecar writes for one image."""
    cfg = get_settings()
    processed_base = Path(cfg.folders.processed)
    failed_dir = Path(cfg.folders.failed)

    try:
        result = process_image(path, storage, destinations, dry_run)
        day_dir = processed_base / datetime.now().strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        dest = day_dir / path.name
        shutil.move(str(path), str(dest))
        _write_sidecar(day_dir, path.stem, asdict(result))
        log.info("Moved %s → %s", path.name, day_dir)

    except Exception as exc:
        log.error("Pipeline failed for %s: %s", path.name, exc, exc_info=True)
        failed_dir.mkdir(parents=True, exist_ok=True)
        dest = failed_dir / path.name
        shutil.move(str(path), str(dest))
        _write_sidecar(failed_dir, path.stem, {"file": path.name, "error": str(exc)})
        log.info("Moved %s → %s (failed)", path.name, failed_dir)


# ── watchdog handler ──────────────────────────────────────────────────────────

class ImageHandler(FileSystemEventHandler):
    """watchdog event handler for new image files in the inbox."""

    def __init__(self, storage: ImageStorage, destinations, dry_run: bool) -> None:
        super().__init__()
        self._storage = storage
        self._destinations = destinations
        self._dry_run = dry_run
        self._cfg = get_settings()

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return

        log.debug("Detected new file: %s", path.name)
        rel = self._cfg.reliability
        if _wait_for_stable(path, rel.file_stability_checks, rel.file_stability_interval):
            _handle_file(path, self._storage, self._destinations, self._dry_run)
        else:
            log.warning("File disappeared before processing: %s", path.name)


# ── Startup scan + observer ───────────────────────────────────────────────────

def scan_existing(inbox: Path, storage: ImageStorage, destinations, dry_run: bool) -> None:
    """Process any images already present in the inbox on startup."""
    images = [p for p in inbox.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS]
    if not images:
        return
    log.info("Found %d existing image(s) in inbox — processing now.", len(images))
    cfg = get_settings()
    rel = cfg.reliability
    for path in images:
        if _wait_for_stable(path, rel.file_stability_checks, rel.file_stability_interval):
            _handle_file(path, storage, destinations, dry_run)


def start_watcher(
    inbox: Path,
    storage: ImageStorage,
    destinations,
    dry_run: bool = False,
) -> Observer:
    """
    Start the watchdog observer on *inbox* and return it (non-blocking).

    The caller is responsible for calling ``observer.stop()`` and
    ``observer.join()`` on shutdown.
    """
    inbox.mkdir(parents=True, exist_ok=True)
    handler = ImageHandler(storage, destinations, dry_run)
    observer = Observer()
    observer.schedule(handler, str(inbox), recursive=False)
    observer.start()
    log.info("Watching %s for new images …", inbox)
    return observer
