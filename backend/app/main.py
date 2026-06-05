from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .ffmpeg_tools import VideoProcessingError, build_gif, validate_crop
from .fonts import font_media_type, get_font_file, list_fonts, save_fonts
from .models import BilibiliRequest, ErrorResponse, ExportRequest, ExportResponse, FontInfo, VideoInfo
from .storage import download_bilibili, get_video_file, get_video_metadata, save_upload


settings.ensure_dirs()

app = FastAPI(
    title="Video2Emoticon",
    description="Create custom GIF emoticons from uploaded videos or Bilibili BV ids.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


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
    "/api/videos/bilibili",
    response_model=VideoInfo,
    responses={400: {"model": ErrorResponse}},
)
async def bilibili_video(request: BilibiliRequest) -> VideoInfo:
    try:
        return await asyncio.to_thread(download_bilibili, request.bv)
    except VideoProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/videos/{video_id}/file")
def video_file(video_id: str) -> FileResponse:
    try:
        path = get_video_file(video_id)
    except VideoProcessingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path)


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

    return ExportResponse(
        filename=output_path.name,
        download_url=f"/api/outputs/{output_path.name}",
        size_bytes=output_path.stat().st_size,
    )


@app.get("/api/outputs/{filename}")
def output_file(filename: str) -> FileResponse:
    path = settings.outputs_dir / filename
    if "/" in filename or "\\" in filename or not path.exists():
        raise HTTPException(status_code=404, detail="output not found")
    return FileResponse(path, media_type="image/gif", filename=filename)


if settings.frontend_dist.exists():
    app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")


@app.get("/{_:path}")
def missing_frontend() -> dict[str, str]:
    return {
        "message": "Frontend build not found. Run the Vite build or use the Docker image.",
    }
