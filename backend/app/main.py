from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from .bili_subtitle import NoSubtitleError, fetch_image_bytes
from .config import settings
from .ffmpeg_tools import VideoProcessingError, build_audio_clip, build_gif, validate_crop
from .fonts import font_media_type, get_font_file, list_fonts, save_fonts
from .models import (
    AudioExtractRequest,
    BilibiliPagesResponse,
    BilibiliRequest,
    ErrorResponse,
    ExportRequest,
    ExportResponse,
    FontInfo,
    KeyPoint,
    SummaryRequest,
    SummaryResponse,
    VideoInfo,
)
from .storage import (
    delete_old_videos,
    download_bilibili,
    extract_bilibili_page,
    extract_bv,
    get_video_file,
    get_video_metadata,
    list_bilibili_pages,
    save_upload,
)
from . import summary_service
from . import summary_store
from .summary_service import SummaryConfigError


settings.ensure_dirs()

logger = logging.getLogger(__name__)
SOURCE_VIDEO_RETENTION_SECONDS = 24 * 60 * 60
SOURCE_VIDEO_CLEANUP_INTERVAL_SECONDS = 60 * 60
cleanup_task: asyncio.Task[None] | None = None

app = FastAPI(
    title="Video to Any",
    description="Convert uploaded videos or Bilibili BV ids into multiple output formats.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    issues = [
        {
            "loc": ".".join(str(part) for part in error.get("loc", [])),
            "msg": error.get("msg", "invalid value"),
            "type": error.get("type", "validation_error"),
        }
        for error in exc.errors()
    ]
    logger.warning("Request validation failed: path=%s errors=%s", request.url.path, issues)
    detail = "; ".join(f"{issue['loc']}: {issue['msg']}" for issue in issues)
    return JSONResponse(
        status_code=400,
        content={
            "detail": f"请求参数无效: {detail}" if detail else "请求参数无效",
            "errors": issues,
        },
    )


async def cleanup_old_videos_loop() -> None:
    while True:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(delete_old_videos, SOURCE_VIDEO_RETENTION_SECONDS)
        await asyncio.sleep(SOURCE_VIDEO_CLEANUP_INTERVAL_SECONDS)


@app.on_event("startup")
async def start_source_video_cleanup() -> None:
    global cleanup_task
    cleanup_task = asyncio.create_task(cleanup_old_videos_loop())


@app.on_event("startup")
async def init_summary_store() -> None:
    summary_store.init_db()


@app.on_event("shutdown")
async def stop_source_video_cleanup() -> None:
    if cleanup_task is None:
        return
    cleanup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cleanup_task


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def get_config() -> dict[str, str]:
    """Frontend-facing config (e.g. the QR landing URL for share images)."""
    return {"site_url": settings.site_url}


@app.post(
    "/api/videos/upload",
    response_model=VideoInfo,
    responses={400: {"model": ErrorResponse}},
)
async def upload_video(file: UploadFile = File(...)) -> VideoInfo:
    try:
        return await save_upload(file)
    except VideoProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/videos/bilibili/pages",
    response_model=BilibiliPagesResponse,
    responses={400: {"model": ErrorResponse}},
)
async def bilibili_video_pages(request: BilibiliRequest) -> BilibiliPagesResponse:
    try:
        return await asyncio.to_thread(list_bilibili_pages, request.bv, request.page)
    except VideoProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/videos/bilibili",
    response_model=VideoInfo,
    responses={400: {"model": ErrorResponse}},
)
async def bilibili_video(request: BilibiliRequest) -> VideoInfo:
    try:
        return await asyncio.to_thread(download_bilibili, request.bv, request.page)
    except VideoProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/audio/extract",
    response_model=ExportResponse,
    responses={400: {"model": ErrorResponse}},
)
async def audio_extract(request: AudioExtractRequest) -> ExportResponse:
    try:
        metadata = get_video_metadata(request.video_id)
        output_path = await asyncio.to_thread(
            build_audio_clip,
            Path(metadata["source_path"]),
            settings.outputs_dir,
            request.start_time,
            request.end_time,
            request.format,
            float(metadata["duration"]),
            request.enhance,
        )
    except VideoProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ExportResponse(
        filename=output_path.name,
        download_url=f"/api/outputs/{output_path.name}",
        size_bytes=output_path.stat().st_size,
    )


@app.post(
    "/api/summary/generate",
    response_model=SummaryResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def summary_generate(request: SummaryRequest) -> SummaryResponse:
    try:
        bv = extract_bv(request.bv)
        page = request.page or extract_bilibili_page(request.bv) or 1
    except VideoProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        payload = await asyncio.to_thread(summary_service.generate_summary, bv, page)
    except NoSubtitleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SummaryConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except VideoProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SummaryResponse(**payload)


@app.get(
    "/api/summary/{bvid}",
    response_model=SummaryResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_stored_summary(bvid: str, page: int = 1) -> SummaryResponse:
    """Return a previously generated summary by BV (+page).

    Powers the shareable result page at #/summary/{bvid}: after generation the
    summary is persisted in summary_store, so this looks it up without
    re-calling the LLM. Returns 404 when no summary exists yet.
    """
    try:
        normalized_bv = extract_bv(bvid)
    except VideoProcessingError:
        normalized_bv = bvid
    summary = summary_store.get_summary(normalized_bv, page)
    if summary is None:
        raise HTTPException(status_code=404, detail="未找到该视频的总结，请先在总结页生成。")
    summary["cached"] = True
    summary["subtitle_url"] = summary_service.subtitle_download_url(normalized_bv, page)
    return SummaryResponse(**summary)


@app.get("/api/summary/subtitle/{bvid}/{page}")
def summary_subtitle(bvid: str, page: int) -> PlainTextResponse:
    timeline = summary_store.get_subtitle_timeline(bvid, page)
    if timeline is None:
        raise HTTPException(status_code=404, detail="subtitle not found")
    filename = f"{bvid}_P{page}.txt"
    return PlainTextResponse(
        content=timeline,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _cover_media_type(cover_url: str) -> str:
    suffix = ""
    if "." in cover_url:
        suffix = cover_url.rsplit(".", 1)[-1].lower().split("?", 1)[0]
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(suffix, "image/jpeg")


@app.get("/api/summary/cover/{bvid}/{page}")
async def summary_cover(bvid: str, page: int) -> Response:
    """Same-origin proxy for a video's Bilibili cover.

    The cover CDN sends no CORS headers, so the frontend draws it through here to
    avoid cross-origin canvas tainting when html2canvas rasterizes the share card.
    """
    summary = summary_store.get_summary(bvid, page)
    cover_url = (summary or {}).get("cover_url") or ""
    if not cover_url:
        raise HTTPException(status_code=404, detail="cover not found")
    try:
        content = await asyncio.to_thread(fetch_image_bytes, cover_url)
    except VideoProcessingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=_cover_media_type(cover_url),
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/videos/{video_id}/file")
def video_file(video_id: str) -> FileResponse:
    try:
        path = get_video_file(video_id)
    except VideoProcessingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    media_type = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".avi": "video/x-msvideo",
    }.get(path.suffix.lower(), "video/mp4")
    return FileResponse(path, media_type=media_type)


@app.get("/api/fonts", response_model=list[FontInfo])
def fonts() -> list[FontInfo]:
    return list_fonts()


@app.post(
    "/api/fonts/upload",
    response_model=list[FontInfo],
    responses={400: {"model": ErrorResponse}},
)
async def upload_fonts(files: list[UploadFile] = File(...)) -> list[FontInfo]:
    try:
        return await save_fonts(files)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/fonts/{font_id}/file")
def font_file(font_id: str) -> FileResponse:
    try:
        path = get_font_file(font_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type=font_media_type(path), filename=path.name)


@app.post(
    "/api/gif/export",
    response_model=ExportResponse,
    responses={400: {"model": ErrorResponse}},
)
async def export_gif(request: ExportRequest) -> ExportResponse:
    try:
        metadata = get_video_metadata(request.video_id)
        validate_crop(request.crop, int(metadata["width"]), int(metadata["height"]))
        output_path = await asyncio.to_thread(
            build_gif,
            Path(metadata["source_path"]),
            settings.outputs_dir,
            request,
            float(metadata["duration"]),
        )
    except VideoProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    response = ExportResponse(
        filename=output_path.name,
        download_url=f"/api/outputs/{output_path.name}",
        size_bytes=output_path.stat().st_size,
    )
    return response


def output_media_type(path: Path) -> str:
    return {
        ".gif": "image/gif",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
    }.get(path.suffix.lower(), "application/octet-stream")


@app.get("/api/outputs/{filename}")
def output_file(filename: str) -> FileResponse:
    path = settings.outputs_dir / filename
    if "/" in filename or "\\" in filename or not path.exists():
        raise HTTPException(status_code=404, detail="output not found")
    return FileResponse(path, media_type=output_media_type(path), filename=filename)


if settings.frontend_dist.exists():
    app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")


@app.get("/{_:path}")
def missing_frontend() -> dict[str, str]:
    return {
        "message": "Frontend build not found. Run the Vite build or use the Docker image.",
    }
