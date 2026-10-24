"""Measure observable facts of one web page and turn them into findings.

Nothing here guesses: each finding is built from a value that was measured in
a real headless Chromium (Playwright) or from an HTTP status code. A finding
without evidence cannot be constructed (``Finding.__post_init__`` raises).

Politeness: robots.txt is checked before any page or link request, one
browser is used at a time and closed at the end, link checks are sequential
with a delay, and every request identifies itself with ``USER_AGENT``.
"""
from __future__ import annotations

import datetime as _dt
import ipaddress
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import USER_AGENT

SEVERITIES = ("high", "medium", "low")
DESKTOP_VIEWPORT = {"width": 1366, "height": 900}
MOBILE_VIEWPORT = {"width": 390, "height": 844}

# Thresholds (documented so every finding can be traced to a rule).
SLOW_LOAD_MS = 3000
HEAVY_PAGE_BYTES = 3 * 1024 * 1024
MANY_REQUESTS = 100
MIN_FONT_PX = 12.0
SMALL_FONT_SHARE = 0.20
MIN_TAP_PX = 24.0  # WCAG 2.2 target size (minimum)
COPYRIGHT_MAX_AGE_YEARS = 3
LINK_CHECK_CAP = 30
LINK_TIMEOUT_S = 8.0

CTA_KEYWORDS = (
    # cz
    "kontakt", "objedn", "rezerv", "poptáv", "poptav", "zavolej", "napište",
    "napiste", "volejte", "termín", "termin", "nezávazn", "nezavazn",
    # en
    "contact", "book", "order", "reserve", "reservation", "appointment",
    "call us", "get a quote", "enquir", "inquir",
)
BOOKING_KEYWORDS = ("rezervac", "rezervovat", "objednat se", "online objedn",
                    "booking", "book now", "reservation", "appointment", "reservio")
# medical/dental practice of any size (gates the "no online booking" check)
CLINIC_KEYWORDS = ("klinik", "ordinac", "zubní", "zubni", "lékař", "lekar",
                   "mudr", "stomatolog", "dentáln", "dentaln", "fyzioterap",
                   "veterin", "clinic", "dental", "dentist", "physio")
# multi-doctor clinic scale (drives the premium offer tier)
CLINIC_SCALE_KEYWORDS = ("klinik", "clinic", "poliklinik", "centrum", "center", "centre")
# page says it serves foreigners (drives the multilingual check + premium tier)
FOREIGN_KEYWORDS = ("zahraničn", "zahranicn", "cizinc", "expat", "foreign patient",
                    "english speaking", "english-speaking", "mluvíme anglicky",
                    "mluvime anglicky", "we speak english", "international patient", "turist")
ESHOP_KEYWORDS = ("košík", "kosik", "do košíku", "add to cart", "cart",
                  "e-shop", "eshop", "pokladna", "checkout")
LANG_LABELS = {"cz", "cs", "en", "de", "ru", "ua", "uk", "sk", "pl", "fr",
               "english", "deutsch", "čeština", "cestina", "русский", "українська"}


class EvidenceError(ValueError):
    """Raised when someone tries to build a finding without measured evidence."""


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str
    evidence: dict
    fix_suggestion: str

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise EvidenceError("finding id is required")
        if self.severity not in SEVERITIES:
            raise EvidenceError(f"severity must be one of {SEVERITIES}")
        if not isinstance(self.evidence, dict) or not self.evidence:
            raise EvidenceError(f"finding {self.id!r} has no evidence")
        if all(v in (None, "", [], {}) for v in self.evidence.values()):
            raise EvidenceError(f"finding {self.id!r} evidence is empty")
        if not self.fix_suggestion or not self.fix_suggestion.strip():
            raise EvidenceError(f"finding {self.id!r} has no fix suggestion")

    def to_dict(self) -> dict:
        return asdict(self)


def validate_findings(items: list[dict]) -> list[Finding]:
    """Re-validate findings coming from JSON/LLM output; invalid ones raise."""
    return [Finding(**{k: item.get(k) for k in ("id", "severity", "evidence", "fix_suggestion")})
            for item in items]


@dataclass
class AuditResult:
    url: str
    final_url: str = ""
    status: str = "ok"  # ok | skipped_robots | http_error | load_failed
    http_status: int | None = None
    audited_at: str = ""
    findings: list[Finding] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    screenshots: list[dict] = field(default_factory=list)
    skipped_checks: list[dict] = field(default_factory=list)
    requests_made: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["findings"] = [f.to_dict() for f in self.findings]
        return d


# ---------------------------------------------------------------- robots / http

class _Robots:
    """robots.txt cache per origin; missing/unreadable robots.txt allows all."""

    def __init__(self, user_agent: str, timeout: float = LINK_TIMEOUT_S):
        self.ua = user_agent
        self.timeout = timeout
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.fetches = 0

    def allowed(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._cache:
            self._cache[origin] = self._load(origin + "/robots.txt")
        rp = self._cache[origin]
        return True if rp is None else rp.can_fetch(self.ua, url)

    def _load(self, robots_url: str):
        self.fetches += 1
        req = urllib.request.Request(robots_url, headers={"User-Agent": self.ua})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                text = resp.read(512 * 1024).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):  # RFC 9309: treat as full disallow
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(["User-agent: *", "Disallow: /"])
                return rp
            return None
        except (urllib.error.URLError, OSError, ValueError):
            return None
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(text.splitlines())
        return rp


def _check_link(url: str, timeout: float) -> int | str:
    """HEAD, falling back to GET for servers that refuse HEAD. Returns status or error text."""
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                if method == "GET":
                    resp.read(1024)
                return int(resp.status)
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in (405, 501, 403):
                continue
            return int(exc.code)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if method == "HEAD":
                continue
            return f"error: {type(exc).__name__}: {str(exc)[:120]}"
    return "error: unreachable"


def _is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------- DOM probes

_DOM_PROBE = r"""
() => {
  const scale = (window.visualViewport && window.visualViewport.scale) || 1;
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const sel = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    else if (el.classList.length) s += '.' + Array.from(el.classList).slice(0, 2).join('.');
    return s;
  };
  // text elements with own (direct) text
  let textTotal = 0, textSmall = 0; const smallSamples = [];
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    if (['SCRIPT','STYLE','NOSCRIPT','svg','SVG'].includes(el.tagName)) continue;
    const own = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (!own || !vis(el)) continue;
    textTotal++;
    const fs = parseFloat(getComputedStyle(el).fontSize) * scale;
    if (fs < %MIN_FONT%) {
      textSmall++;
      if (smallSamples.length < 5) smallSamples.push({selector: sel(el), effective_font_px: Math.round(fs * 10) / 10,
        text: el.textContent.trim().slice(0, 40)});
    }
  }
  // tap targets (inline links inside running text are exempt, like WCAG 2.5.8)
  const taps = document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, [role=button]');
  let tapTotal = 0, tapSmall = 0; const tapSamples = [];
  for (const el of taps) {
    if (!vis(el)) continue;
    const cs = getComputedStyle(el);
    if (el.tagName === 'A' && cs.display === 'inline' && el.closest('p, li, td, span') &&
        (el.parentElement.textContent.trim().length > el.textContent.trim().length + 20)) continue;
    tapTotal++;
    const r = el.getBoundingClientRect();
    const w = r.width * scale, h = r.height * scale;
    if (w < %MIN_TAP% || h < %MIN_TAP%) {
      tapSmall++;
      if (tapSamples.length < 5) tapSamples.push({selector: sel(el), width_px: Math.round(w), height_px: Math.round(h),
        text: (el.textContent || el.value || '').trim().slice(0, 30)});
    }
  }
  return {
    scale: scale,
    inner_width: window.innerWidth,
    scroll_width: Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0),
    text_total: textTotal, text_small: textSmall, small_text_samples: smallSamples,
    tap_total: tapTotal, tap_small: tapSmall, tap_samples: tapSamples,
  };
}
""".replace("%MIN_FONT%", str(MIN_FONT_PX)).replace("%MIN_TAP%", str(MIN_TAP_PX))

_PAGE_PROBE = r"""
() => {
  const nav = performance.getEntriesByType('navigation')[0];
  const res = performance.getEntriesByType('resource');
  let bytes = nav ? (nav.transferSize || nav.encodedBodySize || 0) : 0;
  for (const r of res) bytes += (r.transferSize || r.encodedBodySize || 0);
  const meta = (n) => { const m = document.querySelector(`meta[name="${n}"]`); return m ? (m.getAttribute('content') || '') : null; };
  const links = Array.from(document.querySelectorAll('a[href]')).map(a => ({href: a.href, raw: a.getAttribute('href'),
     text: (a.textContent || '').trim().slice(0, 60), hreflang: a.getAttribute('hreflang') || ''}));
  const imgs = Array.from(document.images);
  const noAlt = imgs.filter(i => !i.hasAttribute('alt')).map(i => (i.currentSrc || i.src || '').slice(0, 80));
  const insecure = [];
  for (const el of document.querySelectorAll('img[src], script[src], iframe[src], link[rel=stylesheet][href], source[src], video[src], audio[src]')) {
    const u = el.getAttribute('src') || el.getAttribute('href') || '';
    if (/^http:\/\//i.test(u)) insecure.push(u.slice(0, 120));
  }
  const buttons = Array.from(document.querySelectorAll('button, input[type=submit], input[type=button], [role=button]'))
     .map(b => (b.textContent || b.value || '').trim().slice(0, 60));
  return {
    load_ms: nav ? Math.round(nav.loadEventEnd - nav.startTime) : null,
    dom_content_loaded_ms: nav ? Math.round(nav.domContentLoadedEventEnd - nav.startTime) : null,
    transfer_bytes: bytes,
    resource_count: res.length + 1,
    title: document.title || '',
    meta_description: meta('description'),
    viewport_meta: meta('viewport'),
    lang: document.documentElement.getAttribute('lang') || '',
    h1_count: document.querySelectorAll('h1').length,
    h1_text: Array.from(document.querySelectorAll('h1')).map(h => h.textContent.trim().slice(0, 80)).slice(0, 3),
    links: links.slice(0, 400),
    image_count: imgs.length,
    images_without_alt: noAlt,
    insecure_subresources: insecure,
    forms: document.querySelectorAll('form').length,
    buttons: buttons.slice(0, 60),
    hreflang_alternates: Array.from(document.querySelectorAll('link[rel=alternate][hreflang]')).map(l => l.getAttribute('hreflang')),
    body_text: (document.body ? document.body.innerText : '').slice(0, 60000),
  };
}
"""

_COPYRIGHT_RE = re.compile(
    r"(?:©|\(c\)|copyright)\s*(?:[^\d\n]{0,20})?((?:19|20)\d{2})(?:\s*[-–—]\s*((?:19|20)\d{2}))?",
    re.IGNORECASE,
)


def _copyright_year(text: str) -> tuple[int, str] | None:
    best = None
    for m in _COPYRIGHT_RE.finditer(text):
        years = [int(y) for y in m.groups() if y]
        y = max(years)
        if best is None or y > best[0]:
            best = (y, m.group(0).strip()[:60])
    return best


def _has_kw(text: str, keywords) -> list[str]:
    low = text.lower()
    return sorted({k for k in keywords if k in low})


_LANG_ALIASES = {"cz": "cs", "čeština": "cs", "cestina": "cs", "english": "en", "deutsch": "de",
                 "ua": "uk", "українська": "uk", "русский": "ru"}


def _language_versions(data: dict, origin_host: str) -> set[str]:
    """Languages the site offers, from hreflang attributes, CZ/EN switcher links and /en/-style paths."""
    langs = {l["hreflang"].lower()[:2] for l in data["links"] if l["hreflang"]}
    langs |= {h.lower()[:2] for h in data["hreflang_alternates"] if h and h != "x-default"}
    for l in data["links"]:
        t = l["text"].strip().lower()
        if t in LANG_LABELS:
            langs.add(_LANG_ALIASES.get(t, t))
        p = urllib.parse.urlsplit(l["href"] or "")
        m = re.match(r"^/(?:[^/]+/)?(en|de|ru|uk|ua|sk|pl|fr)(/|$)", p.path)
        if m and p.netloc == origin_host:
            langs.add(_LANG_ALIASES.get(m.group(1), m.group(1)))
    if langs and data.get("lang"):
        langs.add(data["lang"].lower()[:2])
    return langs


# ---------------------------------------------------------------- main audit

def audit_url(
    url: str,
    out_dir: str | Path,
    *,
    current_year: int | None = None,
    polite_delay_s: float = 1.0,
    link_cap: int = LINK_CHECK_CAP,
    check_links: bool = True,
    nav_timeout_ms: int = 30000,
) -> AuditResult:
    """Audit one page. Screenshots go to ``out_dir`` (keep it outside git)."""
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    year = current_year or _dt.date.today().year
    res = AuditResult(url=url, audited_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"))
    robots = _Robots(USER_AGENT)

    if not robots.allowed(url):
        res.status = "skipped_robots"
        res.skipped_checks.append({"check": "all", "reason": "robots.txt disallows this URL for our user agent"})
        res.requests_made = robots.fetches
        return res

    host = urllib.parse.urlsplit(url).hostname or ""
    slug = re.sub(r"[^a-z0-9]+", "-", (host + urllib.parse.urlsplit(url).path).lower()).strip("-")[:60] or "page"
    console_errors: list[str] = []
    requests_seen: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            # ---- desktop
            ctx = browser.new_context(viewport=DESKTOP_VIEWPORT, user_agent=USER_AGENT)
            page = ctx.new_page()
            page.on("console", lambda m: console_errors.append(m.text[:200]) if m.type == "error" else None)
            page.on("pageerror", lambda e: console_errors.append(f"pageerror: {str(e)[:200]}"))
            page.on("request", lambda r: requests_seen.append(r.url))
            try:
                resp = page.goto(url, wait_until="load", timeout=nav_timeout_ms)
            except PWError as exc:
                res.status = "load_failed"
                res.metrics["load_error"] = str(exc)[:300]
                res.findings.append(Finding(
                    "page_load_failed", "high", {"url": url, "error": str(exc)[:200]},
                    "Zkontrolovat hosting/DNS/certifikát – stránka se v prohlížeči nenačetla."))
                return res
            res.http_status = resp.status if resp else None
            res.final_url = page.url
            # the HTML document is downloaded ONCE; mobile passes replay it from memory (polite)
            doc = {"status": resp.status if resp else 200, "body": resp.body() if resp else b"",
                   "ctype": (resp.headers.get("content-type") if resp else None) or "text/html"}
            doc_url = res.final_url.split("#")[0]

            def _replay(route):  # Playwright passes (route, request) only to 2-arg handlers
                route.fulfill(status=doc["status"], body=doc["body"], headers={"content-type": doc["ctype"]})
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except PWError:
                pass
            data = page.evaluate(_PAGE_PROBE)
            desk_shot = out / f"{slug}-desktop.png"
            page.screenshot(path=str(desk_shot))
            res.screenshots.append({"viewport": "desktop 1366x900", "path": str(desk_shot),
                                    "bytes": desk_shot.stat().st_size})
            ctx.close()

            # ---- mobile (real mobile emulation: viewport meta is honoured)
            mctx = browser.new_context(viewport=MOBILE_VIEWPORT, is_mobile=True, has_touch=True,
                                       device_scale_factor=2, user_agent=USER_AGENT)
            mctx.route(lambda u: u.split("#")[0] == doc_url, _replay)
            mpage = mctx.new_page()
            mpage.goto(res.final_url or url, wait_until="load", timeout=nav_timeout_ms)
            mobile = mpage.evaluate(_DOM_PROBE)
            mob_shot = out / f"{slug}-mobile.png"
            mpage.screenshot(path=str(mob_shot))
            res.screenshots.append({"viewport": "mobile 390x844", "path": str(mob_shot),
                                    "bytes": mob_shot.stat().st_size})
            mctx.close()
            # overflow measured in a plain 390px-wide window (no zoom-out), the width
            # a phone actually has; is_mobile emulation widens the layout viewport.
            octx = browser.new_context(viewport=MOBILE_VIEWPORT, user_agent=USER_AGENT)
            octx.route(lambda u: u.split("#")[0] == doc_url, _replay)
            opage = octx.new_page()
            opage.goto(res.final_url or url, wait_until="load", timeout=nav_timeout_ms)
            overflow = opage.evaluate(
                "() => ({inner_width: window.innerWidth, scroll_width: Math.max("
                "document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0)})")
            octx.close()
        finally:
            browser.close()

    page_requests = 1 + robots.fetches  # one HTML document download (+ its subresources) and robots.txt
    res.metrics.update({
        "load_ms": data["load_ms"], "dom_content_loaded_ms": data["dom_content_loaded_ms"],
        "transfer_bytes": data["transfer_bytes"], "resource_count": data["resource_count"],
        "title": data["title"], "meta_description": data["meta_description"],
        "viewport_meta": data["viewport_meta"], "lang": data["lang"], "h1_count": data["h1_count"],
        "image_count": data["image_count"], "forms": data["forms"],
        "mobile": mobile, "mobile_overflow_window": overflow,
        "console_errors": console_errors[:10],
    })

    F = res.findings
    final = res.final_url or url
    if res.http_status and res.http_status >= 400:
        res.status = "http_error"
        F.append(Finding("http_error", "high", {"url": final, "http_status": res.http_status},
                         "Opravit chybu serveru / chybějící stránku, aby se web zobrazil."))
        res.requests_made = page_requests
        return res

    # ---- https / mixed content
    scheme = urllib.parse.urlsplit(final).scheme
    if _is_loopback(urllib.parse.urlsplit(final).hostname or ""):
        res.skipped_checks.append({"check": "https", "reason": "loopback test host – not applicable"})
    elif scheme != "https":
        F.append(Finding("no_https", "high", {"final_url": final, "scheme": scheme},
                         "Zapnout HTTPS (např. certifikát Let's Encrypt) a přesměrovat http → https."))
    if scheme == "https":
        insecure = data["insecure_subresources"] + [u for u in requests_seen if u.startswith("http://")]
        insecure = sorted(set(insecure))
        if insecure:
            F.append(Finding("mixed_content", "medium", {"insecure_urls": insecure[:5], "count": len(insecure)},
                             "Načítat všechny obrázky/skripty přes https://."))

    # ---- speed / weight
    if data["load_ms"] is not None and data["load_ms"] > SLOW_LOAD_MS:
        F.append(Finding("slow_load", "medium", {"load_ms": data["load_ms"], "threshold_ms": SLOW_LOAD_MS},
                         "Zmenšit obrázky (WebP/AVIF), odložit skripty, zapnout cache a kompresi."))
    if data["transfer_bytes"] > HEAVY_PAGE_BYTES:
        F.append(Finding("heavy_page", "medium",
                         {"transfer_bytes": data["transfer_bytes"], "threshold_bytes": HEAVY_PAGE_BYTES},
                         "Zkomprimovat obrázky a odstranit nepoužívané knihovny."))
    if data["resource_count"] > MANY_REQUESTS:
        F.append(Finding("many_requests", "low", {"requests": data["resource_count"], "threshold": MANY_REQUESTS},
                         "Sloučit a omezit počet načítaných souborů."))

    # ---- mobile
    if not data["viewport_meta"]:
        F.append(Finding("no_viewport_meta", "high",
                         {"meta_viewport": "missing", "mobile_zoom_scale": round(mobile["scale"], 3)},
                         "Přidat <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> a responzivní CSS."))
    if overflow["scroll_width"] > overflow["inner_width"] + 2:
        F.append(Finding("mobile_horizontal_overflow", "high",
                         {"viewport_width_px": overflow["inner_width"], "content_width_px": overflow["scroll_width"]},
                         "Odstranit pevné šířky (width: 1200px, tabulkové layouty), použít max-width: 100 %."))
    if mobile["text_total"] and mobile["text_small"] / mobile["text_total"] >= SMALL_FONT_SHARE:
        F.append(Finding("small_text_mobile", "medium",
                         {"small_text_elements": mobile["text_small"], "text_elements": mobile["text_total"],
                          "min_font_px": MIN_FONT_PX, "samples": mobile["small_text_samples"][:3]},
                         "Základní písmo alespoň 16 px a správný viewport."))
    if mobile["tap_small"]:
        F.append(Finding("small_tap_targets", "medium" if mobile["tap_small"] >= 3 else "low",
                         {"small_targets": mobile["tap_small"], "targets_checked": mobile["tap_total"],
                          "min_px": MIN_TAP_PX, "samples": mobile["tap_samples"][:3]},
                         "Zvětšit odkazy a tlačítka na min. 24×24 px (ideálně 44 px) s odstupy."))

    # ---- basic SEO / semantics
    if not data["title"].strip():
        F.append(Finding("missing_title", "medium", {"title": "empty or missing"},
                         "Doplnit výstižný <title> s názvem firmy a oborem/městem."))
    if not (data["meta_description"] or "").strip():
        F.append(Finding("missing_meta_description", "low", {"meta_description": "missing"},
                         "Doplnit meta description (do ~155 znaků) – zobrazuje se ve výsledcích Googlu."))
    if data["h1_count"] == 0:
        F.append(Finding("missing_h1", "low", {"h1_count": 0},
                         "Přidat jeden hlavní nadpis <h1> s tím, co firma nabízí."))
    if not data["lang"].strip():
        F.append(Finding("missing_lang", "low", {"html_lang": "missing"},
                         "Doplnit <html lang=\"cs\"> (a jazyk u dalších jazykových verzí)."))
    # ---- dental/medical niche: booking + languages (only on pages that say they are a practice)
    full_text = data["body_text"] + " " + data["title"]
    dental = _has_kw(full_text, CLINIC_KEYWORDS)
    booking = _has_kw(full_text + " " + " | ".join(l["text"] for l in data["links"]) + " "
                      + " ".join(l["href"] or "" for l in data["links"]), BOOKING_KEYWORDS)
    if dental and not booking and not data["forms"]:
        F.append(Finding("no_online_booking", "medium",
                         {"practice_keywords": dental[:5], "booking_keywords_found": 0, "forms": 0},
                         "Přidat online objednání (formulář nebo rezervační systém) viditelně na úvodní stránku."))
    foreign = _has_kw(full_text, FOREIGN_KEYWORDS)
    langs_now = _language_versions(data, urllib.parse.urlsplit(final).netloc)
    if foreign and len(langs_now) < 2:
        F.append(Finding("single_language_foreign_audience", "medium",
                         {"foreign_audience_phrases": foreign[:5], "language_versions": sorted(langs_now),
                          "html_lang": data["lang"] or "missing"},
                         "Přidat anglickou (případně další) jazykovou verzi s hreflang a přepínačem jazyků."))

    if data["images_without_alt"]:
        F.append(Finding("images_without_alt", "low",
                         {"images_without_alt": len(data["images_without_alt"]), "images_total": data["image_count"],
                          "samples": data["images_without_alt"][:3]},
                         "Doplnit popisky alt k obrázkům."))

    # ---- CTA
    text = data["body_text"]
    tel = [l["raw"] for l in data["links"] if (l["raw"] or "").lower().startswith("tel:")]
    mail = [l["raw"] for l in data["links"] if (l["raw"] or "").lower().startswith("mailto:")]
    cta_texts = " | ".join([l["text"] for l in data["links"]] + data["buttons"])
    cta_hits = _has_kw(cta_texts, CTA_KEYWORDS)
    res.signals["cta"] = {"tel_links": len(tel), "mailto_links": len(mail), "forms": data["forms"],
                          "cta_keywords_in_links_buttons": cta_hits}
    if not tel and not mail and not data["forms"] and not cta_hits:
        F.append(Finding("no_visible_cta", "high",
                         {"tel_links": 0, "mailto_links": 0, "forms": 0, "cta_keyword_matches": 0,
                          "keywords_checked": len(CTA_KEYWORDS)},
                         "Přidat viditelné tlačítko „Zavolat“ / „Objednat“ / kontaktní formulář do první obrazovky."))

    # ---- copyright
    cy = _copyright_year(text)
    if cy and year - cy[0] > COPYRIGHT_MAX_AGE_YEARS:
        F.append(Finding("outdated_copyright", "low",
                         {"copyright_text": cy[1], "year": cy[0], "current_year": year},
                         "Aktualizovat patičku a obsah (akce, ceny, rok)."))

    # ---- console errors
    if console_errors:
        F.append(Finding("console_errors", "low", {"count": len(console_errors), "samples": console_errors[:3]},
                         "Opravit chyby JavaScriptu – mohou rozbíjet části stránky."))

    # ---- links (same site, sequential, robots-aware, capped)
    origin_host = urllib.parse.urlsplit(final).netloc
    internal: list[str] = []
    for l in data["links"]:
        href = l["href"] or ""
        p = urllib.parse.urlsplit(href)
        if p.scheme not in ("http", "https") or p.netloc != origin_host:
            continue
        clean = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))
        if clean not in internal:
            internal.append(clean)
    res.signals["internal_pages"] = len(internal)
    link_requests = 0
    if check_links:
        broken, skipped_robots, checked = [], [], 0
        for link in internal[:link_cap]:
            if link.rstrip("/") == final.split("#")[0].rstrip("/"):
                continue
            if not robots.allowed(link):
                skipped_robots.append(link)
                continue
            if checked:
                time.sleep(polite_delay_s)
            st = _check_link(link, LINK_TIMEOUT_S)
            checked += 1
            link_requests += 1
            if not isinstance(st, int) or st >= 400:
                broken.append({"url": link, "status": st})
        res.metrics["links"] = {"internal_found": len(internal), "checked": checked,
                                "cap": link_cap, "skipped_by_robots": skipped_robots}
        if broken:
            F.append(Finding("broken_links", "high" if len(broken) >= 3 else "medium",
                             {"broken": broken[:10], "count": len(broken), "checked": checked},
                             "Opravit nebo odstranit nefunkční odkazy (a nastavit přesměrování 301)."))
    else:
        res.skipped_checks.append({"check": "broken_links", "reason": "disabled by caller"})

    # ---- business signals used for the offer tier (measured, not guessed)
    sig_text = text + " " + data["title"] + " " + cta_texts
    res.signals.update({
        "language_versions": sorted(_language_versions(data, origin_host)),
        "clinic_keywords": _has_kw(sig_text, CLINIC_KEYWORDS),
        "clinic_scale_keywords": _has_kw(sig_text, CLINIC_SCALE_KEYWORDS),
        "foreign_audience_keywords": _has_kw(sig_text, FOREIGN_KEYWORDS),
        "booking_keywords": _has_kw(sig_text, BOOKING_KEYWORDS),
        "eshop_keywords": _has_kw(sig_text, ESHOP_KEYWORDS),
    })
    res.requests_made = page_requests + link_requests
    return res


def save_result(res: AuditResult, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Evidence-only website audit (one URL).")
    ap.add_argument("url")
    ap.add_argument("--out", required=True, help="directory for screenshots + audit.json (outside git)")
    ap.add_argument("--no-links", action="store_true")
    a = ap.parse_args(argv)
    res = audit_url(a.url, a.out, check_links=not a.no_links)
    save_result(res, Path(a.out) / "audit.json")
    print(json.dumps({"status": res.status, "findings": [f.id for f in res.findings]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
