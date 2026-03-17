from __future__ import annotations

import json
import shutil
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from .config import tool_root
from .models import AppSettings

ProgressCallback = Optional[Callable[[int, str], None]]

GITHUB_LATEST_API = "https://api.github.com/repos/{repo}/releases/latest"
GITHUB_TAG_API = "https://api.github.com/repos/{repo}/releases/tags/{tag}"

TOOL_DEFS = {
    "realesrgan": {
        "label": "Real-ESRGAN ncnn Vulkan",
        "repo": "xinntao/Real-ESRGAN",
        "release_tag": "v0.2.5.0",
        "asset_contains": "realesrgan-ncnn-vulkan-20220424-windows.zip",
        "exe_name": "realesrgan-ncnn-vulkan.exe",
    },
    "rife": {
        "label": "RIFE ncnn Vulkan",
        "repo": "nihui/rife-ncnn-vulkan",
        "asset_contains": "-windows.zip",
        "exe_name": "rife-ncnn-vulkan.exe",
    },
}


def resolve_ffmpeg(path_hint: str) -> str:
    return path_hint or (shutil.which("ffmpeg") or "ffmpeg")


def resolve_ffprobe(path_hint: str) -> str:
    return path_hint or (shutil.which("ffprobe") or "ffprobe")


def find_local_tool(exe_name: str) -> str:
    candidates: list[Path] = []
    candidate = shutil.which(exe_name)
    if candidate:
        candidates.append(Path(candidate).resolve(strict=False))
    root = tool_root()
    candidates.extend(path.resolve(strict=False) for path in root.rglob(exe_name))
    if not candidates:
        return ""

    def score(path: Path) -> tuple[int, float]:
        ready = 0
        if exe_name == "realesrgan-ncnn-vulkan.exe" and (path.parent / "models").exists():
            ready = 2
        elif exe_name == "rife-ncnn-vulkan.exe" and (
            (path.parent / "rife-v4.6").exists() or (path.parent / "models" / "rife-v4.6").exists()
        ):
            ready = 2
        return (ready, path.stat().st_mtime)

    return str(sorted(candidates, key=score, reverse=True)[0])


def normalize_path(path_hint: str) -> str:
    if not path_hint:
        return ""
    path = Path(path_hint)
    if path.exists():
        return str(path.resolve(strict=False))
    return path_hint


def realesrgan_model_available(exe_path: str, model_name: str, scale: int) -> bool:
    if not exe_path:
        return False
    base = Path(exe_path).resolve(strict=False).parent / "models"
    if model_name == "realesr-animevideov3":
        stem = f"{model_name}-x{scale}"
    else:
        stem = model_name
    return (base / f"{stem}.param").exists() and (base / f"{stem}.bin").exists()


def refresh_tool_paths(settings: AppSettings) -> AppSettings:
    settings.tool_paths.ffmpeg = resolve_ffmpeg(settings.tool_paths.ffmpeg)
    settings.tool_paths.ffprobe = resolve_ffprobe(settings.tool_paths.ffprobe)
    detected_realesrgan = find_local_tool("realesrgan-ncnn-vulkan.exe")
    detected_rife = find_local_tool("rife-ncnn-vulkan.exe")
    current_realesrgan = normalize_path(settings.tool_paths.realesrgan)
    current_rife = normalize_path(settings.tool_paths.rife)

    if detected_realesrgan and not (Path(current_realesrgan).exists() and (Path(current_realesrgan).resolve(strict=False).parent / "models").exists()):
        settings.tool_paths.realesrgan = detected_realesrgan
    else:
        settings.tool_paths.realesrgan = current_realesrgan or detected_realesrgan

    if detected_rife and not Path(current_rife).exists():
        settings.tool_paths.rife = detected_rife
    else:
        settings.tool_paths.rife = current_rife or detected_rife
    return settings


def _emit(progress: ProgressCallback, value: int, message: str) -> None:
    if progress:
        progress(value, message)


def _read_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Upscaler/0.1",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_release(tool_def: dict) -> dict:
    if tool_def.get("release_tag"):
        return _read_json(GITHUB_TAG_API.format(repo=tool_def["repo"], tag=tool_def["release_tag"]))
    return _read_json(GITHUB_LATEST_API.format(repo=tool_def["repo"]))


def download_release_tool(tool_key: str, progress: ProgressCallback = None) -> str:
    if tool_key not in TOOL_DEFS:
        raise ValueError(f"Unsupported tool: {tool_key}")

    tool_def = TOOL_DEFS[tool_key]
    dest_dir = tool_root() / tool_key
    shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)

    _emit(progress, 5, f"Fetching release metadata for {tool_def['label']}")
    release = _read_release(tool_def)

    asset = None
    for candidate in release.get("assets", []):
        if tool_def["asset_contains"] in candidate.get("name", ""):
            asset = candidate
            break
    if not asset:
        raise RuntimeError(f"Windows asset not found for {tool_def['label']}")

    archive_path = dest_dir / asset["name"]
    _emit(progress, 20, f"Downloading {asset['name']}")
    with urllib.request.urlopen(asset["browser_download_url"], timeout=300) as response:
        archive_path.write_bytes(response.read())

    _emit(progress, 75, f"Extracting {asset['name']}")
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(dest_dir)

    archive_path.unlink(missing_ok=True)

    exe_name = tool_def["exe_name"]
    for path in dest_dir.rglob(exe_name):
        _emit(progress, 100, f"{tool_def['label']} ready")
        return str(path.resolve(strict=False))

    raise RuntimeError(f"Executable {exe_name} not found after extracting {asset['name']}")
