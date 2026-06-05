from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import UploadFile

from .config import settings
from .ffmpeg_tools import VideoProcessingError, probe_video, run_checked
from .models import BilibiliPageInfo, BilibiliPagesResponse, SourceType, VideoInfo


BV_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}")
BILIBILI_CACHE_NAMESPACE = uuid.NAMESPACE_URL
_bilibili_download_locks: dict[str, threading.Lock] = {}
_bilibili_download_locks_lock = threading.Lock()


@dataclass(frozen=True)
class ParsedBilibiliInput:
    bv: str
    page: int | None = None


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
    created_at = time.time()
    payload = info.model_dump(mode="json")
    payload["source_path"] = str(source_path)
    payload["created_at"] = created_at
    payload["last_used_at"] = created_at
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_metadata(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _touch_metadata(path: Path, metadata: dict) -> None:
    metadata["last_used_at"] = time.time()
    try:
        path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


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


def _parse_positive_page(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        page = int(value)
    except ValueError:
        return None
    return page if page >= 1 else None


def _is_bilibili_host(hostname: str | None) -> bool:
    host = (hostname or "").lower()
    return host == "bilibili.com" or host.endswith(".bilibili.com")


def parse_bilibili_input(value: str) -> ParsedBilibiliInput:
    text = value.strip()
    if not text:
        raise VideoProcessingError("请输入 BV 号或 Bilibili 视频地址")

    if BV_PATTERN.fullmatch(text):
        return ParsedBilibiliInput(bv=text)

    url_text = text
    if "://" not in url_text and re.match(r"(?:[a-z0-9-]+\.)*bilibili\.com/", url_text, re.IGNORECASE):
        url_text = f"https://{url_text}"

    parsed = urlparse(url_text)
    if parsed.scheme not in {"http", "https"} or not _is_bilibili_host(parsed.hostname):
        raise VideoProcessingError("请输入 BV 号或 bilibili.com/video/BV... 地址")

    match = re.search(r"/video/(BV[0-9A-Za-z]{10})(?:/|$)", parsed.path)
    if not match:
        raise VideoProcessingError("请输入 bilibili.com/video/BV... 视频地址")

    page_values = parse_qs(parsed.query).get("p")
    page = _parse_positive_page(page_values[0]) if page_values else None
    if page_values and page is None:
        raise VideoProcessingError("Bilibili URL 的 p 参数必须是正整数")
    return ParsedBilibiliInput(bv=match.group(1), page=page)


def extract_bv(value: str) -> str:
    return parse_bilibili_input(value).bv


def extract_bilibili_page(value: str) -> int | None:
    return parse_bilibili_input(value).page


def _bilibili_auth_args() -> list[str]:
    try:
        cookies_file = settings.prepare_bilibili_cookies_file()
    except FileNotFoundError as exc:
        raise VideoProcessingError(str(exc)) from exc

    args: list[str] = []
    if cookies_file:
        args.extend(["--cookies", str(cookies_file)])
    if settings.bilibili_cookie_header:
        args.extend(["--add-headers", f"Cookie:{settings.bilibili_cookie_header}"])
    return args


def _raise_bilibili_download_error(exc: VideoProcessingError) -> None:
    message = str(exc)
    if "HTTP Error 412" in message:
        raise VideoProcessingError(
            "Bilibili returned HTTP 412. Configure a valid Bilibili cookie with "
            "BILIBILI_COOKIES_FILE or BILIBILI_COOKIE_HEADER, then retry."
        ) from exc
    raise exc


def _clean_page_title(title: object, page: int) -> str:
    text = str(title or "").strip()
    return text or f"P{page}"


def _optional_duration(value: object) -> float | None:
    if value is None:
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return round(duration, 3) if duration > 0 else None


def _bilibili_cache_id(bv: str, page: int) -> str:
    return uuid.uuid5(BILIBILI_CACHE_NAMESPACE, f"video2emoticon:bilibili:{bv}:p{page}").hex


def _bilibili_download_lock(video_id: str) -> threading.Lock:
    with _bilibili_download_locks_lock:
        lock = _bilibili_download_locks.get(video_id)
        if lock is None:
            lock = threading.Lock()
            _bilibili_download_locks[video_id] = lock
        return lock


def _source_candidates(target_dir: Path) -> list[Path]:
    if not target_dir.exists():
        return []
    return sorted(
        path
        for path in target_dir.iterdir()
        if path.is_file() and path.name.startswith("source.") and not path.name.endswith(".part")
    )


def _cached_bilibili_info(video_id: str, filename: str) -> VideoInfo | None:
    metadata_path = _metadata_path(video_id, SourceType.bilibili)
    if metadata_path.exists():
        try:
            metadata = _read_metadata(metadata_path)
            source_path_raw = metadata.get("source_path")
            if source_path_raw and Path(source_path_raw).is_file():
                _touch_metadata(metadata_path, metadata)
                return VideoInfo.model_validate(metadata)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    for source_path in _source_candidates(_video_dir(video_id, SourceType.bilibili)):
        try:
            info = _video_info_from_path(video_id, SourceType.bilibili, filename, source_path)
        except Exception:
            continue
        _write_metadata(info, source_path)
        return info

    return None


def list_bilibili_pages(value: str, page: int | None = None) -> BilibiliPagesResponse:
    parsed_input = parse_bilibili_input(value)
    bv = parsed_input.bv
    url = f"https://www.bilibili.com/video/{bv}"
    command = [
        "yt-dlp",
        "--dump-single-json",
        "--skip-download",
        "--flat-playlist",
        "--no-warnings",
        *(_bilibili_auth_args()),
        url,
    ]

    try:
        completed = run_checked(command, timeout=120)
    except VideoProcessingError as exc:
        _raise_bilibili_download_error(exc)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VideoProcessingError("Bilibili page info could not be parsed") from exc

    pages: list[BilibiliPageInfo] = []
    entries = payload.get("entries") or []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        raw_page = entry.get("page") or entry.get("page_number") or index
        try:
            page = int(raw_page)
        except (TypeError, ValueError):
            page = index
        if page < 1:
            page = index
        pages.append(
            BilibiliPageInfo(
                page=page,
                title=_clean_page_title(entry.get("title"), page),
                duration=_optional_duration(entry.get("duration")),
            )
        )

    if not pages:
        pages.append(
            BilibiliPageInfo(
                page=1,
                title=_clean_page_title(payload.get("title"), 1),
                duration=_optional_duration(payload.get("duration")),
            )
        )

    selected_page = page or parsed_input.page or 1
    if selected_page not in {item.page for item in pages}:
        raise VideoProcessingError(f"Bilibili page P{selected_page} does not exist")

    return BilibiliPagesResponse(bv=bv, selected_page=selected_page, pages=pages)


def download_bilibili(value: str, page: int | None = None) -> VideoInfo:
    settings.ensure_dirs()
    parsed_input = parse_bilibili_input(value)
    bv = parsed_input.bv
    selected_page = page or parsed_input.page or 1
    if selected_page < 1:
        raise VideoProcessingError("invalid Bilibili page")
    video_id = _bilibili_cache_id(bv, selected_page)
    filename = f"{bv} P{selected_page}"
    target_dir = _video_dir(video_id, SourceType.bilibili)

    with _bilibili_download_lock(video_id):
        cached_info = _cached_bilibili_info(video_id, filename)
        if cached_info:
            return cached_info

        shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        output_template = str(target_dir / "source.%(ext)s")
        url = f"https://www.bilibili.com/video/{bv}?p={selected_page}"
        try:
            auth_args = _bilibili_auth_args()
        except VideoProcessingError as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise exc

        command = [
            "yt-dlp",
            "--no-playlist",
            "-f",
            "bestvideo+bestaudio/best",
            "--merge-output-format",
            "mp4",
            "-o",
            output_template,
            *auth_args,
        ]
        command.append(url)

        try:
            run_checked(command, timeout=600)
        except VideoProcessingError as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            _raise_bilibili_download_error(exc)
        except subprocess.TimeoutExpired:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise

        candidates = _source_candidates(target_dir)
        if not candidates:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise VideoProcessingError("download completed but no video file was produced")

        source_path = candidates[0]
        try:
            info = _video_info_from_path(video_id, SourceType.bilibili, filename, source_path)
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
            metadata = _read_metadata(path)
            _touch_metadata(path, metadata)
            return metadata
    raise VideoProcessingError("video not found")


def get_video_file(video_id: str) -> Path:
    metadata = get_video_metadata(video_id)
    path = Path(metadata["source_path"])
    if not path.exists():
        raise VideoProcessingError("video file not found")
    return path


def _video_dir_timestamp(path: Path) -> float:
    metadata_path = path / "meta.json"
    if metadata_path.exists():
        try:
            metadata = _read_metadata(metadata_path)
            return float(
                metadata.get("last_used_at")
                or metadata.get("created_at")
                or metadata_path.stat().st_mtime
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return metadata_path.stat().st_mtime
    return path.stat().st_mtime


def delete_old_videos(max_age_seconds: float, now: float | None = None) -> int:
    current_time = time.time() if now is None else now
    cutoff = current_time - max_age_seconds
    deleted = 0

    for base_dir in (settings.uploads_dir, settings.downloads_dir):
        if not base_dir.exists():
            continue
        for path in base_dir.iterdir():
            if not path.is_dir():
                continue
            try:
                timestamp = _video_dir_timestamp(path)
            except OSError:
                continue
            if timestamp <= cutoff:
                shutil.rmtree(path, ignore_errors=True)
                deleted += 1

    return deleted
