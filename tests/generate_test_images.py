"""
generate_test_images.py — Create the 3 sample test images used by the test suite.

Run once: python tests/generate_test_images.py
Requires: pillow
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import textwrap

OUT = Path(__file__).parent / "images"
OUT.mkdir(exist_ok=True)

BG   = (255, 255, 255)
FG   = (30, 30, 30)
W, H = 600, 400


def _img(name: str, lines: list[str], title: str = "") -> None:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    # Title bar
    if title:
        draw.rectangle([0, 0, W, 40], fill=(220, 220, 220))
        draw.text((10, 10), title, fill=FG)
    y = 60
    for line in lines:
        draw.text((20, y), line, fill=FG)
        y += 28
    img.save(OUT / name)
    print(f"  ✓  {OUT / name}")


# 1. To-do list
_img(
    "todo_list.png",
    title="My Tasks",
    lines=[
        "TODO:",
        "[ ] Buy milk by Friday",
        "[ ] Send weekly report to Alice — ASAP",
        "[ ] Book dentist appointment next Monday",
        "[ ] Review pull request #42 by EOD",
        "[ ] Pay electricity bill — due 2025-08-10",
    ],
)

# 2. Meeting whiteboard
_img(
    "whiteboard.png",
    title="Sprint Planning — 2025-07-30",
    lines=[
        "Action Items:",
        "- Bob: update API docs by next Friday",
        "- Carol: fix login bug (P0) — today",
        "- Dave: set up staging environment next week",
        "- All: review design mockups before Thursday",
        "",
        "Blockers: waiting on infra team for DB access",
    ],
)

# 3. Image with no tasks (landscape photo placeholder)
_img(
    "no_tasks.png",
    title="",
    lines=[
        "",
        "  [Landscape photograph — mountains at sunset]",
        "",
        "  No text. No tasks.",
    ],
)

print("Done.")
