from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import Callable

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .config import temp_root
from .models import AppSettings, JobOptions, VideoMetadata
from .tools import realesrgan_model_available

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, str], None]


class PipelineError(RuntimeError):
    pass


@dataclass(slots=True)
class ActiveProcess:
    process: subprocess.Popen[str] | None = None


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def probe_video(ffprobe_path: str, input_path: Path) -> VideoMetadata:
    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(input_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)

    video_stream = next((stream for stream in payload["streams"] if stream["codec_type"] == "video"), None)
    if not video_stream:
        raise PipelineError("No video stream found in input file.")

    audio_stream = next((stream for stream in payload["streams"] if stream["codec_type"] == "audio"), None)
    fps_text = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/1"
    numerator, denominator = fps_text.split("/", maxsplit=1)
    fps = float(numerator) / float(denominator or 1)
    duration = float(video_stream.get("duration") or payload["format"].get("duration") or 0)
    frame_count_text = video_stream.get("nb_frames")
    frame_count = int(frame_count_text) if frame_count_text and frame_count_text.isdigit() else max(1, round(duration * fps))

    return VideoMetadata(
        width=int(video_stream["width"]),
        height=int(video_stream["height"]),
        fps=fps,
        duration=duration,
        frame_count=frame_count,
        has_audio=audio_stream is not None,
        video_codec=video_stream.get("codec_name", ""),
        audio_codec=audio_stream.get("codec_name", "") if audio_stream else "",
    )


def list_image_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(path for path in input_path.rglob("*") if path.suffix.lower() in IMAGE_EXTENSIONS)


def build_color_filter(options: JobOptions) -> str:
    brightness = max(-1.0, min(1.0, options.brightness - 1.0))
    contrast = max(0.2, min(3.0, options.contrast))
    saturation = max(0.0, min(3.0, options.saturation))
    gamma = max(0.1, min(3.0, options.gamma))
    if all(abs(value - 1.0) < 0.001 for value in [options.brightness, options.contrast, options.saturation, options.gamma]):
        return ""
    return f"eq=brightness={brightness:.3f}:contrast={contrast:.3f}:saturation={saturation:.3f}:gamma={gamma:.3f}"


def gamma_correct(image: Image.Image, gamma: float) -> Image.Image:
    if abs(gamma - 1.0) < 0.001:
        return image
    inv_gamma = 1.0 / gamma
    table = [min(255, max(0, int(((value / 255.0) ** inv_gamma) * 255.0))) for value in range(256)]
    if image.mode == "RGBA":
        rgb = image.convert("RGB").point(table * 3)
        rgb.putalpha(image.getchannel("A"))
        return rgb
    return image.point(table * len(image.getbands()))


def apply_image_adjustments(image: Image.Image, options: JobOptions) -> Image.Image:
    result = ImageOps.exif_transpose(image)
    alpha = result.getchannel("A") if "A" in result.getbands() else None
    working = result.convert("RGB")

    if options.auto_contrast:
        working = ImageOps.autocontrast(working)
    if options.denoise_strength > 0:
        size = 3 if options.denoise_strength <= 2 else 5
        working = working.filter(ImageFilter.MedianFilter(size=size))
    if abs(options.brightness - 1.0) >= 0.001:
        working = ImageEnhance.Brightness(working).enhance(options.brightness)
    if abs(options.contrast - 1.0) >= 0.001:
        working = ImageEnhance.Contrast(working).enhance(options.contrast)
    if abs(options.saturation - 1.0) >= 0.001:
        working = ImageEnhance.Color(working).enhance(options.saturation)
    if abs(options.gamma - 1.0) >= 0.001:
        working = gamma_correct(working, options.gamma)
    if options.sharpen_strength > 0:
        working = ImageEnhance.Sharpness(working).enhance(1.0 + options.sharpen_strength * 2.0)

    if alpha is not None:
        working = working.convert("RGBA")
        working.putalpha(alpha)
    return working


def build_prefilter(options: JobOptions) -> str:
    filters: list[str] = []
    if options.denoise_strength > 0:
        luma = max(1.0, options.denoise_strength * 1.2)
        filters.append(f"hqdn3d={luma:.1f}:{luma / 1.2:.1f}:6:6")
    if options.use_deband:
        filters.append("gradfun=radius=10:strength=0.6")
    return ",".join(filters)


def build_temporal_refine_filter(options: JobOptions) -> str:
    if options.temporal_strength <= 0:
        return ""
    spatial = 0.4 + min(options.temporal_strength, 1.0) * 0.6
    temporal = 2.0 + min(options.temporal_strength, 1.0) * 4.0
    return f"hqdn3d={spatial:.1f}:{spatial:.1f}:{temporal:.1f}:{temporal:.1f}"


def build_postfilter(options: JobOptions, target_height: int) -> str:
    filters: list[str] = []
    if target_height > 0:
        filters.append(f"scale=-2:{target_height}:flags=lanczos")
    color_filter = build_color_filter(options)
    if color_filter:
        filters.append(color_filter)
    temporal_filter = build_temporal_refine_filter(options)
    if temporal_filter:
        filters.append(temporal_filter)
    if options.sharpen_strength > 0:
        amount = max(0.1, min(options.sharpen_strength, 1.5))
        filters.append(f"unsharp=5:5:{amount:.2f}:5:5:0.0")
    if options.grain_strength > 0:
        grain = int(max(1, min(options.grain_strength * 18, 12)))
        filters.append(f"noise=alls={grain}:allf=t+u")
    return ",".join(filters)


def build_direct_filtergraph(options: JobOptions, target_height: int, target_fps: float) -> str:
    filters: list[str] = []
    if options.denoise_strength > 0:
        luma = max(1.0, options.denoise_strength * 1.2)
        filters.append(f"hqdn3d={luma:.1f}:{luma / 1.2:.1f}:6:6")
    if target_height > 0:
        filters.append(f"scale=-2:{target_height}:flags=lanczos")
    color_filter = build_color_filter(options)
    if color_filter:
        filters.append(color_filter)
    temporal_filter = build_temporal_refine_filter(options)
    if temporal_filter:
        filters.append(temporal_filter)
    if options.interpolation_backend == "minterpolate" and target_fps > 0:
        filters.append(f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1")
    if options.sharpen_strength > 0:
        amount = max(0.1, min(options.sharpen_strength, 1.5))
        filters.append(f"unsharp=5:5:{amount:.2f}:5:5:0.0")
    if options.use_deband:
        filters.append("gradfun=radius=8:strength=0.4")
    if options.grain_strength > 0:
        grain = int(max(1, min(options.grain_strength * 18, 12)))
        filters.append(f"noise=alls={grain}:allf=t+u")
    return ",".join(filters)


def parse_progress_microseconds(raw_value: str) -> float | None:
    value = raw_value.strip()
    if not value or value.upper() == "N/A":
        return None
    try:
        return float(value)
    except ValueError:
        return None


class PipelineRunner:
    def __init__(self, settings: AppSettings, options: JobOptions, log: LogCallback, progress: ProgressCallback) -> None:
        self.settings = settings
        self.options = options
        self.log = log
        self.progress = progress
        self.cancel_event = Event()
        self.active = ActiveProcess()

    def cancel(self) -> None:
        self.cancel_event.set()
        if self.active.process and self.active.process.poll() is None:
            self.active.process.terminate()

    def run(self) -> None:
        if self.options.job_kind == "image":
            self._run_image_pipeline()
            return

        self.options.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.progress(2, "Analyzing input")
        meta = probe_video(self.settings.tool_paths.ffprobe, self.options.input_path)
        self.log(
            f"Input: {meta.width}x{meta.height} @ {meta.fps:.3f} fps, duration {meta.duration:.2f}s, frames ~{meta.frame_count}"
        )
        if self.options.upscale_backend == "realesrgan":
            self.log("Local AI VSR path active: Real-ESRGAN restore/upscale with temporal refinement.")

        target_height = self.options.target_height or meta.height
        target_fps = self.options.target_fps or meta.fps
        upscale_scale = 4 if target_height >= 2160 else 2
        use_ai_upscale = (
            self.options.upscale_backend == "realesrgan"
            and bool(self.settings.tool_paths.realesrgan)
            and realesrgan_model_available(self.settings.tool_paths.realesrgan, self.options.upscale_model, upscale_scale)
        )
        use_ai_interp = self.options.interpolation_backend == "rife" and bool(self.settings.tool_paths.rife)
        effective_options = self.options

        if self.options.upscale_backend == "realesrgan" and not use_ai_upscale:
            self.log("Real-ESRGAN binary or model pack missing, falling back to ffmpeg scaling.")
        if self.options.interpolation_backend == "rife" and not use_ai_interp:
            self.log("RIFE missing, falling back to ffmpeg minterpolate if target fps is set.")
            effective_options = replace(self.options, interpolation_backend="minterpolate")

        if use_ai_upscale or use_ai_interp:
            self._run_ai_pipeline(meta, target_height, target_fps, use_ai_upscale, use_ai_interp)
        else:
            original = self.options
            self.options = effective_options
            try:
                self._run_direct_ffmpeg(meta, target_height, target_fps)
            finally:
                self.options = original

    def _run_direct_ffmpeg(self, meta: VideoMetadata, target_height: int, target_fps: float) -> None:
        self.progress(5, "Processing with FFmpeg")
        filtergraph = build_direct_filtergraph(self.options, target_height, target_fps)
        cmd = [self.settings.tool_paths.ffmpeg, "-y", "-i", str(self.options.input_path)]
        if filtergraph:
            cmd.extend(["-vf", filtergraph])
        cmd.extend(self._encoder_args())
        cmd.extend(["-pix_fmt", "yuv420p", str(self.options.output_path)])
        self._run_process(cmd, "ffmpeg-direct", total_duration=max(meta.duration, 0.1), progress_span=(5, 100))

    def _run_ai_pipeline(
        self,
        meta: VideoMetadata,
        target_height: int,
        target_fps: float,
        use_ai_upscale: bool,
        use_ai_interp: bool,
    ) -> None:
        upscale_scale = 4 if target_height >= 2160 else 2
        work_dir = temp_root() / self.options.output_path.stem
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)

        audio_path = work_dir / "audio.mka"
        raw_frames = work_dir / "frames_raw"
        current_frames = raw_frames
        upscaled_frames = work_dir / "frames_upscaled"
        interp_frames = work_dir / "frames_interp"
        raw_frames.mkdir(parents=True, exist_ok=True)

        try:
            if meta.has_audio:
                self.progress(8, "Extracting audio")
                audio_cmd = [self.settings.tool_paths.ffmpeg, "-y", "-i", str(self.options.input_path), "-vn", "-c:a", "copy", str(audio_path)]
                self._run_process(audio_cmd, "extract-audio")

            self.progress(12, "Extracting frames")
            extract_cmd = [self.settings.tool_paths.ffmpeg, "-y", "-i", str(self.options.input_path)]
            prefilter = build_prefilter(self.options)
            if prefilter:
                extract_cmd.extend(["-vf", prefilter])
            extract_cmd.extend([str(raw_frames / "%08d.png")])
            self._run_process(extract_cmd, "extract-frames", total_duration=max(meta.duration, 0.1), progress_span=(12, 35))

            if use_ai_upscale:
                self.progress(38, "Upscaling with Real-ESRGAN")
                upscaled_frames.mkdir(parents=True, exist_ok=True)
                scale = upscale_scale
                realesrgan_parent = Path(self.settings.tool_paths.realesrgan).resolve().parent
                upscale_cmd = [
                    self.settings.tool_paths.realesrgan,
                    "-i",
                    str(raw_frames),
                    "-o",
                    str(upscaled_frames),
                    "-m",
                    "models",
                    "-n",
                    self.options.upscale_model,
                    "-s",
                    str(scale),
                    "-f",
                    "png",
                ]
                self._run_process(upscale_cmd, "realesrgan", progress_span=(38, 63), cwd=realesrgan_parent)
                current_frames = upscaled_frames

            if use_ai_interp and target_fps > meta.fps:
                self.progress(65, "Interpolating with RIFE")
                interp_frames.mkdir(parents=True, exist_ok=True)
                target_frame_count = max(meta.frame_count + 1, round(meta.duration * target_fps))
                rife_parent = Path(self.settings.tool_paths.rife).resolve().parent
                if (rife_parent / self.options.interpolation_model).exists():
                    rife_model_ref = self.options.interpolation_model
                else:
                    rife_model_ref = f"models/{self.options.interpolation_model}"
                rife_cmd = [
                    self.settings.tool_paths.rife,
                    "-i",
                    str(current_frames),
                    "-o",
                    str(interp_frames),
                    "-n",
                    str(target_frame_count),
                    "-m",
                    rife_model_ref,
                    "-f",
                    "%08d.png",
                ]
                if target_height >= 2160:
                    rife_cmd.append("-u")
                self._run_process(rife_cmd, "rife", progress_span=(65, 88), cwd=rife_parent)
                current_frames = interp_frames

            self.progress(90, "Encoding output")
            input_fps = target_fps if use_ai_interp and target_fps > meta.fps else meta.fps
            postfilter = build_postfilter(self.options, target_height)
            encode_cmd = [self.settings.tool_paths.ffmpeg, "-y", "-framerate", f"{input_fps:.6f}", "-i", str(current_frames / "%08d.png")]
            if meta.has_audio and audio_path.exists():
                encode_cmd.extend(["-i", str(audio_path)])
            if postfilter:
                encode_cmd.extend(["-vf", postfilter])
            encode_cmd.extend(self._encoder_args())
            if meta.has_audio and audio_path.exists():
                encode_cmd.extend(["-c:a", "copy", "-shortest"])
            encode_cmd.extend(["-pix_fmt", "yuv420p", str(self.options.output_path)])
            self._run_process(encode_cmd, "encode", total_duration=max(meta.duration, 0.1), progress_span=(90, 100))
        finally:
            if not self.options.keep_temp:
                shutil.rmtree(work_dir, ignore_errors=True)

    def _run_image_pipeline(self) -> None:
        input_path = self.options.input_path
        output_dir = self.options.output_path
        output_dir.mkdir(parents=True, exist_ok=True)

        files = list_image_files(input_path)
        if not files:
            raise PipelineError("No supported images found.")

        self.progress(2, "Scanning images")
        self.log(f"Found {len(files)} image(s) to process.")

        use_ai_upscale = (
            self.options.upscale_backend == "realesrgan"
            and bool(self.settings.tool_paths.realesrgan)
            and realesrgan_model_available(self.settings.tool_paths.realesrgan, self.options.upscale_model, self.options.image_scale)
        )
        if self.options.upscale_backend == "realesrgan" and not use_ai_upscale:
            self.log("Real-ESRGAN portable binary or model pack missing, falling back to Pillow Lanczos upscale.")

        if use_ai_upscale:
            self.log(f"Using local AI image upscale backend with model {self.options.upscale_model}.")
            self._run_image_ai_pipeline(files, output_dir)
        else:
            self._run_image_pillow_pipeline(files, output_dir)

    def _run_image_ai_pipeline(self, files: list[Path], output_dir: Path) -> None:
        work_dir = temp_root() / f"{self.options.output_path.name}_images"
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)

        try:
            self.progress(10, "Running Real-ESRGAN on images")
            realesrgan_parent = Path(self.settings.tool_paths.realesrgan).resolve().parent
            source_input = self.options.input_path.resolve()
            if source_input.is_file():
                ai_output_target = work_dir / f"{source_input.stem}_ai.{self.options.image_output_format}"
                ai_output_target.parent.mkdir(parents=True, exist_ok=True)
            else:
                ai_output_target = work_dir / "ai_output"
                ai_output_target.mkdir(parents=True, exist_ok=True)
            upscale_cmd = [
                self.settings.tool_paths.realesrgan,
                "-i",
                str(source_input),
                "-o",
                str(ai_output_target),
                "-m",
                "models",
                "-n",
                self.options.upscale_model,
                "-s",
                str(self.options.image_scale),
                "-f",
                self.options.image_output_format,
            ]
            self._run_process(upscale_cmd, "realesrgan-images", progress_span=(10, 70), cwd=realesrgan_parent)

            if ai_output_target.is_file():
                ai_files = [ai_output_target] if ai_output_target.exists() else []
            else:
                ai_files = list_image_files(ai_output_target)
            if not ai_files:
                raise PipelineError("Real-ESRGAN did not produce any output images.")

            for index, image_path in enumerate(ai_files, start=1):
                output_path = output_dir / f"{image_path.stem}_lifted.{self.options.image_output_format}"
                self._apply_image_postprocess(image_path, output_path)
                progress = 70 + math.floor((index / max(1, len(ai_files))) * 30)
                self.progress(progress, "Post-processing images")
        finally:
            if not self.options.keep_temp:
                shutil.rmtree(work_dir, ignore_errors=True)

    def _run_image_pillow_pipeline(self, files: list[Path], output_dir: Path) -> None:
        total = max(1, len(files))
        for index, image_path in enumerate(files, start=1):
            with Image.open(image_path) as source:
                working = ImageOps.exif_transpose(source)
                if self.options.image_scale > 1:
                    width = max(1, working.width * self.options.image_scale)
                    height = max(1, working.height * self.options.image_scale)
                    working = working.resize((width, height), Image.Resampling.LANCZOS)
                working = apply_image_adjustments(working, self.options)
                output_path = output_dir / f"{image_path.stem}_lifted.{self.options.image_output_format}"
                self._save_image(working, output_path)
            self.progress(5 + math.floor((index / total) * 95), "Processing images")

    def _apply_image_postprocess(self, src_path: Path, output_path: Path) -> None:
        with Image.open(src_path) as source:
            working = apply_image_adjustments(source, self.options)
            self._save_image(working, output_path)

    def _save_image(self, image: Image.Image, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fmt = self.options.image_output_format.lower()
        if output_path.exists() and not self.options.overwrite:
            output_path = output_path.with_name(f"{output_path.stem}_new{output_path.suffix}")

        save_target = image
        save_kwargs: dict[str, object] = {}
        if fmt in {"jpg", "jpeg"}:
            if save_target.mode not in {"RGB", "L"}:
                save_target = save_target.convert("RGB")
            save_kwargs["quality"] = 95
            save_kwargs["subsampling"] = 0
        elif fmt == "png":
            save_kwargs["compress_level"] = 2
        save_target.save(output_path, **save_kwargs)

    def _encoder_args(self) -> list[str]:
        encoder = self.options.encoder
        cq = str(self.options.cq_value)
        if encoder in {"h264_nvenc", "hevc_nvenc"}:
            return ["-c:v", encoder, "-preset", "p5", "-cq", cq, "-b:v", "0"]
        return ["-c:v", "libx264", "-preset", "slow", "-crf", cq]

    def _run_process(
        self,
        cmd: list[str],
        label: str,
        total_duration: float | None = None,
        progress_span: tuple[int, int] | None = None,
        cwd: str | Path | None = None,
    ) -> None:
        if self.cancel_event.is_set():
            raise PipelineError("Job cancelled.")

        if label.startswith("ffmpeg") or label in {"extract-frames", "encode"}:
            cmd = [cmd[0], "-hide_banner", "-progress", "pipe:1", "-nostats", *cmd[1:]]

        self.log("$ " + " ".join(f'"{part}"' if " " in part else part for part in cmd))
        process = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.active.process = process

        start, end = progress_span or (0, 0)
        for raw_line in process.stdout or []:
            line = raw_line.strip()
            if not line:
                continue
            self.log(line)
            if total_duration and line.startswith("out_time_ms=") and end > start:
                microseconds = parse_progress_microseconds(line.split("=", maxsplit=1)[1])
                if microseconds is not None:
                    ratio = min(1.0, microseconds / 1_000_000 / total_duration)
                    self.progress(start + math.floor((end - start) * ratio), label)
            elif total_duration and line.startswith("progress=end") and end > start:
                self.progress(end, label)

            if self.cancel_event.is_set() and process.poll() is None:
                process.terminate()
                raise PipelineError("Job cancelled.")

        return_code = process.wait()
        self.active.process = None
        if return_code != 0:
            raise PipelineError(f"{label} failed with exit code {return_code}")
