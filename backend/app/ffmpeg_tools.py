from __future__ import annotations

import json
import math
import subprocess
import uuid
from pathlib import Path

from .config import settings
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


def probe_video(path: Path) -> tuple[float, int, int]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration:format=duration",
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
    duration_raw = stream.get("duration") or payload.get("format", {}).get("duration")
    duration = float(duration_raw or 0)
    if duration <= 0:
        raise VideoProcessingError("video duration could not be detected")

    return duration, width, height


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


def _drawtext_filter(text: TextLayer, text_file: Path) -> str:
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
    if settings.font_file:
        options.append(f"fontfile={_escape_filter_value(settings.font_file)}")
    if text.box:
        options.extend(
            [
                "box=1",
                f"boxcolor={text.box_color}@{text.box_opacity:.2f}",
                "boxborderw=8",
            ]
        )

    return f"drawtext={':'.join(options)}"


def build_gif(input_path: Path, output_dir: Path, request: ExportRequest, duration: float) -> Path:
    if request.end_time > duration + 0.05:
        raise VideoProcessingError("time range exceeds video duration")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = f"{uuid.uuid4().hex}.gif"
    output_path = output_dir / output_name

    crop = request.crop
    output_width = min(max(crop.width, 240), 480)
    filters = [
        f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y}",
        f"scale={output_width}:-1:flags=lanczos",
        f"fps={request.fps}",
    ]

    text_file: Path | None = None
    if request.text.enabled and request.text.content.strip():
        text_file = output_dir / f"{output_path.stem}.txt"
        text_file.write_text(request.text.content.strip(), encoding="utf-8")
        filters.append(_drawtext_filter(request.text, text_file))

    video_chain = ",".join(filters)
    filter_complex = (
        f"[0:v]{video_chain},split[v0][v1];"
        "[v0]palettegen=stats_mode=diff[p];"
        "[v1][p]paletteuse=dither=bayer:bayer_scale=3"
    )
    loop_value = "0" if request.loop else "1"
    clip_duration = request.end_time - request.start_time

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

    return output_path
