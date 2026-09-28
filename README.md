# Image-to-Task Automation System

Watch a local folder for images, extract text and context with an LLM (or OCR
fallback), and automatically create structured tasks in **Notion** and/or
**Todoist**.

---

## Table of Contents

1. [Requirements](#requirements)
2. [Setup](#setup)
3. [Getting API Tokens](#getting-api-tokens)
   - [Todoist](#todoist-api-token)
   - [Notion](#notion-api-token--database)
4. [Configuration](#configuration)
5. [Running](#running)
6. [Running as a Background Service](#running-as-a-background-service)
   - [macOS — launchd](#macos--launchd)
   - [Linux — systemd](#linux--systemd)
   - [Windows — Task Scheduler](#windows--task-scheduler)
7. [Running Tests](#running-tests)
8. [Adding a New Destination](#adding-a-new-destination)
9. [Known Limitations](#known-limitations)

---

## Requirements

- **Python 3.11+**
- **Tesseract OCR** (optional — used as fallback when no LLM key is set)
  - macOS: `brew install tesseract`
  - Ubuntu/Debian: `sudo apt install tesseract-ocr`
  - Windows: [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki)
  - *Ask before running any system-level install commands.*

---

## Setup

```bash
# 1. Clone / download the project
cd image-to-task

# 2. Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Copy the environment template
cp .env.example .env

# 5. Generate sample test images (one-time)
python tests/generate_test_images.py
```

Edit `.env` and fill in the keys for the services you want to use (see below).

---

## Getting API Tokens

### Todoist API Token

1. Log in to [todoist.com](https://todoist.com).
2. Go to **Settings → Integrations → Developer**.
3. Copy your **API token**.
4. Paste it as `TODOIST_API_TOKEN` in your `.env`.

### Notion API Token & Database

#### Create an integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations).
2. Click **+ New integration**, give it a name, select your workspace.
3. Copy the **Internal Integration Token** (starts with `secret_`).
4. Paste it as `NOTION_TOKEN` in your `.env`.

#### Create the database

Create a new Notion database (full-page) with these **exact** property names and types:

| Property name | Type |
|---|---|
| `Name` | Title (auto-created) |
| `Status` | Status (must include an option named `Not started`) |
| `Due` | Date |
| `Priority` | Select (options: `Low`, `Medium`, `High`, `Urgent`) |
| `Tags` | Multi-select |
| `Source Image` | Text |

#### Connect the integration to the database

1. Open the database page in Notion.
2. Click **⋯ (More)** → **+ Add connections** → select your integration.

#### Find the Database ID

The database URL looks like:
```
https://www.notion.so/myworkspace/abc123def456...?v=...
```
The `abc123def456...` part (32 hex characters) is the **Database ID**.
Paste it as `NOTION_DATABASE_ID` in your `.env`.

---

## Configuration

Non-secret settings live in **`config.yaml`**:

| Key | Default | Description |
|---|---|---|
| `folders.inbox` | `./inbox` | Folder to watch for new images |
| `folders.processed` | `./processed` | Successful images moved here under `YYYY-MM-DD/` |
| `folders.failed` | `./failed` | Failed images moved here |
| `llm.provider` | `openai` | `openai` \| `anthropic` \| `watsonx` |
| `llm.model` | `gpt-4o` | Model name for the chosen provider |
| `ocr.enabled` | `true` | Fall back to Tesseract if LLM fails |
| `tasks.confidence_threshold` | `0.6` | Tasks below this go to "Needs Review" |
| `destinations.active` | `[notion]` | List of active destinations |
| `reliability.dry_run` | `false` | Print tasks without creating them |

Secrets (`OPENAI_API_KEY`, `NOTION_TOKEN`, etc.) always go in **`.env`**, never in `config.yaml`.

---

## Running

```bash
# Watch the inbox continuously (Ctrl-C to stop)
python main.py

# Dry run — log what would be created, create nothing
python main.py --dry-run

# Process existing images once then exit
python main.py --once

# Use an alternative config file
python main.py --config /path/to/my_config.yaml
```

Drop any supported image (`.png .jpg .jpeg .webp .heic .tiff .bmp`) into
`./inbox/` and the pipeline runs automatically.

---

## Running as a Background Service

### macOS — launchd

Create `~/Library/LaunchAgents/com.image-to-task.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.image-to-task</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/image-to-task/.venv/bin/python</string>
    <string>/path/to/image-to-task/main.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/path/to/image-to-task</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/path/to/image-to-task/logs/launchd.log</string>
  <key>StandardErrorPath</key>
  <string>/path/to/image-to-task/logs/launchd.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.image-to-task.plist
launchctl start com.image-to-task
```

### Linux — systemd

Create `/etc/systemd/system/image-to-task.service`:

```ini
[Unit]
Description=Image-to-Task Automation
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/image-to-task
ExecStart=/path/to/image-to-task/.venv/bin/python main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now image-to-task
sudo journalctl -u image-to-task -f    # follow logs
```

### Windows — Task Scheduler

1. Open **Task Scheduler** → **Create Basic Task**.
2. Set trigger: **At startup** (or **At log on**).
3. Action: **Start a program**
   - Program: `C:\path\to\image-to-task\.venv\Scripts\python.exe`
   - Arguments: `main.py`
   - Start in: `C:\path\to\image-to-task`
4. Under **Settings**, check **"If the task fails, restart every: 1 minute"**.

---

## Running Tests

```bash
# From the project root
pytest tests/ -v
```

All external APIs (OpenAI, Notion, Todoist) are mocked — no real keys needed
to run the test suite.

---

## Adding a New Destination

1. Create `src/destinations/myapp.py` and subclass `TaskDestination`:

```python
from .base import TaskDestination, TaskResult
from ..task_parser import Task

class MyAppDestination(TaskDestination):
    def create_task(self, task: Task) -> TaskResult:
        # call your API here
        return TaskResult(success=True, external_id="123")
```

2. Register it in `main.py` inside `_build_destinations()`:

```python
elif name == "myapp":
    from src.destinations.myapp import MyAppDestination
    dests.append(MyAppDestination())
```

3. Add `myapp` to `destinations.active` in `config.yaml`.

That's it — no other files need to change.

---

## Known Limitations

- **HEIC support** requires `pillow-heif` (`pip install pillow-heif`) and registration with Pillow; not included by default.
- **Tesseract language packs** beyond English must be installed separately (`tesseract-ocr-<lang>`).
- **watsonx.ai vision**: the OpenAI-compatible endpoint is used; verify your specific model supports image inputs in your region.
- **Notion Status property**: if your database uses a different status option name than `"Not started"`, update `destinations.notion.status_not_started` in `config.yaml`.
- **Rate limits**: Notion's ~3 req/s limit means large batches of tasks from a single image may be throttled; the retry+backoff logic handles transient 429s but does not implement token-bucket throttling.
- **Image upload**: neither Todoist nor Notion's free API tier supports programmatic file attachment; source images are referenced by filename only.
- **Relative date resolution** uses the image file's `mtime` as the reference date. If files are copied rather than moved into the inbox, `mtime` may not reflect when the image was created.
