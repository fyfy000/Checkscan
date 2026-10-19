"""Paths and limits for the local ChequeScript app."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CHECKSCAN_DATA", ROOT / "data"))
JOBS_DIR = DATA_DIR / "jobs"
SETTINGS_PATH = DATA_DIR / "settings.json"

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_PAGES = 80
MAX_PICTURES = 40

DEFAULT_SETTINGS = {
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o",
    "api_key": "",
}
