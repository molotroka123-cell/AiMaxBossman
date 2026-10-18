"""`web_learn` - the bounded web-learning task of the self-improvement orchestra.

First safe slice, not unlimited autonomy:
  (a) the topic comes from the owner's goal list or from a failed test;
  (b) one search through String;
  (c) at most MAX_PAGES (5) pages fetched, each through the guarded path;
  (d) a short sourced note (URL list, retrieved-at, license/terms flag, what it
      supports) is written as a CANDIDATE lesson into an isolated directory
      outside the repository's stable code - never auto-merged, never touches
      stable code;
  (e) promotion needs an independent verifier verdict (fail-closed default);
  (f) topics outside the allow-list are refused before any network call.

Fetched pages are untrusted data. A page with prompt-injection markers is
dropped from the note; page text is never copied beyond a short excerpt.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import policy
from .policy import PolicyRefusal

TASK_TYPE = "web_learn"
MAX_PAGES = 5
EXCERPT_CHARS = 280
ORIGINS = ("owner_goal", "failed_test")

# (f) allow-list: the topic must be about public technical knowledge.
_ALLOW_RE = re.compile(
    r"\b(release|version|changelog|install|setup|build|compile|error|exception|bug|fix|api|sdk|cli|"
    r"library|framework|package|model|weights|checkpoint|benchmark|inference|quantiz\w*|gguf|onnx|"
    r"docs?|documentation|tutorial|guide|how to|spec|standard|rfc|license|python|rust|node|typescript|"
    r"javascript|windows|linux|docker|git|github|sqlite|fastapi|pytest|ffmpeg|gpu|cuda|vulkan|rocm|"
    r"amd|nvidia|llm|diffusion|lora|comfyui|ollama|mcp|prompt|agent|telegram bot api|http|tls|"
    r"performance|optimi[sz]\w*|security advisory|cve)\b|\.cpp\b|\.py\b|\.js\b", re.I)
# Refused regardless of the allow-list.
_REFUSE_RES = [
    (re.compile(r"\b(password|passwd|credential|credentials|login|token|api key|apikey|secret|cookie|session)\b.*"
                r"\b(leak|dump|steal|crack|find|get|hunt|bypass|list)\b|"
                r"\b(leak|dump|steal|crack|hunt|bypass)\b.*\b(password|credential|token|api key|cookie)\b|"
                r"\b(combolist|stealer|keylogger|phishing|carding)\b", re.I), "credential hunting"),
    (re.compile(r"\b(paywall|captcha)\b.*\b(bypass|crack|get around|remove)\b|\b(bypass|get around)\b.*"
                r"\b(paywall|captcha|login wall)\b|behind (a )?login|private (account|profile|group)", re.I),
     "scraping behind logins / paywall or captcha bypass"),
    (re.compile(r"\b(home address|phone number|where does .* live|who is .* (dating|married)|dox+|"
                r"social security|passport number|find (him|her|them)|personal data of|"
                r"email address of|кто такой|где живет|где живёт|адрес проживания|номер телефона)\b", re.I),
     "personal-data lookup about a private individual"),
    (re.compile(r"\b(csam|child porn|bomb[- ]making|synthesi[sz]e (meth|nerve)|ransomware (builder|source)|"
                r"ddos for hire|buy (drugs|weapons)|counterfeit)\b", re.I), "illegal content"),
]


class WebLearnRefused(PolicyRefusal):
    """Topic or origin is outside the allow-list."""


def classify_topic(topic: str, origin: str) -> tuple[bool, str]:
    t = " ".join(str(topic or "").split())
    if origin not in ORIGINS:
        return False, f"origin must be one of {ORIGINS} (owner goal list or a failed test)"
    if not (8 <= len(t) <= 240):
        return False, "topic must be 8..240 characters"
    why = policy.sensitive_reason(t)
    if why:
        return False, f"topic {why}"
    for pat, reason in _REFUSE_RES:
        if pat.search(t):
            return False, reason
    if not _ALLOW_RE.search(t):
        return False, "topic is not about public technical knowledge (allow-list)"
    return True, ""


# ---------------------------------------------------------------- license heuristics

_LICENSES = [("MIT", re.compile(r"\bMIT License\b", re.I)), ("Apache-2.0", re.compile(r"Apache License,? Version 2", re.I)),
             ("BSD", re.compile(r"\bBSD[- ]\d?-?Clause\b", re.I)), ("GPL", re.compile(r"\bGNU (Affero |Lesser )?General Public License\b", re.I)),
             ("CC", re.compile(r"\bCreative Commons|CC[- ]BY\b", re.I))]
_RESTRICTIVE = re.compile(r"all rights reserved|do not (copy|reproduce|scrape)|no (scraping|crawling)|"
                          r"prohibited from (copying|scraping)", re.I)


def license_flag(text: str) -> str:
    if _RESTRICTIVE.search(text or ""):
        return "restrictive: do not copy text, link only"
    for name, pat in _LICENSES:
        if pat.search(text or ""):
            return f"open-license-mention:{name} (verify on the source)"
    return "unknown: link only, do not copy text"


# ---------------------------------------------------------------- parsing

_URL_RE = re.compile(r"https?://[^\s<>\"')\]}]+")


def _search_urls(raw: str) -> list[tuple[str, str]]:
    """[(url, title)] from the search payload (JSON when possible, regex otherwise)."""
    found: list[tuple[str, str]] = []
    try:
        data = json.loads(raw)
    except ValueError:
        data = None

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            url = node.get("url") or node.get("link")
            if isinstance(url, str) and url.startswith("http"):
                found.append((url, str(node.get("title") or "")[:120]))
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    if data is not None:
        walk(data)
    if not found:
        found = [(u.rstrip(".,;"), "") for u in _URL_RE.findall(raw)]
    return found


def _excerpt(raw: str) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"[#*`>|\[\]]", " ", raw)).strip()
    return text[:EXCERPT_CHARS]


# ---------------------------------------------------------------- isolation

def assert_isolated(out_root: Path) -> Path:
    """Candidates may only live in a dedicated directory, never inside stable code."""
    p = Path(out_root).resolve()
    if p.name != "candidates" or p.parent.name != "web_learn":
        raise WebLearnRefused("candidate directory must be .../web_learn/candidates")
    for anc in p.parents:
        if (anc / "bcc" / "__init__.py").is_file() or (anc / ".git").exists():
            raise WebLearnRefused("candidate directory is inside a code repository")
    return p


# ---------------------------------------------------------------- the task

@dataclass(slots=True)
class LearnReport:
    ok: bool
    status: str                       # candidate | refused | no_sources | search_failed
    reason: str = ""
    candidate_id: str = ""
    path: str = ""
    pages: list[dict] = field(default_factory=list)


async def run_web_learn(topic: str, origin: str, *, web: Any, out_root: Path,
                        now: Callable[[], datetime] | None = None) -> LearnReport:
    """`web` needs async search(query) and fetch(url) returning WebResult-like objects."""
    ok, why = classify_topic(topic, origin)
    if not ok:
        web.audit.line("web_learn", "-", "refused") if hasattr(web, "audit") else None
        return LearnReport(False, "refused", why)
    root = assert_isolated(out_root)
    clock = now or (lambda: datetime.now(timezone.utc))
    topic = " ".join(topic.split())
    sres = await web.search(topic)
    if not sres.ok:
        return LearnReport(False, "search_failed", f"{sres.status}: {sres.detail}")
    pages: list[dict] = []
    seen: set[str] = set()
    for url, title in _search_urls(sres.raw):
        if len(pages) >= MAX_PAGES:
            break
        domain = policy.domain_of(url)
        if domain in seen:                                   # one page per domain: diverse sources
            continue
        try:
            policy.check_public_url(url)
        except PolicyRefusal:
            continue
        seen.add(domain)
        fres = await web.fetch(url)
        stamp = clock().strftime("%Y-%m-%dT%H:%M:%SZ")
        if not fres.ok:
            pages.append({"url": url, "retrieved_at": stamp, "status": fres.status, "used": False})
            continue
        if fres.injection_suspected or policy.injection_suspected(fres.raw):
            pages.append({"url": url, "retrieved_at": stamp, "status": "dropped: injection markers",
                          "used": False})
            continue
        pages.append({"url": url, "title": title, "retrieved_at": stamp, "status": "ok", "used": True,
                      "license_terms": license_flag(fres.raw), "supports": _excerpt(fres.raw)})
    used = [p for p in pages if p.get("used")]
    if not used:
        return LearnReport(False, "no_sources", "no page passed policy and fetch", pages=pages)
    cid = f"{clock().strftime('%Y%m%dT%H%M%SZ')}-{policy.stable_id(topic, origin)}"
    d = root / cid
    d.mkdir(parents=True, exist_ok=True)
    meta = {"id": cid, "task_type": TASK_TYPE, "topic": topic, "origin": origin, "status": "candidate",
            "verified": False, "promotable": False, "created_at": clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sources": pages}
    (d / "candidate.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Candidate lesson (UNVERIFIED): {topic}", "",
             "Status: candidate. Not promoted. Needs an independent verifier before use.", ""]
    for p in used:
        lines += [f"- {p['url']}", f"  - retrieved_at: {p['retrieved_at']}",
                  f"  - license/terms: {p['license_terms']}", f"  - supports (excerpt, untrusted): {p['supports']}"]
    (d / "note.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return LearnReport(True, "candidate", candidate_id=cid, path=str(d), pages=pages)


# ---------------------------------------------------------------- promotion gate

def promote_candidate(candidate_dir: Path, verification: dict | None) -> dict:
    """Fail-closed. Records a verified state only on a PASS from a verifier that is
    not the author. Never copies anything into stable code or main."""
    d = Path(candidate_dir)
    meta_path = d / "candidate.json"
    if not meta_path.is_file():
        raise WebLearnRefused("no such candidate")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    v = verification or {}
    verifier = str(v.get("verifier") or "").strip()
    author = str(v.get("author") or meta.get("author") or "web_learn").strip()
    if v.get("verdict") != "PASS" or not verifier:
        raise WebLearnRefused("independent verification (verdict PASS) is required")
    if verifier.lower() == author.lower():
        raise WebLearnRefused("verifier must differ from the author")
    meta.update({"status": "verified_for_review", "verified": True, "promotable": True,
                 "verification": {"verifier": verifier, "verdict": "PASS",
                                  "evidence_sha256": str(v.get("evidence_sha256") or "")[:64]}})
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
