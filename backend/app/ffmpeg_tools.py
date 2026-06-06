from __future__ import annotations

import json
import math
import shutil
import subprocess
import uuid
from pathlib import Path

from .fonts import resolve_font_file
from .models import CropRect, ExportRequest, TextLayer


class VideoProcessingError(RuntimeError):
    pass


def run_checked(command: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise VideoProcessingError(f"missing command: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoProcessingError(f"{command[0]} timed out") from exc

    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise VideoProcessingError(message[-1200:])
    return completed


def _rotation_degrees(stream: dict) -> int:
    candidates: list[object] = []
    tags = stream.get("tags") or {}
    candidates.append(tags.get("rotate"))
    for side_data in stream.get("side_data_list") or []:
        candidates.append(side_data.get("rotation"))

    for raw_value in candidates:
        if raw_value is None:
            continue
        try:
            return int(round(float(str(raw_value).strip()))) % 360
        except ValueError:
            continue
    return 0


def probe_video(path: Path) -> tuple[float, int, int]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    completed = run_checked(command, timeout=30)
    payload = json.loads(completed.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise VideoProcessingError("no video stream found")

    stream = streams[0]
    width = int(stream["width"])
    height = int(stream["height"])
    rotation = _rotation_degrees(stream)
    display_width, display_height = (height, width) if rotation in (90, 270) else (width, height)
    duration_raw = stream.get("duration") or payload.get("format", {}).get("duration")
    duration = float(duration_raw or 0)
    if duration <= 0:
        raise VideoProcessingError("video duration could not be detected")

    return duration, display_width, display_height


def validate_crop(crop: CropRect, video_width: int, video_height: int) -> None:
    if crop.x + crop.width > video_width or crop.y + crop.height > video_height:
        raise VideoProcessingError("crop rectangle is outside the video bounds")


def _escape_filter_value(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace(",", "\\,")
    )
    return f"'{escaped}'"


def _drawtext_filter(text: TextLayer, text_file: Path, font_file: str | None) -> str:
    if text.position == "top":
        y_expr = "14"
    elif text.position == "center":
        y_expr = "(h-text_h)/2"
    else:
        y_expr = "h-text_h-14"

    options = [
        f"textfile={_escape_filter_value(str(text_file))}",
        f"fontsize={text.font_size}",
        f"fontcolor={text.color}",
        f"borderw={max(2, math.ceil(text.font_size / 12))}",
        f"bordercolor={text.stroke_color}",
        "x=(w-text_w)/2",
        f"y={y_expr}",
    ]
    if font_file:
        options.append(f"fontfile={_escape_filter_value(font_file)}")
    if text.box:
        options.extend(
            [
                "box=1",
                f"boxcolor={text.box_color}@{text.box_opacity:.2f}",
                "boxborderw=8",
            ]
        )

    return f"drawtext={':'.join(options)}"


def _optimize_gif(path: Path) -> None:
    if shutil.which("gifsicle") is None:
        return

    try:
        run_checked(["gifsicle", "-O3", "--lossy=30", "-b", str(path)], timeout=120)
    except VideoProcessingError:
        try:
            run_checked(["gifsicle", "-O3", "-b", str(path)], timeout=120)
        except VideoProcessingError:
            return


def build_gif(input_path: Path, output_dir: Path, request: ExportRequest, duration: float) -> Path:
    end_time = request.end_time
    if end_time is None:
        raise VideoProcessingError("duration or end_time is required")
    if end_time > duration + 0.05:
        raise VideoProcessingError("time range exceeds video duration")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = f"{uuid.uuid4().hex}.gif"
    output_path = output_dir / output_name

    crop = request.crop
    output_width = request.output_width or crop.width
    filters = [
        f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y}",
        f"scale={output_width}:-1:flags=lanczos",
    ]
    if not math.isclose(request.speed_factor, 1.0):
        filters.append(f"setpts={1 / request.speed_factor:.8f}*PTS")
    filters.append(f"fps={request.fps}")

    text_file: Path | None = None
    if request.text.enabled and request.text.content.strip():
        try:
            font_file = resolve_font_file(request.text.font_id)
        except ValueError as exc:
            raise VideoProcessingError(str(exc)) from exc
        text_file = output_dir / f"{output_path.stem}.txt"
        text_file.write_text(request.text.content.strip(), encoding="utf-8")
        filters.append(_drawtext_filter(request.text, text_file, font_file))

    video_chain = ",".join(filters)
    filter_complex = (
        f"[0:v]{video_chain},split[v0][v1];"
        "[v0]palettegen=stats_mode=diff[p];"
        "[v1][p]paletteuse=dither=bayer:bayer_scale=3"
    )
    loop_value = "0" if request.loop else "1"
    clip_duration = end_time - request.start_time

    command = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{request.start_time:.3f}",
        "-t",
        f"{clip_duration:.3f}",
        "-i",
        str(input_path),
        "-filter_complex",
        filter_complex,
        "-loop",
        loop_value,
        str(output_path),
    ]
    try:
        run_checked(command, timeout=180)
    finally:
        if text_file and text_file.exists():
            text_file.unlink()

    _optimize_gif(output_path)
    return output_path


def build_audio_clip(
    input_path: Path,
    output_dir: Path,
    start_time: float,
    clip_duration: float,
    output_format: str,
    source_duration: float,
) -> Path:
    if clip_duration <= 0:
        raise VideoProcessingError("audio clip duration must be greater than 0")
    if start_time + clip_duration > source_duration + 0.05:
        raise VideoProcessingError("time range exceeds video duration")
    if output_format not in {"mp3", "m4a", "wav"}:
        raise VideoProcessingError("unsupported audio format")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{uuid.uuid4().hex}.{output_format}"
    codec_args = {
        "mp3": ["-codec:a", "libmp3lame", "-b:a", "192k"],
        "m4a": ["-codec:a", "aac", "-b:a", "192k"],
        "wav": ["-codec:a", "pcm_s16le", "-ar", "44100"],
    }[output_format]

    command = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start_time:.3f}",
        "-t",
        f"{clip_duration:.3f}",
        "-i",
        str(input_path),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "2",
        *codec_args,
        str(output_path),
    ]
    run_checked(command, timeout=180)
    return output_path
