"""Real-data gate. A record is shown or exported only with verifiable provenance: an https source URL and a fetch timestamp,
and no demo / synthetic marker. Everything else is archived (never silently dropped) and excluded from every view and export."""
from __future__ import annotations

from urllib.parse import urlsplit

FAKE_TLDS = (".invalid", ".test", ".example", ".localhost")
FAKE_HOSTS = ("localhost", "example.invalid", "127.0.0.1")


def _https_host(url) -> str | None:
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    return host or None


def unverified_reason(doc: dict) -> str | None:
    """None = the record has provenance; otherwise a short machine reason."""
    if not isinstance(doc, dict):
        return "not_a_record"
    if doc.get("demo"):
        return "demo_flag"
    if str(doc.get("data_quality", "")).lower() in ("synthetic", "demo", "sample", "fixture"):
        return "synthetic_quality"
    sid = str(doc.get("source_id") or doc.get("source") or "").lower()
    if not sid:
        return "no_source"
    if sid in ("demo", "seed", "sample", "fixture"):
        return "demo_source"
    host = _https_host(doc.get("source_url"))
    if host is None:
        return "no_https_source_url"
    if host in FAKE_HOSTS or host.endswith(FAKE_TLDS):
        return "fake_host"
    if not (doc.get("retrieved_at") or doc.get("fetched_at")):
        return "no_fetched_at"
    return None


def is_real(doc: dict) -> bool:
    return unverified_reason(doc) is None
