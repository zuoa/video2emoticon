"""Video summarization over Bilibili subtitles (LLM, map-reduce for long videos).

Unlike the dfb slice summary (which truncates to 12k chars and produces a
2-3 sentence blurb), this splits the full subtitle by token budget on subtitle-
line boundaries, summarizes each chunk, then synthesizes an overall summary —
so a 60-minute video is summarized in full, not truncated.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from .bili_subtitle import (
    NoSubtitleError,
    VideoProcessingError,
    fetch_subtitle,
    format_timestamp,
    parse_timestamp_to_seconds,
)
from .config import settings
from . import summary_store

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - dependency declared in requirements
    OpenAI = None  # type: ignore[assignment]


class SummaryConfigError(VideoProcessingError):
    """Raised when the LLM is not configured (e.g. missing API key)."""


_client = None


def _get_client():
    if OpenAI is None:
        raise SummaryConfigError("openai 包未安装，无法调用大模型。")
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.openai_timeout,
        )
    return _client


# --- Prompts ---

_SINGLE_PROMPT = (
    "你是一名专业的视频内容编辑，擅长像「AI 课代表」那样提炼 B 站视频精华。"
    "下面是一支视频当前分P的完整带时间戳字幕，请用中文产出结构化总结。\n\n"
    "要求：\n"
    "【视频总结 overall_summary】\n"
    "- 3-6 句话（可分段），讲清：视频主题是什么、按什么顺序讲了哪些主要内容、核心结论或价值、适合什么人看。\n"
    "- 直接进入内容，不要用“这个视频”“本期视频主要”“以下是对视频的总结”等元信息式开头。\n"
    "- 不要出现“字幕”“视频”“切片”等字眼。\n\n"
    "【关键内容点 key_points】\n"
    "- 挑选 {kp_count} 个最值得标注的内容点，按时间先后排序，尽量分散在不同时间段。\n"
    "- time 必须是字幕里实际出现过的 MM:SS 或 HH:MM:SS，不要编造时间点。\n"
    "- title 是 8-16 字的小标题；detail 是 1-2 句话说明这个时间点讲了什么。\n\n"
    "【金句/知识点 quotes】\n"
    "- 提炼 3-8 条值得记住的金句或核心知识点，每条一句话；可为空数组。\n\n"
    "严格输出 JSON，不要 Markdown、不要代码块：\n"
    '{{"overall_summary":"...","key_points":[{{"time":"02:15","title":"...","detail":"..."}}],"quotes":["..."]}}\n\n'
    "当前分P：P{page_no}{page_title}\n"
    "字幕内容：\n{subtitle}"
)

_CHUNK_PROMPT = (
    "你是一名专业的视频内容编辑。下面是一支视频某个时间段（第 {index}/{total} 段）的带时间戳字幕，请用中文提炼这一段。\n\n"
    "要求：\n"
    "- section_summary：2-4 句话概括这一段内容，直接进入内容，不要“这段视频”式开头。\n"
    "- key_points：挑选这段里 {kp_count} 个值得标注的内容点，time 必须是字幕里实际出现过的 MM:SS 或 HH:MM:SS，"
    "title 8-16 字，detail 1-2 句，按时间排序。\n"
    "- quotes：这一段里值得记住的金句/知识点 0-5 条，可为空数组。\n\n"
    "严格输出 JSON，不要 Markdown、不要代码块：\n"
    '{{"section_summary":"...","key_points":[{{"time":"...","title":"...","detail":"..."}}],"quotes":["..."]}}\n\n'
    "字幕内容：\n{subtitle}"
)

_SYNTHESIS_PROMPT = (
    "你是一名专业的视频内容编辑。下面是一支视频按时间段切分后、每段的摘要与关键内容点（已带绝对时间戳）。"
    "请综合成整支视频的结构化总结。\n\n"
    "要求：\n"
    "【视频总结 overall_summary】3-6 句话，讲清主题、主要内容脉络、核心结论、适合人群。"
    "直接进入内容，不要“这个视频”式开头。\n"
    "【关键内容点 key_points】从所有段落里挑选最多 {kp_count} 个最重要、时间上最分散的内容点，按时间排序，去重合并。"
    "time 保持原 MM:SS，title 8-16 字，detail 1-2 句。\n"
    "【金句/知识点 quotes】合并去重后 3-8 条，可为空数组。\n\n"
    "严格输出 JSON，不要 Markdown、不要代码块：\n"
    '{{"overall_summary":"...","key_points":[{{"time":"...","title":"...","detail":"..."}}],"quotes":["..."]}}\n\n'
    "各段摘要与关键点：\n{chunks_brief}"
)

_BOILERPLATE_PATTERNS = (
    re.compile(r"^这个视频[，。,:：\s]*"),
    re.compile(r"^本期视频[主要]*[，。,:：\s]*"),
    re.compile(r"^该视频[主要]*[，。,:：\s]*"),
    re.compile(r"^以下[是对于]*[对这个]*视频的总结[，。,:：\s]*"),
    re.compile(r"^这是一个(直播)?(视频|切片)[，。,:：\s]*"),
    re.compile(r"^主要(记录了|讲了|介绍了)[，。,:：\s]*"),
)

_TS_RE = re.compile(r"\[(\d{1,3}:\d{2}(?::\d{2})?)\]")


# --- Parsing & normalization ---

def _clean_summary(summary: str) -> str:
    summary = (summary or "").strip()
    for pattern in _BOILERPLATE_PATTERNS:
        summary = pattern.sub("", summary).strip()
    return summary


def _extract_json_object(text_value: str) -> dict | None:
    text_value = (text_value or "").strip()
    if not text_value:
        return None
    if text_value.startswith("```"):
        text_value = re.sub(r"^```(?:json)?\s*", "", text_value, flags=re.IGNORECASE)
        text_value = re.sub(r"\s*```$", "", text_value)
    try:
        parsed = json.loads(text_value)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = text_value.find("{")
    end = text_value.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text_value[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _parse_summary_response(content: str) -> tuple[str, list, list]:
    """Return (summary_text, raw_key_points, raw_quotes)."""
    parsed = _extract_json_object(content)
    if parsed:
        summary = (
            parsed.get("overall_summary")
            or parsed.get("summary")
            or parsed.get("section_summary")
            or parsed.get("摘要")
            or ""
        )
        points = (
            parsed.get("key_points")
            or parsed.get("highlights")
            or parsed.get("关键内容点")
            or []
        )
        quotes = parsed.get("quotes") or parsed.get("金句") or []
        return _clean_summary(str(summary)), points, quotes

    return _clean_summary(content), [], []


def _bili_video_url(bv: str, page: int, seconds: int | None) -> str:
    url = f"https://www.bilibili.com/video/{bv}"
    params: list[str] = []
    if page and page > 1:
        params.append(f"p={page}")
    if seconds is not None:
        params.append(f"t={max(0, int(seconds))}")
    return f"{url}?{'&'.join(params)}" if params else url


def _normalize_key_points(raw_list, bv: str, page: int, cap: int = 20) -> list[dict]:
    points: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for raw in raw_list or []:
        if not isinstance(raw, dict):
            continue
        seconds = parse_timestamp_to_seconds(
            raw.get("time") or raw.get("timestamp") or raw.get("start") or raw.get("seconds")
        )
        if seconds is None:
            continue
        title = re.sub(r"\s+", " ", str(raw.get("title") or raw.get("heading") or "")).strip()
        detail = re.sub(
            r"\s+", " ",
            str(raw.get("detail") or raw.get("description") or raw.get("content") or ""),
        ).strip()
        if not title and not detail:
            continue
        key = (seconds, title)
        if key in seen:
            continue
        seen.add(key)
        points.append({
            "time": format_timestamp(seconds),
            "seconds": seconds,
            "title": title[:40],
            "detail": detail[:160],
            "url": _bili_video_url(bv, page, seconds),
        })
    points.sort(key=lambda p: p["seconds"])
    return points[:cap]


def _normalize_quotes(raw_quotes, cap: int = 8) -> list[str]:
    quotes: list[str] = []
    seen: set[str] = set()
    for raw in raw_quotes or []:
        text_value = re.sub(r"\s+", " ", str(raw or "")).strip(" -—：「」“”\"'")
        if not text_value or text_value in seen:
            continue
        seen.add(text_value)
        quotes.append(text_value[:120])
        if len(quotes) >= cap:
            break
    return quotes


# --- Chunking & counting ---

def _compute_highlight_count(seconds: int | None, cap: int = 16) -> int:
    if not seconds or seconds <= 0:
        return 4
    minutes = seconds / 60
    return max(3, min(cap, round(minutes / 3) + 2))


def _split_timeline(timeline: str, max_chars: int) -> list[str]:
    lines = timeline.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > max_chars:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def _chunk_span_seconds(chunk: str) -> int | None:
    times = _TS_RE.findall(chunk)
    if not times:
        return None
    first = parse_timestamp_to_seconds(times[0])
    last = parse_timestamp_to_seconds(times[-1])
    if first is None or last is None:
        return None
    return max(1, last - first)


# --- LLM calls ---

def _chat(prompt: str, max_tokens: int = 1200) -> str:
    client = _get_client()
    try:
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001 - surface as a user-facing error
        logger.exception("LLM call failed")
        raise VideoProcessingError(f"大模型调用失败：{exc}") from exc


def _summarize_single(timeline: str, duration_seconds: int | None, page: int, page_title: str) -> tuple[str, list, list]:
    kp_count = _compute_highlight_count(duration_seconds)
    prompt = _SINGLE_PROMPT.format(
        kp_count=kp_count,
        page_no=page,
        page_title=f" {page_title}" if page_title else "",
        subtitle=timeline,
    )
    content = _chat(prompt, max_tokens=1600)
    return _parse_summary_response(content)


def _summarize_chunk(index: int, total: int, chunk: str) -> dict:
    span = _chunk_span_seconds(chunk)
    kp_count = _compute_highlight_count(span or 0)
    prompt = _CHUNK_PROMPT.format(index=index + 1, total=total, kp_count=kp_count, subtitle=chunk)
    content = _chat(prompt, max_tokens=900)
    summary, points, quotes = _parse_summary_response(content)
    return {"index": index, "summary": summary, "points": points, "quotes": quotes}


def _summarize_chunks(chunks: list[str]) -> list[dict]:
    total = len(chunks)
    with ThreadPoolExecutor(max_workers=min(4, total)) as executor:
        results = list(executor.map(
            lambda idx_chunk: _summarize_chunk(idx_chunk[0], total, idx_chunk[1]),
            enumerate(chunks),
        ))
    results.sort(key=lambda r: r["index"])
    return results


def _build_chunks_brief(chunk_results: list[dict]) -> str:
    parts: list[str] = []
    for r in chunk_results:
        block = [f"【第 {r['index'] + 1} 段】", f"摘要：{r['summary']}"]
        if r["points"]:
            block.append("关键点：")
            for p in r["points"]:
                block.append(
                    f"- [{p.get('time', '')}] {p.get('title', '')} — {p.get('detail', '')}"
                )
        if r["quotes"]:
            block.append("金句：" + " / ".join(str(q) for q in r["quotes"]))
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def _synthesize(chunk_results: list[dict], duration_seconds: int | None) -> tuple[str, list, list]:
    kp_count = _compute_highlight_count(duration_seconds)
    prompt = _SYNTHESIS_PROMPT.format(
        kp_count=kp_count,
        chunks_brief=_build_chunks_brief(chunk_results),
    )
    content = _chat(prompt, max_tokens=1600)
    return _parse_summary_response(content)


# --- Markdown rendering ---

def _render_markdown(
    bv: str,
    page: int,
    title: str,
    up: str,
    duration_str: str | None,
    overall: str,
    points: list[dict],
    quotes: list[str],
) -> str:
    lines = [f"# {title}", ""]
    meta: list[str] = []
    if up:
        meta.append(f"UP 主：{up}")
    if duration_str:
        meta.append(f"时长：{duration_str}")
    meta.append(f"BV：{bv}（P{page}）")
    lines.append("  ".join(meta))
    lines += ["", "## 视频总结", "", overall.strip(), ""]
    if points:
        lines += ["## 关键内容点", ""]
        for p in points:
            lines.append(f"- **[{p['time']}] {p['title']}** — {p['detail']}")
            lines.append(f"  - 跳转：{p['url']}")
        lines.append("")
    if quotes:
        lines += ["## 金句 / 知识点", ""]
        for q in quotes:
            lines.append(f"- {q}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


# --- Cache (SQLite via summary_store) ---

def subtitle_download_url(bv: str, page: int) -> str:
    return f"/api/summary/subtitle/{bv}/{page}"


# --- Public entry point ---

def generate_summary(bv: str, page: int) -> dict:
    if not settings.openai_api_key:
        raise SummaryConfigError("未配置 OPENAI_API_KEY，无法调用大模型生成总结。")

    cached = summary_store.get_summary(bv, page)
    if cached:
        cached["cached"] = True
        cached["subtitle_url"] = subtitle_download_url(bv, page)
        return cached

    data = fetch_subtitle(bv, page)  # raises NoSubtitleError / VideoProcessingError
    duration = data.duration_seconds
    chunks = _split_timeline(data.timeline, settings.summary_max_input_chars)

    if len(chunks) > 1:
        chunk_results = _summarize_chunks(chunks)
        overall, points, quotes = _synthesize(chunk_results, duration)
    else:
        overall, points, quotes = _summarize_single(
            data.timeline, duration, data.page, data.title
        )

    overall = _clean_summary(overall) or data.title
    points = _normalize_key_points(points, bv, data.page)
    quotes = _normalize_quotes(quotes)
    duration_str = format_timestamp(duration) if duration else None
    markdown = _render_markdown(
        bv, data.page, data.title, data.up, duration_str, overall, points, quotes
    )

    payload = {
        "bv": bv,
        "page": data.page,
        "cid": data.cid,
        "title": data.title,
        "up": data.up,
        "duration": duration_str,
        "overall_summary": overall,
        "key_points": points,
        "quotes": quotes,
        "markdown": markdown,
        "subtitle_timeline": data.timeline,
        "subtitle_format": "txt",
        "subtitle_url": subtitle_download_url(bv, data.page),
        "cached": False,
    }
    summary_store.save_summary(payload)
    return payload
