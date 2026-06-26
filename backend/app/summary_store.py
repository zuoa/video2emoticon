"""SQLite persistence for generated video summaries.

Keyed by (bvid, page) so a repeat request for the same BV + same P returns the
cached summary instantly without re-fetching the subtitle or re-calling the LLM.
Uses stdlib ``sqlite3`` (no new dependency) with WAL for safe concurrent reads.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator

from .config import settings

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS video_summaries (
    bvid              TEXT    NOT NULL,
    page              INTEGER NOT NULL,
    cid               INTEGER,
    title             TEXT,
    up                TEXT,
    duration          TEXT,
    overall_summary   TEXT    NOT NULL,
    key_points        TEXT    NOT NULL,
    quotes            TEXT    NOT NULL,
    markdown          TEXT    NOT NULL,
    subtitle_timeline TEXT    NOT NULL,
    subtitle_format   TEXT    NOT NULL,
    created_at        REAL    NOT NULL,
    updated_at        REAL    NOT NULL,
    PRIMARY KEY (bvid, page)
)
"""


def _db_path():
    return settings.summaries_dir / "summaries.db"


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    settings.ensure_dirs()
    conn = sqlite3.connect(_db_path(), timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create the table if missing. Safe to call repeatedly (IF NOT EXISTS)."""
    try:
        with _connect() as conn:
            conn.execute(_CREATE_TABLE_SQL)
    except sqlite3.Error:
        logger.exception("Failed to initialize summary database")


def _row_to_payload(row: sqlite3.Row) -> dict:
    return {
        "bv": row["bvid"],
        "page": row["page"],
        "cid": row["cid"],
        "title": row["title"],
        "up": row["up"],
        "duration": row["duration"],
        "overall_summary": row["overall_summary"],
        "key_points": json.loads(row["key_points"] or "[]"),
        "quotes": json.loads(row["quotes"] or "[]"),
        "markdown": row["markdown"],
        "subtitle_timeline": row["subtitle_timeline"],
        "subtitle_format": row["subtitle_format"] or "txt",
    }


def get_summary(bvid: str, page: int) -> dict | None:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM video_summaries WHERE bvid = ? AND page = ?",
                (bvid, page),
            ).fetchone()
    except sqlite3.Error:
        logger.exception("Failed to read summary for %s P%s", bvid, page)
        return None
    return _row_to_payload(row) if row else None


def get_subtitle_timeline(bvid: str, page: int) -> str | None:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT subtitle_timeline FROM video_summaries WHERE bvid = ? AND page = ?",
                (bvid, page),
            ).fetchone()
    except sqlite3.Error:
        logger.exception("Failed to read subtitle for %s P%s", bvid, page)
        return None
    return row["subtitle_timeline"] if row else None


def save_summary(payload: dict) -> None:
    """Insert a summary, or update it if (bvid, page) already exists."""
    now = time.time()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO video_summaries (
                    bvid, page, cid, title, up, duration,
                    overall_summary, key_points, quotes, markdown,
                    subtitle_timeline, subtitle_format, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bvid, page) DO UPDATE SET
                    cid = excluded.cid,
                    title = excluded.title,
                    up = excluded.up,
                    duration = excluded.duration,
                    overall_summary = excluded.overall_summary,
                    key_points = excluded.key_points,
                    quotes = excluded.quotes,
                    markdown = excluded.markdown,
                    subtitle_timeline = excluded.subtitle_timeline,
                    subtitle_format = excluded.subtitle_format,
                    updated_at = excluded.updated_at
                """,
                (
                    payload["bv"],
                    payload["page"],
                    payload.get("cid"),
                    payload.get("title"),
                    payload.get("up"),
                    payload.get("duration"),
                    payload["overall_summary"],
                    json.dumps(payload["key_points"], ensure_ascii=False),
                    json.dumps(payload["quotes"], ensure_ascii=False),
                    payload["markdown"],
                    payload["subtitle_timeline"],
                    payload.get("subtitle_format", "txt"),
                    now,
                    now,
                ),
            )
    except sqlite3.Error:
        logger.exception("Failed to persist summary for %s P%s", payload.get("bv"), payload.get("page"))


def delete_summary(bvid: str, page: int) -> bool:
    try:
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM video_summaries WHERE bvid = ? AND page = ?",
                (bvid, page),
            )
            return cur.rowcount > 0
    except sqlite3.Error:
        logger.exception("Failed to delete summary for %s P%s", bvid, page)
        return False
