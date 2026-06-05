from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings:
    def __init__(self) -> None:
        data_dir = os.getenv("DATA_DIR")
        self.data_dir = Path(data_dir) if data_dir else PROJECT_ROOT / "data"
        self.uploads_dir = self.data_dir / "uploads"
        self.downloads_dir = self.data_dir / "downloads"
        self.outputs_dir = self.data_dir / "outputs"
        self.cookies_dir = self.data_dir / "cookies"
        self.bilibili_cookies_file = os.getenv("BILIBILI_COOKIES_FILE") or os.getenv(
            "BILIBILI_COOKIE_FILE"
        )
        self.bilibili_cookies = os.getenv("BILIBILI_COOKIES") or os.getenv(
            "BILIBILI_COOKIE"
        )
        self.bilibili_cookie_header = os.getenv("BILIBILI_COOKIE_HEADER")
        self.frontend_dist = Path(
            os.getenv("FRONTEND_DIST", str(PROJECT_ROOT / "frontend" / "dist"))
        )
        self.cors_origins = [
            item.strip()
            for item in os.getenv("CORS_ORIGINS", "*").split(",")
            if item.strip()
        ]
        self.font_file = self._find_font_file()

    def ensure_dirs(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self.cookies_dir.mkdir(parents=True, exist_ok=True)
        self._write_bilibili_cookies_from_env()

    def prepare_bilibili_cookies_file(self) -> Path | None:
        if self.bilibili_cookies_file:
            path = Path(self.bilibili_cookies_file).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"BILIBILI_COOKIES_FILE not found: {path}")
            return path

        self._write_bilibili_cookies_from_env()
        default_path = self.cookies_dir / "bilibili.cookies.txt"
        if default_path.exists():
            return default_path
        return None

    def _write_bilibili_cookies_from_env(self) -> None:
        if not self.bilibili_cookies:
            return

        cookie_text = self.bilibili_cookies
        if "\\n" in cookie_text and "\n" not in cookie_text:
            cookie_text = cookie_text.replace("\\n", "\n")
        if not cookie_text.endswith("\n"):
            cookie_text += "\n"

        target = self.cookies_dir / "bilibili.cookies.txt"
        target.write_text(cookie_text, encoding="utf-8")
        target.chmod(0o600)

    def _find_font_file(self) -> str | None:
        configured = os.getenv("FONT_FILE")
        if configured and Path(configured).exists():
            return configured

        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
        for candidate in candidates:
            if Path(candidate).exists():
                return candidate
        return None


settings = Settings()
