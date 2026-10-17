"""Trends: public YouTube "most popular" chart + model-assisted analysis (ideas only, never view predictions).

fetch_trends  -> YouTube Data API v3 videos.list chart=mostPopular (API key, quota cost 1 unit per call).
analyze_trends -> compact redacted summary (titles/tags/durations/view counts only) to an OpenAI-compatible
                  chat callable (the app passes OpenRouter :free models as a fallback chain, see
                  apps/war-terminal/warterminal/ai.py make_chat); strict JSON reply.

Verified facts (official docs, read 2026-10-17):
* videos.list: chart=mostPopular + regionCode (ISO 3166-1 alpha-2), maxResults 1..50, quota 1 unit/call.
  https://developers.google.com/youtube/v3/docs/videos/list
* Since 2025-07-21 the mostPopular chart features Trending Music, Movies and Gaming (the Trending page
  was deprecated) - it is NOT a Shorts feed. https://developers.google.com/youtube/v3/revision_history
* contentDetails.duration is ISO 8601 (PT#M#S, may include days P#DT#H#M#S).
  https://developers.google.com/youtube/v3/docs/videos
* Shorts rule: videos uploaded on/after 2024-10-15 with a SQUARE or VERTICAL aspect ratio up to 3 minutes
  are Shorts; before that the limit was 60 s. https://support.google.com/youtube/answer/15424877
  The public API does not expose aspect ratio for other people's videos, so is_short here is a
  duration-based CANDIDATE flag (<=180 s on/after 2024-10-15, <=60 s before), not a certainty.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

API_URL = "https://www.googleapis.com/youtube/v3/videos"
SHORTS_3MIN_SINCE = "2024-10-15"
SHORT_RULE = ("is_short = duration <= 180 s for videos published on/after 2024-10-15 (<= 60 s before); "
              "aspect ratio (square/vertical) is not available via the public API, so this is a candidate flag. "
              "Source: https://support.google.com/youtube/answer/15424877")
CHART_NOTE = ("Since 2025-07-21 chart=mostPopular reflects Trending Music, Movies and Gaming, not a Shorts feed "
              "(https://developers.google.com/youtube/v3/revision_history).")

NOT_CONNECTED_STEPS = [
    "1. Open https://console.cloud.google.com/projectcreate and create a project (free).",
    "2. Enable YouTube Data API v3: https://console.cloud.google.com/apis/library/youtube.googleapis.com",
    "3. Credentials -> Create credentials -> API key: https://console.cloud.google.com/apis/credentials",
    "4. Restrict the key: API restrictions -> YouTube Data API v3 only.",
    "5. Put it into %LOCALAPPDATA%\\Bossman\\keys\\provider-keys.env as YOUTUBE_API_KEY=... (never into git).",
    "Free default quota: 10,000 units/day; this call costs 1 unit per page of up to 50 videos.",
]

_DURATION = re.compile(r"^P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")
_REDACT = [(re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
           (re.compile(r"\+?\d[\d\s().-]{8,}\d"), "[phone]"),
           (re.compile(r"https?://\S+"), "[url]")]
_PREDICTION = re.compile(r"(predict|forecast|guarantee|will (get|reach|hit)|прогноз|гарант|наберёт|наберет|получит)"
                         r".{0,40}(view|просмотр)|(view|просмотр).{0,40}(predict|forecast|guarantee|прогноз|гарант)",
                         re.I | re.S)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_duration(value: str | None) -> int | None:
    """ISO 8601 duration ('PT1M5S', 'P1DT2H', 'PT45S') -> whole seconds; None when absent or unparsable.
    'P0D' (live/upcoming) -> 0."""
    if not value or not isinstance(value, str):
        return None
    m = _DURATION.match(value.strip().upper())
    if not m or value.strip().upper() in ("P", "PT"):
        return None
    w, d, h, mi, s = m.groups()
    total = (int(w or 0) * 7 + int(d or 0)) * 86400 + int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)
    return int(round(total))


def is_short(duration_s: int | None, published_at: str | None = None) -> bool:
    if duration_s is None or duration_s <= 0:
        return False
    limit = 180 if (published_at or "9999")[:10] >= SHORTS_3MIN_SINCE else 60
    return duration_s <= limit


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_item(raw: dict) -> dict:
    sn, st, cd = raw.get("snippet") or {}, raw.get("statistics") or {}, raw.get("contentDetails") or {}
    dur = parse_duration(cd.get("duration"))
    pub = sn.get("publishedAt")
    return {"video_id": raw.get("id"), "title": sn.get("title") or "", "channel": sn.get("channelTitle") or "",
            "views": _int(st.get("viewCount")), "likes": _int(st.get("likeCount")),   # likes may be hidden -> None
            "published_at": pub, "duration_s": dur, "tags": list(sn.get("tags") or []),
            "category_id": sn.get("categoryId"), "is_short": is_short(dur, pub)}


def _scrub(text: str, api_key: str | None) -> str:
    text = str(text)
    if api_key:
        text = text.replace(api_key, "[key]")
    return re.sub(r"key=[^&\s]+", "key=[key]", text)[:300]


def _call(get: Callable, url: str, params: dict) -> dict:
    resp = get(url, params)
    if hasattr(resp, "json") and callable(resp.json):          # requests/httpx response
        code = getattr(resp, "status_code", 200)
        data = resp.json()
        if code >= 400 and isinstance(data, dict) and "error" not in data:
            data = {"error": {"code": code, "message": f"HTTP {code}"}}
        return data
    return resp


def fetch_trends(get, *, api_key: str | None, region: str = "CZ", max_results: int = 50) -> dict:
    """{status: OK|NOT_CONNECTED|ERROR, items, source, fetched_at, ...}. get(url, params) is injected."""
    base = {"items": [], "source": f"YouTube Data API v3 videos.list chart=mostPopular regionCode={region}",
            "fetched_at": _now(), "short_rule": SHORT_RULE, "chart_note": CHART_NOTE}
    if not api_key:
        return {"status": "NOT_CONNECTED", **base, "reason": "нет YOUTUBE_API_KEY", "steps": NOT_CONNECTED_STEPS}
    if not re.fullmatch(r"[A-Za-z]{2}", region or ""):
        return {"status": "ERROR", **base, "reason": f"regionCode must be ISO 3166-1 alpha-2, got {region!r}"}
    want = max(1, min(int(max_results), 200))
    items: list[dict] = []
    token: str | None = None
    pages = 0
    try:
        while len(items) < want:
            params = {"part": "snippet,statistics,contentDetails", "chart": "mostPopular",
                      "regionCode": region.upper(), "maxResults": min(50, want - len(items)), "key": api_key}
            if token:
                params["pageToken"] = token
            data = _call(get, API_URL, params)
            pages += 1
            if not isinstance(data, dict):
                return {"status": "ERROR", **base, "reason": "unexpected response type"}
            if "error" in data:
                err = data["error"] if isinstance(data["error"], dict) else {"message": data["error"]}
                reasons = [e.get("reason") for e in err.get("errors", []) if isinstance(e, dict)]
                return {"status": "ERROR", **base, "items": items,
                        "reason": _scrub(f"{err.get('code', '')} {err.get('message', '')} {reasons or ''}".strip(), api_key)}
            items.extend(parse_item(r) for r in data.get("items") or [] if isinstance(r, dict))
            token = data.get("nextPageToken")
            if not token or not data.get("items"):
                break
    except Exception as exc:  # noqa: BLE001 - network failures become an honest ERROR, never a crash
        return {"status": "ERROR", **base, "items": items, "reason": _scrub(f"{type(exc).__name__}: {exc}", api_key)}
    return {"status": "OK", **base, "items": items[:want], "quota_units": pages}


# ------------------------------------------------------------------------------------------ analysis

def redact(text: str) -> str:
    for rx, sub in _REDACT:
        text = rx.sub(sub, text)
    return text


def build_summary(items: list[dict], limit: int = 50) -> list[dict]:
    """Only titles/tags/durations/view counts (plus the short flag) leave the PC; free text is redacted."""
    out = []
    for it in (items or [])[:limit]:
        out.append({"title": redact(str(it.get("title") or ""))[:140],
                    "tags": [redact(str(t))[:40] for t in (it.get("tags") or [])[:8]],
                    "duration_s": it.get("duration_s"), "views": it.get("views"),
                    "is_short": bool(it.get("is_short"))})
    return out


SYSTEM = ("You help a small creator plan YouTube Shorts. You get a list of currently popular public videos "
          "(title, tags, duration in seconds, view count). Find recurring topics, opening hooks and formats that a "
          "NEW channel could adapt without copying anyone. Never predict, estimate or promise view counts or "
          "growth. Reply with ONE JSON object only: {\"topics\":[\"...\"], \"hooks\":[\"...\"], "
          "\"formats\":[\"...\"], \"notes\":\"<=400 chars\"}. Up to 8 strings per list, each <=160 chars.")


def build_messages(items: list[dict]) -> list[dict]:
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(build_summary(items), ensure_ascii=False)}]


def _clean_list(v: Any) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        s = (json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x)).strip()
        if s and not _PREDICTION.search(s):
            out.append(s[:200])
    return out[:8]


def _closers(s: str) -> str | None:
    """Brackets needed to close s; None when s ends inside a string."""
    stack, in_str, esc = [], False, False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append("]" if ch == "[" else "}")
        elif ch in "]}" and stack:
            stack.pop()
    return None if in_str else "".join(reversed(stack))


def _salvage(s: str) -> dict | None:
    """A free model sometimes stops mid-JSON: keep every complete string element, close the brackets."""
    ends = [i for i, ch in enumerate(s) if ch == '"'][::-1][:300]
    for i in ends:
        cand = s[:i + 1]
        close = _closers(cand)
        if close is None:
            continue
        try:
            data = json.loads(cand + close)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def parse_analysis(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    start = t.find("{")
    if start < 0:
        raise ValueError("model reply has no JSON object")
    m = re.search(r"\{.*\}", t, re.S)
    truncated = False
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        data = None
    if data is None:
        data, truncated = _salvage(t[start:]), True
        if data is None:
            raise ValueError("model reply has no parsable JSON object")
    if not isinstance(data, dict):
        raise ValueError("model reply JSON is not an object")
    res = {k: _clean_list(data.get(k)) for k in ("topics", "hooks", "formats")}
    if not any(res.values()):
        raise ValueError("model reply has no topics/hooks/formats")
    notes = str(data.get("notes") or "")
    if _PREDICTION.search(notes):
        notes = ""
    res["notes"] = (notes[:400] + " " if notes else "") + "Идеи, не прогноз просмотров."
    if truncated:
        res["notes"] += " Ответ модели был обрезан: разобрана только полная часть."
    return res


def analyze_trends(items, chat) -> dict:
    """{status: OK|NOT_CONNECTED|ERROR, topics, hooks, formats, notes}. chat(messages) -> str."""
    empty = {"topics": [], "hooks": [], "formats": []}
    if chat is None:
        return {"status": "NOT_CONNECTED", **empty, "notes": "модель не настроена (OPENROUTER_API_KEY / локальная модель)"}
    if not items:
        return {"status": "ERROR", **empty, "notes": "нет трендов для анализа"}
    try:
        reply = chat(build_messages(items))
        if isinstance(reply, dict):                     # guard/budget wrappers may return a status dict
            if reply.get("status") not in (None, "OK"):
                return {"status": "ERROR", **empty, "notes": f"{reply.get('status')}: {str(reply.get('reason', ''))[:200]}"}
            reply = reply.get("text") or reply.get("content") or ""
        return {"status": "OK", **parse_analysis(str(reply))}
    except Exception as exc:  # noqa: BLE001 - budget/guard/network/garbage -> ERROR, never a crash
        return {"status": "ERROR", **empty, "notes": f"{type(exc).__name__}: {str(exc)[:200]}"}
