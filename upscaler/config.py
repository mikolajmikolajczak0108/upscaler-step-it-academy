from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from .models import AppSettings, ToolPaths

APP_NAME = "Upscaler"


def app_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def tool_root() -> Path:
    path = app_root() / "tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_root() -> Path:
    path = Path(tempfile.gettempdir()) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return app_root() / "settings.json"


def load_settings() -> AppSettings:
    path = settings_path()
    if not path.exists():
        return AppSettings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AppSettings()

    tool_paths = ToolPaths(**payload.get("tool_paths", {}))
    return AppSettings(
        tool_paths=tool_paths,
        last_input_dir=payload.get("last_input_dir", ""),
        last_output_dir=payload.get("last_output_dir", ""),
    )


def save_settings(settings: AppSettings) -> None:
    payload = asdict(settings)
    settings_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
