"""Vibe Studio safety: slugs, confined paths, static scan of generated apps,
egress secret check and the preview Content-Security-Policy.

The scan is HEURISTIC and says so: a determined obfuscator can defeat any
static check.  It is the first of three layers -- the scan (reject before the
owner ever previews), the sandboxed iframe without allow-same-origin (opaque
origin, no cookies, no storage of the Command Center) and a strict CSP header
whose ``connect-src`` is the same allowlist the scan enforces (default: none).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

# ----------------------------------------------------------------- limits
MAX_FILES = 20
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_REQUEST_CHARS = 4000
ALLOWED_EXT = (".html", ".css", ".js", ".json", ".svg", ".txt", ".md")
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
_RESERVED_WIN = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                 *(f"lpt{i}" for i in range(1, 10))}
FILE_RE = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_\-.]{0,63}$")


class UnsafePath(ValueError):
    pass


def valid_slug(slug: Any) -> str:
    s = str(slug or "")
    if not SLUG_RE.fullmatch(s) or s in _RESERVED_WIN or "--" in s:
        raise UnsafePath(f"invalid app slug {s[:40]!r}: use 1-40 chars [a-z0-9-], no leading/trailing dash")
    return s


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40].strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if not s or s in _RESERVED_WIN:
        s = "app"
    return s


def safe_rel_path(path: Any) -> str:
    """A relative POSIX path inside one app folder, at most one sub-directory
    level; rejects traversal, absolute and drive paths, backslashes, hidden and
    reserved names and unknown extensions."""
    raw = str(path or "")
    if not raw or "\x00" in raw or "\\" in raw or ":" in raw or raw.startswith(("/", "~")):
        raise UnsafePath(f"unsafe file path {raw[:80]!r}")
    pp = PurePosixPath(raw)
    parts = pp.parts
    if len(parts) > 2 or any(p in ("", ".", "..") for p in parts):
        raise UnsafePath(f"unsafe file path {raw[:80]!r}")
    for part in parts:
        if not FILE_RE.fullmatch(part) or part.startswith(".") or part.endswith("."):
            raise UnsafePath(f"unsafe file name {part[:40]!r}")
        if part.split(".")[0].lower() in _RESERVED_WIN:
            raise UnsafePath(f"reserved file name {part[:40]!r}")
    if not raw.lower().endswith(ALLOWED_EXT):
        raise UnsafePath(f"file type not allowed: {raw[:80]!r} (allowed: {', '.join(ALLOWED_EXT)})")
    return str(pp)


# ----------------------------------------------------------------- egress
_SECRET_PATTERNS = [
    ("api_key", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}")),
    ("openrouter_key", re.compile(r"\bsk-or-[A-Za-z0-9_\-]{10,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
    ("assignment", re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{16,}")),
    ("telegram_bot", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),
    ("local_path", re.compile(r"(?i)\b[a-z]:\\(?:users|bossman|windows)\\")),
    ("vault", re.compile(r"(?i)\b(?:vault|dpapi|provider-keys\.env|\.env\b|bcc\.db)\b")),
]


def secret_findings(text: str) -> list[str]:
    """Names (never values) of secret-like things found in text bound for a cloud model."""
    return [name for name, rx in _SECRET_PATTERNS if rx.search(text or "")]


# ----------------------------------------------------------------- scan
@dataclass
class ScanReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files: int = 0
    bytes: int = 0
    hosts: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        if msg not in self.errors:
            self.errors.append(msg)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings,
                "files": self.files, "bytes": self.bytes, "hosts": self.hosts}


_URL_RE = re.compile(r"""(?ix)
    (?: \b(?:https?|wss?|ftp)://[^\s"'`<>)\\]+ )      # absolute
  | (?: (?<![:\w/])//[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}[^\s"'`<>)\\]* )   # protocol-relative
""")
_XMLNS_OK = ("http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink", "http://www.w3.org/1999/xhtml",
             "http://www.w3.org/2000/xmlns/", "http://www.w3.org/XML/1998/namespace")

_NET_CALL_RE = re.compile(
    r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|RTCPeerConnection|"
    r"WebTransport|Worker|SharedWorker)\b|\bimport\s*\(|navigator\s*\.\s*serviceWorker")
_FETCH_LITERAL_RE = re.compile(r"""\b(?:fetch|importScripts)\s*\(\s*(['"`])([^'"`$]*)\1\s*[,)]""")
_EVAL_RES = [
    (re.compile(r"\beval\s*\("), "eval()"),
    (re.compile(r"\bnew\s+Function\b|\bFunction\s*\(|\bconstructor\s*\.\s*constructor\b"), "Function constructor"),
    (re.compile(r"\bset(?:Timeout|Interval)\s*\(\s*(['\"`])"), "string passed to setTimeout/setInterval"),
    (re.compile(r"\bdocument\s*\.\s*write(?:ln)?\s*\("), "document.write"),
    (re.compile(r"\b(?:window\s*\.\s*)?(?:top|parent)\s*\.\s*(?!postMessage)\w"), "access to parent/top window"),
    (re.compile(r"\bwindow\s*\.\s*open\s*\(|\bopener\b"), "window.open/opener"),
]
_GLOBAL_BRACKET = re.compile(r"\b(?:window|globalThis|self|top|parent)\s*\[")
_GLOBAL_BRACKET_LITERAL = re.compile(r"""\s*(['"])[A-Za-z_$][\w$]*\1\s*\]""")
_STORE_RE = re.compile(r"\b(?:localStorage|sessionStorage)\s*\.\s*setItem\s*\(\s*(['\"`])([^'\"`]*)\1", re.I)
_STORE_NAME_BAD = re.compile(r"(?i)secret|token|passw|api[_-]?key|credential|private|auth|session|cookie|vault")
_COOKIE_RE = re.compile(r"\bdocument\s*\.\s*cookie\s*=")
_HTML_BAD_TAGS = re.compile(r"(?i)<\s*(iframe|object|embed|base|applet|frame|frameset)\b")
_META_REFRESH = re.compile(r"(?i)<\s*meta[^>]+http-equiv\s*=\s*['\"]?refresh")
_FORM_ACTION = re.compile(r"(?i)<\s*form\b[^>]*\baction\s*=\s*['\"]?(?!#|['\"]?>)")
_SRC_ATTR = re.compile(r"""(?i)<\s*(?:script|link|img|source|audio|video|track|use|image)\b[^>]*?\b(?:src|href|xlink:href|poster)\s*=\s*(['"]?)([^'"\s>]+)\1""")
_CSS_URL = re.compile(r"""(?i)url\(\s*(['"]?)([^'")\s]+)\1\s*\)|@import\s+(?:url\()?\s*['"]([^'")]+)""")
_INLINE_HANDLER_SRC = re.compile(r"(?i)\bjavascript\s*:")


def _is_local_ref(ref: str) -> bool:
    r = ref.strip()
    if r.startswith(("data:image/", "data:font/", "data:audio/", "data:video/", "#")):
        return True
    if r.startswith("data:"):
        return False
    if re.match(r"(?i)^[a-z][a-z0-9+.\-]*:", r) or r.startswith("//") or r.startswith("/"):
        return False
    try:
        safe_rel_path(r.split("#")[0].split("?")[0])
        return True
    except UnsafePath:
        return False


def _host_allowed(url: str, allowlist: set[str]) -> bool:
    host = (urlsplit(url if "://" in url else "https:" + url).hostname or "").lower()
    return bool(host) and host in allowlist


def scan_files(files: dict[str, str], *, allowlist: list[str] | None = None) -> ScanReport:
    """Static safety scan of a generated app (path -> text)."""
    allow = {h.lower() for h in (allowlist or [])}
    rep = ScanReport(files=len(files), bytes=sum(len(c.encode("utf-8", "ignore")) for c in files.values()),
                     hosts=sorted(allow))
    if not files:
        rep.fail("no files")
        return rep
    if len(files) > MAX_FILES:
        rep.fail(f"too many files ({len(files)} > {MAX_FILES})")
    if rep.bytes > MAX_TOTAL_BYTES:
        rep.fail(f"app too large ({rep.bytes} > {MAX_TOTAL_BYTES} bytes)")
    if "index.html" not in files:
        rep.fail("index.html is required")
    for path, text in files.items():
        try:
            safe_rel_path(path)
        except UnsafePath as exc:
            rep.fail(str(exc))
            continue
        if len(text.encode("utf-8", "ignore")) > MAX_FILE_BYTES:
            rep.fail(f"{path}: file larger than {MAX_FILE_BYTES} bytes")
        if "\x00" in text:
            rep.fail(f"{path}: NUL byte")
        for finding in secret_findings(text):
            if finding in ("local_path", "vault"):
                rep.fail(f"{path}: references a local path or Bossman secret store ({finding})")
            else:
                rep.fail(f"{path}: hard-coded secret-like value ({finding})")
        low = path.lower()
        # ---- every URL literal, anywhere
        for m in _URL_RE.finditer(text):
            url = m.group(0).rstrip(".,;")
            if url in _XMLNS_OK or any(url.startswith(x) for x in _XMLNS_OK):
                continue
            if _host_allowed(url, allow):
                continue
            rep.fail(f"{path}: network URL to a non-allowlisted host: {url[:80]}")
        # ---- script-ish rules
        if low.endswith((".js", ".html", ".svg")):
            for rx, name in _EVAL_RES:
                if rx.search(text):
                    rep.fail(f"{path}: forbidden construct: {name}")
            for gm in _GLOBAL_BRACKET.finditer(text):
                if not _GLOBAL_BRACKET_LITERAL.match(text, gm.end()):
                    rep.fail(f"{path}: forbidden construct: computed global access")
                    break
            for m in _NET_CALL_RE.finditer(text):
                call = m.group(0).strip()
                if call in ("fetch", "importScripts"):
                    lit = _FETCH_LITERAL_RE.search(text, max(0, m.start() - 1))
                    if lit and lit.start() == m.start():
                        target = lit.group(2)
                        if _is_local_ref(target) or _host_allowed(target, allow):
                            continue
                        rep.fail(f"{path}: {call}() to non-allowlisted target {target[:60]!r}")
                        continue
                    rep.fail(f"{path}: {call}() with a non-literal target cannot be verified")
                elif call.startswith("import"):
                    rep.fail(f"{path}: dynamic import() is not allowed")
                elif call == "Worker" or call == "SharedWorker":
                    rep.fail(f"{path}: {call} is not allowed")
                else:
                    rep.fail(f"{path}: forbidden network API {call}")
            for m in _STORE_RE.finditer(text):
                if _STORE_NAME_BAD.search(m.group(2)):
                    rep.fail(f"{path}: storage key {m.group(2)[:40]!r} looks like a secret")
            if _COOKIE_RE.search(text):
                rep.fail(f"{path}: writes document.cookie")
            if _INLINE_HANDLER_SRC.search(text):
                rep.fail(f"{path}: javascript: URL")
        if low.endswith((".html", ".svg")):
            if _HTML_BAD_TAGS.search(text):
                rep.fail(f"{path}: iframe/object/embed/base tags are not allowed")
            if _META_REFRESH.search(text):
                rep.fail(f"{path}: meta refresh")
            if _FORM_ACTION.search(text):
                rep.fail(f"{path}: form with an action (submit would leave the sandbox)")
            for m in _SRC_ATTR.finditer(text):
                ref = m.group(2)
                if not _is_local_ref(ref) and not _host_allowed(ref, allow):
                    rep.fail(f"{path}: external or unsafe reference {ref[:80]!r}")
        if low.endswith((".css", ".html", ".svg")):
            for m in _CSS_URL.finditer(text):
                ref = m.group(2) or m.group(3) or ""
                if ref and not _is_local_ref(ref) and not _host_allowed(ref, allow):
                    rep.fail(f"{path}: css reference to {ref[:80]!r}")
        if low.endswith(".json"):
            try:
                import json
                json.loads(text)
            except ValueError:
                rep.warnings.append(f"{path}: not valid JSON")
    return rep


# ----------------------------------------------------------------- preview
def preview_csp(allowlist: list[str] | None = None) -> str:
    hosts = " ".join(f"https://{h}" for h in sorted({h.lower() for h in (allowlist or []) if h}))
    connect = hosts or "'none'"
    return ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src data: blob:; font-src data:; media-src data: blob:; "
            f"connect-src {connect}; form-action 'none'; base-uri 'none'; object-src 'none'; "
            "frame-src 'none'; frame-ancestors 'self'")


SANDBOX_ATTR = "allow-scripts"        # NOT allow-same-origin: opaque origin, no cookies, no storage access


def inline_for_preview(files: dict[str, str]) -> str:
    """One self-contained HTML document: local <link>/<script src> are inlined so the
    sandboxed (opaque-origin, cookie-less) frame needs no further requests."""
    html = files.get("index.html", "")

    def script(m: re.Match) -> str:
        ref = m.group(3).split("?")[0].split("#")[0]
        body = files.get(ref)
        if body is None:
            return m.group(0)
        attrs = re.sub(r"""(?i)\s*\bsrc\s*=\s*(['"]?)[^'"\s>]+\1""", "", m.group(1))
        # escaped outside the f-string: a backslash inside the expression is a SyntaxError before Python 3.12
        escaped = body.replace("</script", "<\\/script")
        return f"<script{attrs}>{escaped}</script>"

    def link(m: re.Match) -> str:
        tag = m.group(0)
        if not re.search(r"(?i)rel\s*=\s*['\"]?stylesheet", tag):
            return tag
        href = re.search(r"""(?i)\bhref\s*=\s*(['"]?)([^'"\s>]+)\1""", tag)
        body = files.get(href.group(2).split("?")[0]) if href else None
        if body is None:
            return tag
        escaped = body.replace("</style", "<\\/style")
        return f"<style>{escaped}</style>"

    html = re.sub(r"""(?is)<script([^>]*?\bsrc\s*=\s*(['"]?)([^'"\s>]+)\2[^>]*)>\s*</script>""",
                  script, html)
    html = re.sub(r"(?is)<link\b[^>]*>", link, html)
    return html
