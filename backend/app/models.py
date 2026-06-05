from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class SourceType(str, Enum):
    upload = "upload"
    bilibili = "bilibili"


class VideoInfo(BaseModel):
    id: str
    source_type: SourceType
    filename: str
    duration: float
    width: int
    height: int
    preview_url: str


class BilibiliRequest(BaseModel):
    bv: str = Field(min_length=3, max_length=200)


class CropRect(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class TextLayer(BaseModel):
    enabled: bool = False
    content: str = Field(default="", max_length=120)
    position: Literal["top", "center", "bottom"] = "bottom"
    font_size: int = Field(default=32, ge=12, le=96)
    font_id: str | None = Field(default=None, max_length=255)
    color: str = "#ffffff"
    stroke_color: str = "#111111"
    box: bool = True
    box_color: str = "#000000"
    box_opacity: float = Field(default=0.45, ge=0, le=1)

    @field_validator("color", "stroke_color", "box_color")
    @classmethod
    def validate_hex_color(cls, value: str) -> str:
        if not value.startswith("#") or len(value) not in (4, 7):
            raise ValueError("color must be a hex value")
        allowed = set("0123456789abcdefABCDEF#")
        if any(char not in allowed for char in value):
            raise ValueError("color must be a hex value")
        return value.lower()


class FontInfo(BaseModel):
    id: str
    name: str
    family: str
    filename: str
    url: str


class ExportRequest(BaseModel):
    video_id: str = Field(min_length=1)
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    crop: CropRect
    fps: int = Field(default=12, ge=6, le=24)
    loop: bool = True
    text: TextLayer = Field(default_factory=TextLayer)

    @model_validator(mode="after")
    def validate_time_range(self) -> "ExportRequest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self


class ExportResponse(BaseModel):
    filename: str
    download_url: str
    size_bytes: int


class ErrorResponse(BaseModel):
    detail: str
