"""Filesystem locations used by LocalFlow.

Everything LocalFlow persists lives under a single per-user directory so that
uninstalling is a single delete and so nothing ever leaves the machine.
"""
from __future__ import annotations

import os
from pathlib import Path


def _base_dir() -> Path:
    override = os.environ.get("LOCALFLOW_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "LocalFlow"
    return Path.home() / ".localflow"


DATA_DIR = _base_dir()
MODELS_DIR = DATA_DIR / "models"
AUDIO_DIR = DATA_DIR / "audio"
LOGS_DIR = DATA_DIR / "logs"
RUNTIME_DIR = DATA_DIR / "runtime"
DB_PATH = DATA_DIR / "localflow.db"


def ensure_dirs() -> None:
    for d in (DATA_DIR, MODELS_DIR, AUDIO_DIR, LOGS_DIR, RUNTIME_DIR):
        d.mkdir(parents=True, exist_ok=True)
