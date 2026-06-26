"""Bilibili CC subtitle fetching (WBI-signed player API).

Ported from ``~/Code/py/dfb/services/bili_slice_service.py`` (subtitle layer
only — search/DB/batch were dropped). The mixin-key table is the canonical
first 32 positions, which is all the WBI mixin key ever uses, so it is correct.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from http.cookiejar import LoadError, MozillaCookieJar
from urllib.parse import urlencode

import requests

from .config import settings
from .ffmpeg_tools import VideoProcessingError

logger = logging.getLogger(__name__)

# --- WBI signing ---

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
]

_wbi_keys_cache: dict[str, object] = {"mixin_key": None, "fetched_at": 0.0}
_last_request_time = 0.0
_session: requests.Session | None = None

_BILI_COOKIE_DOMAIN = "bilibili.com"
_LEGACY_BILI_COOKIE_KEYS = {"SESSDATA", "buvid3"}
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _rate_limit() -> None:
    global _last_request_time
    elapsed = time.time() - _last_request_time
    gap = settings.bili_rate_limit_seconds
    if elapsed < gap:
        time.sleep(gap - elapsed)
    _last_request_time = time.time()


def _get_mixin_key(raw_key: str) -> str:
    return "".join(raw_key[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _fetch_wbi_keys() -> str:
    now = time.time()
    cached = _wbi_keys_cache["mixin_key"]
    if cached and (now - _wbi_keys_cache["fetched_at"]) < 43200:
        return cached  # type: ignore[return-value]

    try:
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers={"User-Agent": _USER_AGENT},
            cookies=_load_cookies(),
            timeout=10,
        )
        data = resp.json()
    except Exception:
        logger.warning("Failed to fetch WBI nav keys", exc_info=True)
        return cached or ""

    if data.get("code") != 0:
        logger.warning("Failed to fetch WBI keys: code=%s", data.get("code"))
        return cached or ""

    try:
        img_url = data["data"]["wbi_img"]["img_url"]
        sub_url = data["data"]["wbi_img"]["sub_url"]
        img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
        sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
        mixin_key = _get_mixin_key(img_key + sub_key)
    except (KeyError, IndexError, TypeError):
        return cached or ""

    _wbi_keys_cache["mixin_key"] = mixin_key
    _wbi_keys_cache["fetched_at"] = now
    return mixin_key


def _sign_params(params: dict) -> dict:
    mixin_key = _fetch_wbi_keys()
    if not mixin_key:
        return params

    params = dict(params)
    params["wts"] = int(time.time())
    params = {k: v for k, v in sorted(params.items())}
    query = urlencode(params, doseq=True)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params


# --- Cookie loading (adapted to this project's settings) ---

def _is_bili_cookie_domain(domain: str) -> bool:
    domain = (domain or "").lstrip(".").lower()
    return domain == _BILI_COOKIE_DOMAIN or domain.endswith(f".{_BILI_COOKIE_DOMAIN}")


def _cookie_dict_from_jar(jar: MozillaCookieJar) -> dict:
    cookies: dict[str, str] = {}
    for cookie in jar:
        if cookie.name and cookie.value and _is_bili_cookie_domain(cookie.domain):
            cookies[cookie.name] = cookie.value
    return cookies


def _parse_netscape_cookie_text(text: str) -> dict:
    cookies: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue

        parts = line.split("\t", 6)
        if len(parts) != 7:
            parts = line.split(None, 6)
        if len(parts) != 7:
            continue

        domain, _include_subdomains, _path, _secure, _expires, name, value = parts
        if name and value and _is_bili_cookie_domain(domain):
            cookies[name] = value
    return cookies


def _parse_json_cookie_export(parsed: object) -> dict:
    if isinstance(parsed, list):
        cookies: dict[str, str] = {}
        for item in parsed:
            cookies.update(_parse_json_cookie_export(item))
        return cookies

    if not isinstance(parsed, dict):
        return {}

    if "name" in parsed and "value" in parsed:
        domain = str(parsed.get("domain", ""))
        if domain and not _is_bili_cookie_domain(domain):
            return {}
        name = str(parsed.get("name", ""))
        value = str(parsed.get("value", ""))
        return {name: value} if name and value else {}

    for key in ("cookies", "cookie"):
        nested = parsed.get(key)
        cookies = _parse_json_cookie_export(nested)
        if cookies:
            return cookies

    return {
        str(key): str(value)
        for key, value in parsed.items()
        if key in _LEGACY_BILI_COOKIE_KEYS and value
    }


def _parse_cookie_header(header: str) -> dict:
    cookies: dict[str, str] = {}
    for pair in (header or "").split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name and value:
            cookies[name] = value
    return cookies


def _load_cookies() -> dict:
    """Merge Bilibili cookies from this project's three configured sources."""
    cookies: dict[str, str] = {}

    try:
        cookies_file = settings.prepare_bilibili_cookies_file()
    except FileNotFoundError:
        cookies_file = None

    if cookies_file:
        try:
            text = cookies_file.read_text(encoding="utf-8").lstrip()
        except OSError:
            text = ""
        if text:
            if text[0] in ("{", "["):
                try:
                    cookies.update(_parse_json_cookie_export(json.loads(text)))
                except json.JSONDecodeError:
                    pass
            if not cookies:
                jar = MozillaCookieJar()
                try:
                    jar.load(str(cookies_file), ignore_discard=True, ignore_expires=True)
                    jar_cookies = _cookie_dict_from_jar(jar)
                    if jar_cookies:
                        cookies.update(jar_cookies)
                except (LoadError, OSError):
                    pass
            if not cookies:
                cookies.update(_parse_netscape_cookie_text(text))

    if settings.bilibili_cookie_header:
        cookies.update(_parse_cookie_header(settings.bilibili_cookie_header))

    return cookies


def _get_http_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": _USER_AGENT,
            "Referer": "https://www.bilibili.com",
        })
    _session.cookies.update(_load_cookies())
    return _session


# --- Bilibili API calls ---

def get_video_info(bvid: str) -> dict | None:
    _rate_limit()
    try:
        resp = _get_http_session().get(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": bvid},
            timeout=15,
        )
        data = resp.json()
    except Exception:
        logger.exception("Failed to get video info for %s", bvid)
        return None

    if data.get("code") != 0:
        logger.warning("Video info API error for %s: code=%s", bvid, data.get("code"))
        return None

    v = data["data"]
    return {
        "cid": v.get("cid"),
        "aid": v.get("aid"),
        "title": v.get("title", ""),
        "owner": v.get("owner", {}),
        "duration": v.get("duration"),
        "pic": v.get("pic", ""),
        "pages": v.get("pages", []),
    }


def get_subtitle_urls(bvid: str, cid: int) -> list[dict] | None:
    _rate_limit()
    params = _sign_params({"bvid": bvid, "cid": cid})
    try:
        resp = _get_http_session().get(
            "https://api.bilibili.com/x/player/wbi/v2",
            params=params,
            timeout=15,
        )
        data = resp.json()
    except Exception:
        logger.exception("Failed to get subtitle URLs for %s", bvid)
        return None

    if data.get("code") == -352:
        _wbi_keys_cache["mixin_key"] = None
        logger.warning("WBI signature rejected (-352) for %s", bvid)
        return None
    if data.get("code") != 0:
        logger.warning("Player API error for %s: code=%s", bvid, data.get("code"))
        return None

    return data.get("data", {}).get("subtitle", {}).get("subtitles") or []


def fetch_subtitle_content(subtitle_url: str) -> list[dict]:
    _rate_limit()
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url
    try:
        resp = requests.get(subtitle_url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception("Failed to fetch subtitle content from %s", subtitle_url[:80])
        return []
    return data.get("body", []) or []


def fetch_image_bytes(image_url: str) -> bytes:
    """Download raw image bytes (e.g. a Bilibili cover) via the project session.

    Routed through ``_get_http_session`` so the request carries the same UA,
    ``Referer: bilibili.com`` and cookies used for the other Bilibili calls —
    which keeps hotlink protection happy. Used by the same-origin cover proxy so
    the frontend can draw the cover into an html2canvas without cross-origin
    canvas tainting.
    """
    if not image_url:
        raise VideoProcessingError("缺少封面图片地址")
    url = image_url
    if url.startswith("//"):
        url = "https:" + url
    try:
        resp = _get_http_session().get(url, timeout=15)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.warning("Failed to fetch image bytes from %s: %s", url[:80], exc)
        raise VideoProcessingError(f"封面图片下载失败：{url[:80]}") from exc


# --- Time helpers ---

def format_timestamp(seconds: int | float | str | None) -> str:
    try:
        total_seconds = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        total_seconds = 0
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_timestamp_to_seconds(value: int | float | str | None) -> int | None:
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text_value = str(value or "").strip().replace("：", ":")
    if not text_value:
        return None
    if text_value.isdigit():
        return max(0, int(text_value))
    parts = text_value.split(":")
    try:
        if len(parts) == 2:
            minutes, secs = [int(part) for part in parts]
            if secs >= 60:
                return None
            return max(0, minutes * 60 + secs)
        if len(parts) == 3:
            hours, minutes, secs = [int(part) for part in parts]
            if minutes >= 60 or secs >= 60:
                return None
            return max(0, hours * 3600 + minutes * 60 + secs)
    except ValueError:
        return None
    return None


def format_subtitle_timeline(body: list[dict]) -> str:
    """Render subtitle body as ``[MM:SS] content`` lines (for the LLM)."""
    lines: list[str] = []
    for entry in body or []:
        content = re.sub(r"\s+", " ", str(entry.get("content") or "")).strip()
        if not content:
            continue
        start = entry.get("from")
        if start is None:
            lines.append(content)
            continue
        lines.append(f"[{format_timestamp(start)}] {content}")
    return "\n".join(lines)


# --- High-level subtitle fetch ---

def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _select_video_page(info: dict | None, cid: int | None = None, page_no: int | None = None) -> dict:
    pages = (info or {}).get("pages") or []
    if cid:
        for page in pages:
            if _safe_int(page.get("cid")) == _safe_int(cid):
                return page
    if page_no:
        for page in pages:
            if _safe_int(page.get("page"), 1) == _safe_int(page_no, 1):
                return page
    return pages[0] if pages else {}


@dataclass(frozen=True)
class SubtitleData:
    bv: str
    page: int
    cid: int
    title: str
    up: str
    duration_seconds: int | None
    timeline: str
    body: list[dict] = field(default_factory=list)
    subtitle_url: str = ""
    lan: str = ""
    cover_url: str = ""


class NoSubtitleError(VideoProcessingError):
    """Raised when the video has no usable CC subtitle."""


def fetch_subtitle(bv: str, page: int) -> SubtitleData:
    info = get_video_info(bv)
    if not info:
        raise VideoProcessingError(f"无法获取视频信息：{bv}（可能视频不存在、被删除，或网络/风控限制）")

    page_dict = _select_video_page(info, page_no=page)
    cid = _safe_int(page_dict.get("cid")) or _safe_int(info.get("cid"))
    if not cid:
        raise VideoProcessingError(f"无法解析分 P 的 cid：{bv} P{page}")

    title = (str(page_dict.get("part") or "").strip()) or str(info.get("title") or "")
    up = str((info.get("owner") or {}).get("name") or "")
    duration_seconds = page_dict.get("duration") or info.get("duration")
    duration_seconds = _safe_int(duration_seconds) or None

    subtitle_urls = get_subtitle_urls(bv, cid)
    if subtitle_urls is None:
        raise VideoProcessingError("调用字幕接口失败（网络或风控），请稍后重试")
    if not subtitle_urls:
        raise NoSubtitleError(
            "该视频没有 CC 字幕，无法生成总结。请换一个带 CC 字幕（人工或 AI 字幕）的视频。"
        )

    preferred = next(
        (s for s in subtitle_urls if str(s.get("lan", "")).startswith("zh")),
        subtitle_urls[0],
    )
    subtitle_url = preferred.get("subtitle_url", "")
    if not subtitle_url:
        raise NoSubtitleError("该视频没有可下载的 CC 字幕，无法生成总结。")

    body = fetch_subtitle_content(subtitle_url)
    if not body:
        raise NoSubtitleError("字幕内容为空，无法生成总结。")

    timeline = format_subtitle_timeline(body)
    if not timeline.strip():
        raise NoSubtitleError("字幕内容为空，无法生成总结。")

    return SubtitleData(
        bv=bv,
        page=page,
        cid=cid,
        title=title,
        up=up,
        duration_seconds=duration_seconds,
        timeline=timeline,
        body=body,
        subtitle_url=subtitle_url,
        lan=str(preferred.get("lan", "")),
        cover_url=str(info.get("pic") or ""),
    )
