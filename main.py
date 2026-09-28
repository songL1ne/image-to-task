"""
main.py — CLI entry point for the Image-to-Task Automation System.

Usage:
  python main.py                   # watch inbox (runs until Ctrl-C)
  python main.py --dry-run         # watch + dry-run (no real tasks created)
  python main.py --once            # process existing images then exit
  python main.py --once --dry-run  # dry-run one-shot scan
  python main.py --config path/to/config.yaml
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path
from typing import List

# ── Bootstrap: resolve project root so imports work from any cwd ──────────────
_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.config import load_settings
from src.destinations.base import TaskDestination
from src.storage import ImageStorage
from src.utils import setup_logger, get_logger
from src.watcher import scan_existing, start_watcher


def _build_destinations(dry_run: bool) -> List[TaskDestination]:
    """
    Instantiate and return the active destination adapters.

    Skips initialisation when dry_run is True so no credentials are required
    for a dry run (but still returns stub-free list for logging purposes).
    """
    cfg = load_settings()
    dests: List[TaskDestination] = []

    for name in cfg.destinations.active:
        name = name.lower()
        if name == "notion":
            from src.destinations.notion import NotionDestination
            dests.append(NotionDestination())
        elif name == "todoist":
            from src.destinations.todoist import TodoistDestination
            dests.append(TodoistDestination())
        else:
            get_logger("main").warning("Unknown destination %r — skipping.", name)

    return dests


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Image-to-Task Automation System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run the full pipeline but do not create real tasks.",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Process existing images in inbox then exit (no watcher).",
    )
    parser.add_argument(
        "--config", default=None, metavar="PATH",
        help="Path to an alternative config.yaml.",
    )
    args = parser.parse_args()

    # Load settings (config path may override default)
    cfg = load_settings(args.config)

    # CLI --dry-run overrides config value
    dry_run: bool = args.dry_run or cfg.reliability.dry_run

    # Set up logging
    log_dir = Path(cfg.folders.logs)
    setup_logger(log_dir)
    log = get_logger("main")

    if dry_run:
        log.info("=== DRY RUN MODE — no tasks will be created ===")

    # Ensure required directories exist
    inbox = Path(cfg.folders.inbox)
    inbox.mkdir(parents=True, exist_ok=True)

    # Storage (SQLite dedup DB lives beside the project)
    db_path = _PROJECT_ROOT / "image_tasks.db"
    storage = ImageStorage(db_path)

    # Destinations (skip real init in dry-run mode)
    if dry_run:
        destinations: List[TaskDestination] = []
        log.info("Dry-run: destination adapters not initialised.")
    else:
        try:
            destinations = _build_destinations(dry_run=False)
        except ValueError as exc:
            log.error("Failed to initialise destinations: %s", exc)
            sys.exit(1)

    log.info(
        "Starting Image-to-Task | inbox=%s | destinations=%s | dry_run=%s",
        inbox,
        [type(d).__name__ for d in destinations] or ["(dry-run)"],
        dry_run,
    )

    # ── One-shot mode ─────────────────────────────────────────────────────────
    if args.once:
        scan_existing(inbox, storage, destinations, dry_run)
        log.info("One-shot scan complete.")
        return

    # ── Watcher mode ──────────────────────────────────────────────────────────
    scan_existing(inbox, storage, destinations, dry_run)
    observer = start_watcher(inbox, storage, destinations, dry_run)

    # Graceful shutdown on SIGINT / SIGTERM
    def _shutdown(signum, frame):
        log.info("Shutting down …")
        observer.stop()
        for dest in destinations:
            dest.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while observer.is_alive():
            time.sleep(1)
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
