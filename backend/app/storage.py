from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import UploadFile

from .config import settings
from .ffmpeg_tools import VideoProcessingError, probe_video, run_checked
from .models import SourceType, VideoInfo


BV_PATTERN = re.compile(r"(BV[0-9A-Za-z]{10})")


def _safe_extension(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if not suffix or len(suffix) > 12:
        return ".mp4"
    if not re.fullmatch(r"\.[a-z0-9]+", suffix):
        return ".mp4"
    return suffix


def _video_dir(video_id: str, source_type: SourceType) -> Path:
    base = settings.uploads_dir if source_type == SourceType.upload else settings.downloads_dir
    return base / video_id


def _metadata_path(video_id: str, source_type: SourceType) -> Path:
    return _video_dir(video_id, source_type) / "meta.json"


def _write_metadata(info: VideoInfo, source_path: Path) -> None:
    path = _metadata_path(info.id, info.source_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = info.model_dump(mode="json")
    payload["source_path"] = str(source_path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_metadata(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _video_info_from_path(
    video_id: str, source_type: SourceType, filename: str, source_path: Path
) -> VideoInfo:
    duration, width, height = probe_video(source_path)
    return VideoInfo(
        id=video_id,
        source_type=source_type,
        filename=filename,
        duration=round(duration, 3),
        width=width,
        height=height,
        preview_url=f"/api/videos/{video_id}/file",
    )


async def save_upload(file: UploadFile) -> VideoInfo:
    settings.ensure_dirs()
    video_id = uuid.uuid4().hex
    target_dir = _video_dir(video_id, SourceType.upload)
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = file.filename or "uploaded-video"
    target_path = target_dir / f"source{_safe_extension(filename)}"

    with target_path.open("wb") as output:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)

    try:
        info = _video_info_from_path(video_id, SourceType.upload, filename, target_path)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    _write_metadata(info, target_path)
    return info


def extract_bv(value: str) -> str:
    match = BV_PATTERN.search(value.strip())
    if not match:
        raise VideoProcessingError("invalid BV id")
    return match.group(1)


def download_bilibili(value: str) -> VideoInfo:
    settings.ensure_dirs()
    bv = extract_bv(value)
    video_id = uuid.uuid4().hex
    target_dir = _video_dir(video_id, SourceType.bilibili)
    target_dir.mkdir(parents=True, exist_ok=True)

    output_template = str(target_dir / "source.%(ext)s")
    url = f"https://www.bilibili.com/video/{bv}"
    try:
        cookies_file = settings.prepare_bilibili_cookies_file()
    except FileNotFoundError as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise VideoProcessingError(str(exc)) from exc

    command = [
        "yt-dlp",
        "--no-playlist",
        "-f",
        "bestvideo+bestaudio/best",
        "--merge-output-format",
        "mp4",
        "-o",
        output_template,
    ]
    if cookies_file:
        command.extend(["--cookies", str(cookies_file)])
    if settings.bilibili_cookie_header:
        command.extend(["--add-headers", f"Cookie:{settings.bilibili_cookie_header}"])
    command.append(url)

    try:
        run_checked(command, timeout=600)
    except VideoProcessingError as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        message = str(exc)
        if "HTTP Error 412" in message:
            raise VideoProcessingError(
                "Bilibili returned HTTP 412. Configure a valid Bilibili cookie with "
                "BILIBILI_COOKIES_FILE or BILIBILI_COOKIE_HEADER, then retry."
            ) from exc
        raise
    except subprocess.TimeoutExpired:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    candidates = sorted(
        path for path in target_dir.iterdir() if path.is_file() and path.name.startswith("source.")
    )
    if not candidates:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise VideoProcessingError("download completed but no video file was produced")

    source_path = candidates[0]
    try:
        info = _video_info_from_path(video_id, SourceType.bilibili, bv, source_path)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    _write_metadata(info, source_path)
    return info


def get_video_metadata(video_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", video_id):
        raise VideoProcessingError("invalid video id")

    for source_type in (SourceType.upload, SourceType.bilibili):
        path = _metadata_path(video_id, source_type)
        if path.exists():
            return _read_metadata(path)
    raise VideoProcessingError("video not found")


def get_video_file(video_id: str) -> Path:
    metadata = get_video_metadata(video_id)
    path = Path(metadata["source_path"])
    if not path.exists():
        raise VideoProcessingError("video file not found")
    return path
