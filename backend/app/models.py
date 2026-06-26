from __future__ import annotations

from enum import Enum
from typing import Any, Literal

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
    page: int | None = Field(default=None, ge=1, le=9999)


class BilibiliPageInfo(BaseModel):
    page: int = Field(ge=1)
    title: str
    duration: float | None = None
    cid: int | None = Field(default=None, ge=1)


class BilibiliPagesResponse(BaseModel):
    bv: str
    selected_page: int = Field(ge=1)
    pages: list[BilibiliPageInfo]


class AudioExtractRequest(BaseModel):
    video_id: str = Field(min_length=1)
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    format: Literal["mp3", "m4a", "wav"] = "mp3"
    enhance: bool = False

    @model_validator(mode="after")
    def validate_time_range(self) -> "AudioExtractRequest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self


class CropRect(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    @field_validator("x", "y", "width", "height", mode="before")
    @classmethod
    def normalize_pixel_value(cls, value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return round(value)
        if isinstance(value, str):
            try:
                return round(float(value))
            except ValueError:
                return value
        return value


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
    end_time: float | None = Field(default=None, gt=0)
    duration: float | None = Field(default=None, gt=0)
    crop: CropRect
    output_width: int | None = Field(default=None, gt=0)
    fps: int = Field(default=12, ge=6, le=24)
    speed_factor: float = Field(default=1.0, ge=1 / 16, le=16)
    loop: bool = True
    text: TextLayer = Field(default_factory=TextLayer)

    @field_validator("output_width", mode="before")
    @classmethod
    def normalize_output_width(cls, value: Any) -> Any:
        if value is False or value == "":
            return None
        return value

    @field_validator("speed_factor", mode="before")
    @classmethod
    def normalize_speed_factor(cls, value: Any) -> Any:
        if value is False or value == "" or value == 0 or value == "0":
            return 1.0
        return value

    @field_validator("duration", mode="before")
    @classmethod
    def normalize_duration(cls, value: Any) -> Any:
        if value is False or value == "":
            return None
        return value

    @model_validator(mode="after")
    def validate_time_range(self) -> "ExportRequest":
        if self.duration is not None:
            self.end_time = self.start_time + self.duration
        if self.end_time is None:
            raise ValueError("duration or end_time is required")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self


class ExportResponse(BaseModel):
    filename: str
    download_url: str
    size_bytes: int


class ErrorResponse(BaseModel):
    detail: str


class SummaryRequest(BaseModel):
    bv: str = Field(min_length=3, max_length=200)
    page: int | None = Field(default=None, ge=1, le=9999)


class KeyPoint(BaseModel):
    time: str
    seconds: int = Field(ge=0)
    title: str
    detail: str
    url: str


class SummaryResponse(BaseModel):
    bv: str
    page: int
    cid: int | None = None
    title: str | None = None
    up: str | None = None
    duration: str | None = None
    overall_summary: str
    key_points: list[KeyPoint]
    quotes: list[str]
    markdown: str
    subtitle_url: str
    subtitle_format: str
    cached: bool
