"""YouTube channel connection and uploads (YouTube Data API v3, OAuth 2.0 installed-app flow).

Scopes: youtube.upload + youtube.readonly. The owner clicks through Google consent ONCE (connect); the
refresh token is stored in <config_dir>/youtube_token.json protected with Windows DPAPI (current Windows
user only, same approach as tools/keys_guard.py). Tokens are never printed, logged or returned.

Verified facts (official docs, read 2026-10-17):
* videos.insert: "Quota impact: 100 calls per day. A call to this method has a quota cost of 1 unit in the
  Video Uploads quota bucket." Max file 256GB, video/* or application/octet-stream.
  https://developers.google.com/youtube/v3/docs/videos/insert
  (Granular buckets since 2026-06-01; before 2025-12-04 an upload cost ~1600 units, then ~100.)
  https://developers.google.com/youtube/v3/revision_history
  https://developers.google.com/youtube/v3/determine_quota_cost
* Unverified projects: the 2020-07-28 rule says uploads from unverified API projects created after
  28 July 2020 are locked to private until the project passes a compliance audit
  (revision_history, entry July 28, 2020). The CURRENT videos.insert page instead says "Videos uploaded
  from unverified API projects are not restricted to private viewing mode." The two official pages
  disagree, so upload() always returns the privacyStatus YouTube actually stored - check it after the
  first upload. Audit form: https://support.google.com/youtube/contact/yt_api_form
* status.publishAt "can be set only if the privacy status of the video is private".
  status.containsSyntheticMedia discloses realistic Altered or Synthetic content (since 2024-10-30).
  status.selfDeclaredMadeForKids designates child-directed content. https://developers.google.com/youtube/v3/docs/videos
"""
from __future__ import annotations

import base64
import ctypes
import json
import mimetypes
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
TOKEN_NAME = "youtube_token.json"
PRIVACY = ("private", "unlisted", "public")
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
CHUNK = 8 * 1024 * 1024
SHORT_MAX_S = 180   # https://support.google.com/youtube/answer/15424877 (square/vertical, <=3 min, since 2024-10-15)

NOT_CONNECTED_REASON = ("канал не подключён: создайте OAuth client (Desktop app) в Google Cloud, скачайте JSON и "
                        "выполните connect(config_dir, client_secrets_path) - см. docs/YOUTUBE_SETUP_RU.md")


class YouTubeError(Exception):
    pass


# ------------------------------------------------------------------ DPAPI (copied approach: tools/keys_guard.py)

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise YouTubeError("DPAPI is available only on Windows; refusing to store the token in plain text")
    if not data:
        return b""
    buf = ctypes.create_string_buffer(data, len(data))
    in_blob = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = _Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if fn(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)) == 0:
        raise YouTubeError("DPAPI protect failed" if protect else "DPAPI unprotect failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _token_path(config_dir) -> Path:
    return Path(config_dir) / TOKEN_NAME


def save_token(config_dir, authorized_user_json: str) -> None:
    blob = _dpapi(authorized_user_json.encode("utf-8"), protect=True)
    doc = {"format": "dpapi-v1", "scopes": SCOPES, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "blob": base64.b64encode(blob).decode("ascii")}
    _atomic_write(_token_path(config_dir), json.dumps(doc, indent=1).encode("utf-8"))


def load_token(config_dir) -> dict | None:
    p = _token_path(config_dir)
    if not p.exists():
        return None
    doc = json.loads(p.read_text(encoding="utf-8"))
    if doc.get("format") != "dpapi-v1":
        raise YouTubeError("token file has an unknown format")
    return json.loads(_dpapi(base64.b64decode(doc["blob"]), protect=False).decode("utf-8"))


_SECRETISH = re.compile(r"(ya29\.[\w.-]+|1//[\w.-]+|GOCSPX-[\w-]+|\"?(refresh_token|access_token|client_secret|token)\"?\s*[:=]\s*\"?[^\s\",}]+)")


def _safe(exc: BaseException) -> str:
    """Error text without anything that looks like a credential."""
    return _SECRETISH.sub("[redacted]", f"{type(exc).__name__}: {exc}")[:300]


# ------------------------------------------------------------------ credentials + service (patchable in tests)

def _credentials(config_dir):
    info = load_token(config_dir)
    if info is None:
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        save_token(config_dir, creds.to_json())      # keep the rotated access token protected too
    return creds


def _build_service(config_dir):
    creds = _credentials(config_dir)
    if creds is None:
        return None
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _flow(client_secrets_path):
    from google_auth_oauthlib.flow import InstalledAppFlow
    return InstalledAppFlow.from_client_secrets_file(str(client_secrets_path), SCOPES)


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _channel(service) -> dict | None:
    resp = service.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()
    items = resp.get("items") or []
    if not items:
        return None
    ch = items[0]
    st = ch.get("statistics") or {}
    return {"id": ch.get("id"), "title": (ch.get("snippet") or {}).get("title"),
            "subscribers": None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount")),
            "views": _int(st.get("viewCount")), "videos": _int(st.get("videoCount")),
            "_uploads": ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")}


def _public(ch: dict | None) -> dict | None:
    return None if ch is None else {k: v for k, v in ch.items() if not k.startswith("_")}


# ------------------------------------------------------------------ public interface

def status(config_dir) -> dict:
    try:
        service = _build_service(config_dir)
        if service is None:
            return {"connected": False, "reason": NOT_CONNECTED_REASON}
        ch = _channel(service)
        if ch is None:
            return {"connected": False, "reason": "аккаунт авторизован, но у него нет YouTube-канала"}
        return {"connected": True, "channel": _public(ch), "reason": "OK"}
    except Exception as exc:  # noqa: BLE001
        return {"connected": False, "reason": _safe(exc)}


def connect(config_dir, client_secrets_path) -> dict:
    """Runs the local browser OAuth flow once (owner clicks 'Allow'); stores the token DPAPI-protected."""
    p = Path(client_secrets_path)
    if not p.is_file():
        return {"connected": False, "reason": f"нет файла OAuth client JSON: {p.name}"}
    try:
        flow = _flow(p)
        creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=600,
                                      access_type="offline", prompt="consent",
                                      authorization_prompt_message="Откройте браузер и разрешите доступ к каналу: {url}",
                                      success_message="Готово, окно можно закрыть.")
        if not getattr(creds, "refresh_token", None):
            return {"connected": False, "reason": "Google не выдал refresh token; отзовите доступ и повторите connect"}
        save_token(config_dir, creds.to_json())
    except Exception as exc:  # noqa: BLE001
        return {"connected": False, "reason": _safe(exc)}
    st = status(config_dir)
    return {**st, "token_file": str(_token_path(config_dir)), "protection": "Windows DPAPI (current user)"}


def _validate(title, description, tags, privacy, publish_at) -> str | None:
    if privacy not in PRIVACY:
        return f"privacy must be one of {PRIVACY}"
    if not title or not str(title).strip():
        return "title is required"
    if len(str(title)) > 100:
        return "title is longer than 100 characters"
    if "<" in f"{title}{description}" or ">" in f"{title}{description}":
        return "title/description must not contain < or >"
    if len(str(description or "").encode("utf-8")) > 5000:
        return "description is longer than 5000 bytes"
    if sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1) > 500:
        return "tags exceed 500 characters in total"
    if publish_at is not None:
        if privacy != "private":
            return "publish_at requires privacy='private' (status.publishAt can be set only for private videos)"
        try:
            dt = datetime.fromisoformat(str(publish_at).replace("Z", "+00:00"))
        except ValueError:
            return "publish_at must be ISO 8601, e.g. 2026-10-20T16:00:00Z"
        if dt.tzinfo is None:
            return "publish_at must include a timezone (e.g. Z)"
        if dt <= datetime.now(timezone.utc):
            return "publish_at must be in the future"
    return None


def build_request(path, *, title, description, tags, privacy="private", publish_at=None,
                  contains_synthetic_media=False, category_id="22") -> dict:
    tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
    status_body: dict[str, Any] = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                                   "containsSyntheticMedia": bool(contains_synthetic_media)}
    if publish_at is not None:
        status_body["publishAt"] = str(publish_at)
    body = {"snippet": {"title": str(title), "description": str(description or ""), "tags": tags,
                        "categoryId": str(category_id)},
            "status": status_body}
    p = Path(path)
    media = {"file": p.name, "size_bytes": p.stat().st_size if p.is_file() else None,
             "mime": mimetypes.guess_type(p.name)[0] or "application/octet-stream", "resumable": True,
             "chunk_bytes": CHUNK}
    return {"method": "POST", "url": UPLOAD_URL, "part": "snippet,status", "body": body, "media": media,
            "quota": "1 call of the Video Uploads bucket (default 100 calls/day)"}


def upload(config_dir, path, *, title, description, tags, privacy="private", publish_at=None,
           contains_synthetic_media=False, dry_run=False, category_id="22") -> dict:
    """{status: DRY_RUN|OK|NOT_CONNECTED|ERROR, video_id?, url?, request, ...}. Resumable upload."""
    tags = list(tags or [])
    err = _validate(title, description, [str(t) for t in tags], privacy, publish_at)
    request = build_request(path, title=title, description=description, tags=tags, privacy=privacy,
                            publish_at=publish_at, contains_synthetic_media=contains_synthetic_media,
                            category_id=category_id)
    if err:
        return {"status": "ERROR", "reason": err, "request": request}
    if dry_run:
        return {"status": "DRY_RUN", "request": request}
    if not Path(path).is_file():
        return {"status": "ERROR", "reason": f"файл не найден: {Path(path).name}", "request": request}
    try:
        service = _build_service(config_dir)
        if service is None:
            return {"status": "NOT_CONNECTED", "reason": NOT_CONNECTED_REASON, "request": request}
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(str(path), mimetype=request["media"]["mime"], chunksize=CHUNK, resumable=True)
        insert = service.videos().insert(part="snippet,status", body=request["body"], media_body=media)
        response, retries = None, 0
        while response is None:
            try:
                _, response = insert.next_chunk()
            except HttpError as exc:
                if getattr(exc, "status_code", None) in (500, 502, 503, 504) and retries < 5:
                    retries += 1
                    time.sleep(2 ** retries)
                    continue
                raise
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "reason": _safe(exc), "request": request}
    vid = response.get("id")
    stored = response.get("status") or {}
    out = {"status": "OK", "video_id": vid, "url": f"https://www.youtube.com/shorts/{vid}", "request": request,
           "stored_status": {k: stored.get(k) for k in ("privacyStatus", "uploadStatus", "publishAt",
                                                       "containsSyntheticMedia", "selfDeclaredMadeForKids")}}
    if stored.get("privacyStatus") and stored.get("privacyStatus") != privacy:
        out["warning"] = (f"YouTube сохранил privacyStatus={stored.get('privacyStatus')} вместо {privacy}: "
                          "вероятно, проект API не прошёл аудит (https://support.google.com/youtube/contact/yt_api_form)")
    return out


def channel_stats(config_dir, recent: int = 25) -> dict:
    """Subscribers, total views and stats of recent Shorts-length uploads; every number comes from the API."""
    try:
        service = _build_service(config_dir)
        if service is None:
            return {"status": "NOT_CONNECTED", "reason": NOT_CONNECTED_REASON}
        ch = _channel(service)
        if ch is None:
            return {"status": "ERROR", "reason": "у аккаунта нет канала"}
        ids: list[str] = []
        if ch.get("_uploads"):
            pl = service.playlistItems().list(part="contentDetails", playlistId=ch["_uploads"],
                                              maxResults=max(1, min(recent, 50))).execute()
            ids = [i["contentDetails"]["videoId"] for i in pl.get("items") or [] if i.get("contentDetails")]
        videos = []
        if ids:
            from .trends import parse_duration
            vr = service.videos().list(part="snippet,statistics,contentDetails,status", id=",".join(ids)).execute()
            for v in vr.get("items") or []:
                dur = parse_duration((v.get("contentDetails") or {}).get("duration"))
                st = v.get("statistics") or {}
                videos.append({"video_id": v.get("id"), "title": (v.get("snippet") or {}).get("title"),
                               "published_at": (v.get("snippet") or {}).get("publishedAt"), "duration_s": dur,
                               "privacy": (v.get("status") or {}).get("privacyStatus"),
                               "views": _int(st.get("viewCount")), "likes": _int(st.get("likeCount")),
                               "comments": _int(st.get("commentCount")),
                               "is_short_candidate": dur is not None and 0 < dur <= SHORT_MAX_S})
        shorts = [v for v in videos if v["is_short_candidate"]]
        return {"status": "OK", "channel": _public(ch), "subscribers": ch["subscribers"], "total_views": ch["views"],
                "recent_shorts": shorts, "recent_checked": len(videos),
                "note": "Shorts определены по длительности <=180 с (соотношение сторон API не отдаёт)."}
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "reason": _safe(exc)}
