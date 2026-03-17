from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from uuid import uuid4


@dataclass(slots=True)
class ToolPaths:
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    realesrgan: str = ""
    rife: str = ""


@dataclass(slots=True)
class AppSettings:
    tool_paths: ToolPaths = field(default_factory=ToolPaths)
    last_input_dir: str = ""
    last_output_dir: str = ""


@dataclass(slots=True)
class VideoMetadata:
    width: int
    height: int
    fps: float
    duration: float
    frame_count: int
    has_audio: bool
    video_codec: str = ""
    audio_codec: str = ""


@dataclass(slots=True)
class JobOptions:
    input_path: Path
    output_path: Path
    profile_name: str
    target_height: int
    target_fps: float
    denoise_strength: int
    sharpen_strength: float
    grain_strength: float
    use_deband: bool
    upscale_backend: str
    interpolation_backend: str
    upscale_model: str
    interpolation_model: str
    encoder: str
    cq_value: int
    keep_temp: bool
    job_kind: str = "video"
    preview_only: bool = False
    temporal_strength: float = 0.0
    image_scale: int = 4
    image_output_format: str = "png"
    brightness: float = 1.0
    contrast: float = 1.0
    saturation: float = 1.0
    gamma: float = 1.0
    auto_contrast: bool = False
    overwrite: bool = False


@dataclass(slots=True)
class JobRecord:
    options: JobOptions
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    status: str = "Queued"
    progress: int = 0
    stage: str = "Waiting"
    error: Optional[str] = None
