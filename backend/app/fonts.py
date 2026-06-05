from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import UploadFile

from .config import settings
from .models import FontInfo


FONT_EXTENSIONS = {".otc", ".otf", ".ttc", ".ttf"}
FONT_MEDIA_TYPES = {
    ".otc": "font/collection",
    ".otf": "font/otf",
    ".ttc": "font/collection",
    ".ttf": "font/ttf",
}


def _is_supported_font(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in FONT_EXTENSIONS and not path.name.startswith(".")


def _font_family(filename: str) -> str:
    digest = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:12]
    return f"v2e-font-{digest}"


def _font_info(path: Path) -> FontInfo:
    return FontInfo(
        id=path.name,
        name=path.stem,
        family=_font_family(path.name),
        filename=path.name,
        url=f"/api/fonts/{quote(path.name)}/file",
    )


def list_fonts() -> list[FontInfo]:
    settings.ensure_dirs()
    return [
        _font_info(path)
        for path in sorted(settings.fonts_dir.iterdir(), key=lambda item: item.name.casefold())
        if _is_supported_font(path)
    ]


def _clean_filename(filename: str | None) -> str:
    name = Path(filename or "font").name.replace("\x00", "").strip()
    name = re.sub(r"[\r\n\t/\\]+", "-", name)
    suffix = Path(name).suffix.lower()
    if suffix not in FONT_EXTENSIONS:
        raise ValueError("only .ttf, .otf, .ttc, and .otc font files are supported")

    stem = Path(name).stem.strip(" .-_") or "font"
    return f"{stem}{suffix}"


def _available_font_path(filename: str) -> Path:
    candidate = settings.fonts_dir / filename
    if not candidate.exists():
        return candidate

    suffix = candidate.suffix
    stem = candidate.stem
    return settings.fonts_dir / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"


async def save_fonts(files: list[UploadFile]) -> list[FontInfo]:
    settings.ensure_dirs()
    if not files:
        raise ValueError("no font files uploaded")

    for file in files:
        filename = _clean_filename(file.filename)
        target_path = _available_font_path(filename)
        with target_path.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)

    return list_fonts()


def resolve_font_file(font_id: str | None) -> str | None:
    if not font_id:
        return settings.font_file
    if Path(font_id).name != font_id or "/" in font_id or "\\" in font_id:
        raise ValueError("invalid font id")

    base_dir = settings.fonts_dir.resolve()
    path = (settings.fonts_dir / font_id).resolve()
    try:
        path.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError("invalid font id") from exc

    if not _is_supported_font(path):
        raise ValueError("font not found")
    return str(path)


def get_font_file(font_id: str) -> Path:
    path = resolve_font_file(font_id)
    if not path:
        raise ValueError("font not found")
    return Path(path)


def font_media_type(path: Path) -> str:
    return FONT_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
