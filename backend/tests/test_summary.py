"""Tests for the BV video summary tool (subtitle fetch + LLM map-reduce).

Covers the logic ported from dfb (WBI signing, subtitle timeline, JSON parsing)
and the new behavior (line-boundary chunking, timestamp/key-point normalization,
quote dedup, markdown-safe boilerplate stripping).
"""

from __future__ import annotations

import hashlib
import pytest
from unittest.mock import patch
from urllib.parse import urlencode

from backend.app import bili_subtitle, summary_service, summary_store


# --- WBI signing (ported from dfb) ---

def test_sign_params_hashes_urlencoded_values():
    with (
        patch.object(bili_subtitle, "_fetch_wbi_keys", return_value="mixin-key"),
        patch.object(bili_subtitle.time, "time", return_value=1700000000),
    ):
        signed = bili_subtitle._sign_params({"keyword": "电饭宝 & live", "page": 1})

    unsigned = {k: v for k, v in sorted(signed.items()) if k != "w_rid"}
    encoded_query = urlencode(unsigned, doseq=True)
    raw_query = "&".join(f"{k}={v}" for k, v in unsigned.items())

    assert "%E7%94%B5%E9%A5%AD%E5%AE%9D" in encoded_query  # 电饭宝 url-encoded
    assert signed["wts"] == 1700000000
    assert signed["w_rid"] == hashlib.md5((encoded_query + "mixin-key").encode()).hexdigest()
    assert signed["w_rid"] != hashlib.md5((raw_query + "mixin-key").encode()).hexdigest()


# --- Cookie parsing ---

def test_parse_netscape_cookie_text_filters_bilibili_domain():
    text = "\n".join([
        "# Netscape HTTP Cookie File",
        ".bilibili.com\tTRUE\t/\tTRUE\t1893456000\tSESSDATA\tsess",
        "#HttpOnly_.bilibili.com\tTRUE\t/\tTRUE\t1893456000\tbuvid3\tbuvid",
        ".example.com\tTRUE\t/\tFALSE\t1893456000\tSESSDATA\tignored",
    ])
    cookies = bili_subtitle._parse_netscape_cookie_text(text)
    assert cookies == {"SESSDATA": "sess", "buvid3": "buvid"}


def test_parse_cookie_header_splits_pairs():
    cookies = bili_subtitle._parse_cookie_header("SESSDATA=abc; bili_jct=xyz; junk; =empty")
    assert cookies == {"SESSDATA": "abc", "bili_jct": "xyz"}


# --- Subtitle timeline formatting ---

def test_format_subtitle_timeline_keeps_source_timestamps():
    timeline = bili_subtitle.format_subtitle_timeline([
        {"from": 66.2, "content": " 主播现场改词  "},
        {"from": 3723.8, "content": "弹幕刷屏"},
    ])
    assert timeline == "[01:06] 主播现场改词\n[01:02:03] 弹幕刷屏"


def test_format_subtitle_timeline_drops_empty_content():
    timeline = bili_subtitle.format_subtitle_timeline([
        {"from": 5, "content": "  "},
        {"from": 10, "content": "有效内容"},
    ])
    assert timeline == "[00:10] 有效内容"


# --- JSON extraction & summary response parsing ---

def test_extract_json_object_strips_code_fences():
    assert summary_service._extract_json_object("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert summary_service._extract_json_object("prefix {\"a\": 1} suffix") == {"a": 1}
    assert summary_service._extract_json_object("not json at all") is None


def test_parse_summary_response_reads_key_points_and_quotes():
    content = (
        '{"overall_summary":"这个视频讲了核心内容。",'
        '"key_points":[{"time":"01:06","title":"要点A","detail":"说明A"}],'
        '"quotes":["金句一"]}'
    )
    summary, points, quotes = summary_service._parse_summary_response(content)
    assert summary == "讲了核心内容。"  # boilerplate "这个视频" stripped
    assert points == [{"time": "01:06", "title": "要点A", "detail": "说明A"}]
    assert quotes == ["金句一"]


def test_parse_summary_response_falls_back_to_plain_text():
    summary, points, quotes = summary_service._parse_summary_response("本期视频主要介绍了一些工具。")
    assert summary == "介绍了一些工具。"
    assert points == []
    assert quotes == []


# --- Chunking (line boundaries, no data loss) ---

def test_split_timeline_single_chunk_when_short():
    timeline = "[00:01] a\n[00:02] b\n[00:03] c"
    chunks = summary_service._split_timeline(timeline, max_chars=9000)
    assert chunks == [timeline]


def test_split_timeline_splits_on_line_boundaries_without_loss():
    lines = [f"[{i:04d}] line content number {i}" for i in range(120)]
    timeline = "\n".join(lines)
    chunks = summary_service._split_timeline(timeline, max_chars=300)
    assert len(chunks) > 1
    # every chunk is made of whole lines and the rejoin is lossless
    assert "\n".join(chunks) == timeline
    for chunk in chunks:
        assert chunk  # no empty chunks
        assert all(line.startswith("[") for line in chunk.split("\n"))


def test_compute_highlight_count_scales_and_caps():
    assert summary_service._compute_highlight_count(None) == 4
    assert summary_service._compute_highlight_count(0) == 4
    assert summary_service._compute_highlight_count(180) == 3     # 3 min
    assert summary_service._compute_highlight_count(3600) == 16   # 60 min -> capped


# --- Key point & quote normalization ---

def test_normalize_key_points_sorts_dedups_and_builds_urls():
    raw = [
        {"time": "02:10", "title": "要点B", "detail": "说明B"},
        {"time": "01:06", "title": "要点A", "detail": "说明A"},
        {"time": "not-a-time", "title": "坏数据"},          # dropped: unparseable time
        {"time": "01:06", "title": "要点A", "detail": "说明A"},  # dedup
    ]
    points = summary_service._normalize_key_points(raw, "BV1xx411c7mD", 1)
    assert [p["seconds"] for p in points] == [66, 130]
    assert points[0]["time"] == "01:06"
    assert points[0]["url"] == "https://www.bilibili.com/video/BV1xx411c7mD?t=66"
    assert points[1]["url"] == "https://www.bilibili.com/video/BV1xx411c7mD?t=130"


def test_bili_video_url_includes_page_and_timestamp():
    assert (
        summary_service._bili_video_url("BV1xx411c7mD", 2, 66)
        == "https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=66"
    )
    assert (
        summary_service._bili_video_url("BV1xx411c7mD", 1, 66)
        == "https://www.bilibili.com/video/BV1xx411c7mD?t=66"
    )


def test_normalize_quotes_dedups_trims_and_caps():
    quotes = summary_service._normalize_quotes([" 金句一 ", "金句一", "金句二", "", "x" * 200])
    assert quotes[0] == "金句一"
    assert quotes[1] == "金句二"
    assert len(quotes) == 3          # empty dropped, duplicate removed
    assert len(quotes[2]) <= 120     # over-long truncated


def test_clean_summary_strips_boilerplate_openings():
    assert summary_service._clean_summary("这个视频讲了一些内容。") == "讲了一些内容。"
    assert summary_service._clean_summary("本期视频主要内容介绍工具。") == "内容介绍工具。"
    assert summary_service._clean_summary("直接进入主题。") == "直接进入主题。"


# --- SQLite persistence ---

def _sample_payload(bv: str = "BV1xx411c7mD", page: int = 1, summary: str = "整体总结内容。") -> dict:
    return {
        "bv": bv,
        "page": page,
        "cid": 123456,
        "title": "示例标题",
        "up": "示例UP",
        "duration": "12:34",
        "cover_url": "https://i0.hdslb.com/sample.jpg",
        "overall_summary": summary,
        "key_points": [
            {"time": "01:06", "seconds": 66, "title": "要点", "detail": "说明", "url": "https://x?t=66"}
        ],
        "quotes": ["金句一", "金句二"],
        "markdown": "# 示例标题\n\n整体总结内容。",
        "subtitle_timeline": "[01:06] 第一句\n[02:00] 第二句",
        "subtitle_format": "txt",
    }


def test_summary_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(summary_store.settings, "summaries_dir", tmp_path)
    summary_store.init_db()

    assert summary_store.get_summary("BV1xx411c7mD", 1) is None
    assert summary_store.get_subtitle_timeline("BV1xx411c7mD", 1) is None

    payload = _sample_payload()
    summary_store.save_summary(payload)

    got = summary_store.get_summary("BV1xx411c7mD", 1)
    assert got is not None
    assert got["overall_summary"] == "整体总结内容。"
    assert got["key_points"] == payload["key_points"]
    assert got["quotes"] == payload["quotes"]
    assert got["cid"] == 123456
    assert got["cover_url"] == "https://i0.hdslb.com/sample.jpg"
    assert summary_store.get_subtitle_timeline("BV1xx411c7mD", 1) == payload["subtitle_timeline"]

    # different page is a distinct row
    assert summary_store.get_summary("BV1xx411c7mD", 2) is None


def test_summary_store_upsert_updates_existing_row(tmp_path, monkeypatch):
    monkeypatch.setattr(summary_store.settings, "summaries_dir", tmp_path)
    summary_store.init_db()

    summary_store.save_summary(_sample_payload(summary="第一版总结。"))
    summary_store.save_summary(_sample_payload(summary="更新后的总结。"))

    got = summary_store.get_summary("BV1xx411c7mD", 1)
    assert got is not None
    assert got["overall_summary"] == "更新后的总结。"
    assert summary_store.delete_summary("BV1xx411c7mD", 1) is True
    assert summary_store.get_summary("BV1xx411c7mD", 1) is None
    assert summary_store.delete_summary("BV1xx411c7mD", 1) is False  # already gone


def test_subtitle_download_url_uses_bvid_page_route():
    assert (
        summary_service.subtitle_download_url("BV1xx411c7mD", 1)
        == "/api/summary/subtitle/BV1xx411c7mD/1"
    )


def test_generate_summary_cache_hit_short_circuits_before_network(tmp_path, monkeypatch):
    """A cached row is returned instantly; no subtitle fetch or LLM call occurs."""
    monkeypatch.setattr(summary_store.settings, "summaries_dir", tmp_path)
    monkeypatch.setattr(summary_service.settings, "openai_api_key", "fake-key")
    summary_store.init_db()
    summary_store.save_summary(_sample_payload(summary="已缓存的总结。"))

    # If the cache miss path ran, fetch_subtitle would raise (no network / no subtitle).
    # Returning the cached row proves the short-circuit.
    result = summary_service.generate_summary("BV1xx411c7mD", 1)

    assert result["cached"] is True
    assert result["overall_summary"] == "已缓存的总结。"
    assert result["subtitle_url"] == "/api/summary/subtitle/BV1xx411c7mD/1"


def test_generate_summary_dedupes_concurrent_calls(tmp_path, monkeypatch):
    """Two concurrent generate_summary() calls for the same uncached BV run the
    subtitle fetch + LLM only once: the second blocks on the per-(bv,page) lock
    and returns the row the first stored."""
    import threading
    import time

    monkeypatch.setattr(summary_store.settings, "summaries_dir", tmp_path)
    monkeypatch.setattr(summary_service.settings, "openai_api_key", "fake-key")
    summary_store.init_db()

    fetch_calls = {"n": 0}
    chat_calls = {"n": 0}
    counter_lock = threading.Lock()

    fake_subtitle = bili_subtitle.SubtitleData(
        bv="BV1xx411c7mD",
        page=1,
        cid=1,
        title="标题",
        up="UP主",
        duration_seconds=120,
        timeline="[00:10] 一句话字幕",
        body=[],
        subtitle_url="",
        lan="zh",
        cover_url="https://i0.hdslb.com/cover.jpg",
    )

    def fake_fetch(bv, page):
        with counter_lock:
            fetch_calls["n"] += 1
        return fake_subtitle

    def fake_chat(prompt, max_tokens=1200):
        # Hold the generation lock briefly so the second caller is still waiting
        # when the first finishes — this is what makes the dedupe observable.
        time.sleep(0.1)
        with counter_lock:
            chat_calls["n"] += 1
        return '{"overall_summary":"概要内容。","key_points":[],"quotes":[]}'

    monkeypatch.setattr(summary_service, "fetch_subtitle", fake_fetch)
    monkeypatch.setattr(summary_service, "_chat", fake_chat)

    results: list[dict | None] = [None, None]
    errors: list[BaseException] = []

    def run(idx):
        try:
            results[idx] = summary_service.generate_summary("BV1xx411c7mD", 1)
        except BaseException as exc:  # noqa: BLE001 - capture for assertion
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert fetch_calls["n"] == 1   # subtitle fetched once, not twice
    assert chat_calls["n"] == 1    # LLM called once, not twice
    # both callers got the same summary; the waiter returned the stored row
    assert results[0] is not None and results[1] is not None
    assert results[0]["overall_summary"] == results[1]["overall_summary"]
    # the lock entry is cleaned up once generation finishes
    assert summary_service._generation_locks == {}


# --- Cover URL capture + share-image plumbing ---

_OLD_TABLE_SQL = """
CREATE TABLE video_summaries (
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


def test_init_db_migrates_cover_url_column(tmp_path, monkeypatch):
    """An older DB created before cover_url gains the column on init_db()."""
    monkeypatch.setattr(summary_store.settings, "summaries_dir", tmp_path)
    with summary_store._connect() as conn:
        conn.execute(_OLD_TABLE_SQL)
    with summary_store._connect() as conn:
        assert "cover_url" not in summary_store._existing_columns(conn)

    summary_store.init_db()

    with summary_store._connect() as conn:
        assert "cover_url" in summary_store._existing_columns(conn)
    # init_db() is idempotent — running again must not error on the re-add.
    summary_store.init_db()


def test_get_video_info_returns_cover_pic(monkeypatch):
    class _FakeResponse:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    class _FakeSession:
        def __init__(self, data):
            self._data = data

        def get(self, *args, **kwargs):
            return _FakeResponse(self._data)

    payload = {
        "code": 0,
        "data": {
            "cid": 1,
            "aid": 2,
            "title": "标题",
            "owner": {"name": "UP主"},
            "duration": 100,
            "pic": "https://i0.hdslb.com/cover.jpg",
            "pages": [],
        },
    }
    monkeypatch.setattr(bili_subtitle, "_get_http_session", lambda: _FakeSession(payload))
    monkeypatch.setattr(bili_subtitle, "_rate_limit", lambda: None)

    info = bili_subtitle.get_video_info("BV1xx411c7mD")
    assert info["pic"] == "https://i0.hdslb.com/cover.jpg"


def test_generate_summary_payload_carries_cover_url(tmp_path, monkeypatch):
    """A fresh (uncached) generation surfaces the cover captured from the subtitle fetch."""
    monkeypatch.setattr(summary_store.settings, "summaries_dir", tmp_path)
    monkeypatch.setattr(summary_service.settings, "openai_api_key", "fake-key")
    summary_store.init_db()

    fake_subtitle = bili_subtitle.SubtitleData(
        bv="BV1xx411c7mD",
        page=1,
        cid=1,
        title="标题",
        up="UP主",
        duration_seconds=120,
        timeline="[00:10] 一句话字幕",
        body=[],
        subtitle_url="",
        lan="zh",
        cover_url="https://i0.hdslb.com/cover.jpg",
    )
    monkeypatch.setattr(summary_service, "fetch_subtitle", lambda bv, page: fake_subtitle)
    monkeypatch.setattr(
        summary_service,
        "_chat",
        lambda prompt, max_tokens=1200: '{"overall_summary":"概要内容。","key_points":[],"quotes":[]}',
    )

    result = summary_service.generate_summary("BV1xx411c7mD", 1)
    assert result["cover_url"] == "https://i0.hdslb.com/cover.jpg"
    assert result["cached"] is False


def test_fetch_subtitle_prefers_video_title_over_part_name():
    """The summary title is the video-level title, not the per-P "part" name —
    which on time-segmented videos is a bare timestamp that would render as the
    title on the result page."""
    with (
        patch.object(bili_subtitle, "_rate_limit", lambda: None),
        patch.object(
            bili_subtitle,
            "get_video_info",
            lambda bv: {
                "cid": 1,
                "aid": 2,
                "title": "视频标题",
                "owner": {"name": "UP主"},
                "duration": 120,
                "pic": "cover.jpg",
                "pages": [{"page": 1, "part": "00:15:30", "cid": 1, "duration": 120}],
            },
        ),
        patch.object(
            bili_subtitle,
            "get_subtitle_urls",
            lambda bv, cid: [{"lan": "zh", "subtitle_url": "//x.json"}],
        ),
        patch.object(
            bili_subtitle,
            "fetch_subtitle_content",
            lambda url: [{"from": 0, "content": "你好"}],
        ),
    ):
        data = bili_subtitle.fetch_subtitle("BV1xx411c7mD", 1)

    assert data.title == "视频标题"  # not the part "00:15:30"
    assert data.up == "UP主"


# --- Recognition card enrichment + shareable GET endpoint ---

def test_list_bilibili_pages_enriches_video_meta(monkeypatch):
    """The pages response carries cover/UP/title/duration for the recognition card."""
    from backend.app import storage

    monkeypatch.setattr(
        storage,
        "_fetch_bilibili_pagelist",
        lambda bv: [storage.BilibiliPageInfo(page=1, title="分P标题", duration=120.0, cid=111)],
    )
    monkeypatch.setattr(
        storage,
        "get_video_info",
        lambda bv: {
            "cid": 111,
            "aid": 2,
            "title": "视频标题",
            "owner": {"name": "UP主"},
            "duration": 120,
            "pic": "https://i0.hdslb.com/cover.jpg",
            "pages": [],
        },
    )

    resp = storage.list_bilibili_pages("BV1xx411c7mD")
    assert resp.bv == "BV1xx411c7mD"
    assert [p.page for p in resp.pages] == [1]
    assert resp.title == "视频标题"
    assert resp.up == "UP主"
    assert resp.cover_url == "https://i0.hdslb.com/cover.jpg"
    assert resp.duration == "02:00"


def test_list_bilibili_pages_meta_none_when_info_fetch_fails(monkeypatch):
    """A failed metadata fetch must not break page recognition."""
    from backend.app import storage

    monkeypatch.setattr(
        storage,
        "_fetch_bilibili_pagelist",
        lambda bv: [storage.BilibiliPageInfo(page=1, title="分P标题", duration=60.0, cid=222)],
    )
    monkeypatch.setattr(storage, "get_video_info", lambda bv: None)

    resp = storage.list_bilibili_pages("BV1xx411c7mD")
    assert resp.title is None and resp.up is None
    assert resp.cover_url is None and resp.duration is None
    assert len(resp.pages) == 1  # recognition still worked


def test_get_stored_summary_handler_returns_payload_and_404s(tmp_path, monkeypatch):
    """GET /api/summary/{bvid} surfaces a stored summary (cached + subtitle_url)
    and 404s when nothing is stored. Handler called directly to avoid app
    lifespan side effects (the source-video cleanup task)."""
    from fastapi import HTTPException

    from backend.app import main, summary_store

    monkeypatch.setattr(summary_store.settings, "summaries_dir", tmp_path)
    summary_store.init_db()
    summary_store.save_summary(_sample_payload())

    resp = main.get_stored_summary("BV1xx411c7mD", 1)
    assert resp.bv == "BV1xx411c7mD"
    assert resp.overall_summary == "整体总结内容。"
    assert resp.cached is True
    assert resp.subtitle_url == "/api/summary/subtitle/BV1xx411c7mD/1"

    with pytest.raises(HTTPException) as exc_info:
        main.get_stored_summary("BV1zzzzzzzzzz", 1)
    assert exc_info.value.status_code == 404

