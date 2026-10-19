"""Local files for jobs and the vision-model settings."""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from typing import Any

from app.config import DATA_DIR, DEFAULT_SETTINGS, JOBS_DIR, SETTINGS_PATH

ID_RE = re.compile(r"^[a-f0-9]{32}$")
IMAGE_RE = re.compile(r"^cheque_[0-9]+\.png$")


class Missing(Exception):
    """A job id that is not on this computer."""


def ensure_dirs() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)


def valid_id(job_id: str) -> bool:
    return bool(ID_RE.match(job_id or ""))


def job_dir(job_id: str):
    if not valid_id(job_id):
        raise Missing()
    return JOBS_DIR / job_id


def save_job(job: dict[str, Any]) -> None:
    ensure_dirs()
    directory = job_dir(job["id"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "job.json"
    temporary = directory / "job.json.tmp"
    temporary.write_text(json.dumps(job, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_job(job_id: str) -> dict[str, Any]:
    path = job_dir(job_id) / "job.json"
    if not path.is_file():
        raise Missing()
    return json.loads(path.read_text(encoding="utf-8"))


def delete_job(job_id: str) -> None:
    directory = job_dir(job_id)
    if not directory.is_dir():
        raise Missing()
    shutil.rmtree(directory)


def list_jobs() -> list[dict[str, Any]]:
    ensure_dirs()
    jobs = []
    for path in JOBS_DIR.glob("*/job.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        jobs.append(job)
    jobs.sort(key=lambda job: job.get("created_at", ""), reverse=True)
    return jobs


def load_settings() -> dict[str, str]:
    ensure_dirs()
    settings = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.is_file():
        try:
            stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        if isinstance(stored, dict):
            for key in DEFAULT_SETTINGS:
                if key in stored and stored[key] is not None:
                    settings[key] = str(stored[key])
    return settings


def save_settings(settings: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def effective_settings() -> dict[str, str]:
    """File settings, with OPENAI_API_KEY used only when no key has been saved."""
    settings = load_settings()
    settings["key_source"] = "file" if settings.get("api_key") else ""
    if not settings["api_key"] and os.environ.get("CHECKSCAN_IGNORE_ENV") != "1":
        env_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if env_key:
            settings["api_key"] = env_key
            settings["key_source"] = "env"
    return settings


def settings_hint(settings: dict[str, str]) -> str:
    key = settings.get("api_key", "")
    if not key:
        return ""
    ending = key[-4:] if len(key) >= 8 else ""
    if settings.get("key_source") == "env":
        base = "Using OPENAI_API_KEY from the environment"
    else:
        base = "A key is saved on this computer"
    return f"{base}, ending in {ending}." if ending else base + "."


def now_stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="minutes")
