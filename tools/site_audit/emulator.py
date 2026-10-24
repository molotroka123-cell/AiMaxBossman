"""Training EMULATOR: synthetic Czech dental-practice websites with KNOWN injected defects.

Everything here is SIMULATED: fictional clinics (copy cached in tests/fixtures/site_audit/dental_texts.json,
or built-in templates), served on 127.0.0.1 only. The audit is run on each site and scored against the
ground truth (precision/recall per finding type, false positives on clean controls, offer-tier accuracy).
A deterministic "clinic owner reaction" feeds a SIMULATED pipeline value (real_money = 0), written to an
append-only ledger in the caller's data dir -- same conventions as command-center/bcc/earning_emulator.py.
"""
from __future__ import annotations

import datetime as _dt
import html
import itertools
import json
import re
import struct
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import audit as A
from . import crm, offer

TEXTS_FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "site_audit" / "dental_texts.json"
SIMULATED = True

# defect -> finding ids that MUST appear
EXPECTED = {
    "no_viewport": {"no_viewport_meta"},
    "mobile_overflow": {"mobile_horizontal_overflow"},
    "broken_links": {"broken_links"},
    "no_cta": {"no_visible_cta"},
    "old_copyright": {"outdated_copyright"},
    "missing_meta": {"missing_meta_description"},
    "missing_h1": {"missing_h1"},
    "missing_lang": {"missing_lang"},
    "imgs_no_alt": {"images_without_alt"},
    "slow_heavy": {"heavy_page", "slow_load"},
    "no_online_booking": {"no_online_booking"},
    "cz_only_foreign": {"single_language_foreign_audience"},
}
# real, measurable consequences of a defect (allowed, not required; never counted as fabricated)
CONSEQUENCES = {
    "no_viewport": {"small_text_mobile", "small_tap_targets"},
    "no_cta": {"no_online_booking"},
}
KIND_TIER = {"solo": "landing", "practice": "business", "clinic": "premium", "prague_foreign": "premium"}

SCHEDULE = [
    ("solo", ["no_viewport", "old_copyright"]),
    ("practice", ["mobile_overflow", "broken_links"]),
    ("clinic", ["no_cta", "missing_meta"]),
    ("prague_foreign", ["cz_only_foreign", "imgs_no_alt"]),
    ("solo", ["missing_h1", "missing_lang", "no_online_booking"]),
    ("practice", ["slow_heavy", "old_copyright"]),
    ("clinic", ["no_viewport", "broken_links", "imgs_no_alt"]),
    ("solo", ["mobile_overflow", "no_cta"]),
    ("prague_foreign", ["cz_only_foreign", "missing_meta", "no_online_booking"]),
    ("practice", ["missing_h1", "missing_lang"]),
    ("solo", ["broken_links", "no_online_booking", "missing_meta"]),
    ("clinic", ["slow_heavy", "missing_lang"]),
    ("solo", []),
    ("practice", []),
    ("clinic", []),
    ("prague_foreign", []),
]

_SENSITIVE = (A.BOOKING_KEYWORDS + A.FOREIGN_KEYWORDS + A.CLINIC_SCALE_KEYWORDS + A.ESHOP_KEYWORDS
              + ("©", "copyright", "(c)"))
_TEMPLATES = [
    {"base_name": "Bílý Zub", "city": "Brno", "tagline": "Šetrná péče o zuby celé rodiny.",
     "about": "Pečujeme o zuby dětí i dospělých. Zakládáme si na prevenci a klidném přístupu.",
     "services": ["Preventivní prohlídky", "Bílé výplně", "Dentální hygiena", "Korunky"],
     "prices": [{"service": "Dentální hygiena", "price_czk": 1500}, {"service": "Bílá výplň", "price_czk": 1800}],
     "hours": "Po-Pá 8:00-16:00"},
    {"base_name": "Zubní Pohoda", "city": "Praha", "tagline": "Moderní stomatologie bez stresu.",
     "about": "Používáme digitální rentgen a šetrné postupy. Každému pacientovi vysvětlíme plán léčby.",
     "services": ["Endodoncie", "Implantáty", "Bělení zubů", "Parodontologie"],
     "prices": [{"service": "Bělení zubů", "price_czk": 6500}, {"service": "Implantát", "price_czk": 29000}],
     "hours": "Po-Čt 8:00-18:00, Pá 8:00-13:00"},
]


def load_texts(path: Path = TEXTS_FIXTURE) -> tuple[list[dict], list[str]]:
    """Cached synthetic clinic copy + the provider that produced each record. Falls back to templates."""
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        clinics, providers = [], []
        for b in data.get("batches", []):
            for c in b.get("clinics", []):
                clinics.append(c)
                providers.append(b.get("provider", "unknown"))
        if clinics:
            return clinics, providers
    return list(_TEMPLATES), ["builtin-templates"] * len(_TEMPLATES)


def _scrub(text: str) -> str:
    """Drop sentences that would leak signals the emulator controls itself (booking, languages, scale, years)."""
    keep = []
    for s in re.split(r"(?<=[.!?])\s+", text or ""):
        low = s.lower()
        if any(k in low for k in _SENSITIVE) or re.search(r"\b(19|20)\d{2}\b", s):
            continue
        keep.append(s)
    return " ".join(keep).strip()


@dataclass
class SiteSpec:
    idx: int
    kind: str
    defects: list[str]
    clinic: dict
    provider: str
    files: dict = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return f"s{self.idx:02d}"

    @property
    def name(self) -> str:
        base = self.clinic["base_name"]
        return f"Dentální klinika {base}" if self.kind == "clinic" else f"Zubní ordinace {base}"

    @property
    def expected(self) -> set[str]:
        return set().union(*(EXPECTED[d] for d in self.defects)) if self.defects else set()

    @property
    def allowed(self) -> set[str]:
        return self.expected | set().union(*(CONSEQUENCES.get(d, set()) for d in self.defects)) \
            if self.defects else set()

    @property
    def expected_tier(self) -> str:
        return KIND_TIER[self.kind]


def _bmp(width: int = 1100, height: int = 1000) -> bytes:
    """Valid uncompressed 24-bit BMP (~3.3 MB): a real image, just far too heavy for the web."""
    row = (width * 3 + 3) & ~3
    size = 54 + row * height
    header = b"BM" + struct.pack("<IHHI", size, 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, row * height, 2835, 2835, 0, 0)
    pixel_row = bytes([200, 220, 235]) * width + b"\0" * (row - width * 3)
    return header + info + pixel_row * height


_GIF = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
_CSS = """
body{font-family:system-ui,sans-serif;font-size:17px;line-height:1.5;margin:0;padding:0 16px;color:#1d2b36}
nav a,.btn{display:inline-block;padding:12px 14px;min-height:44px;box-sizing:border-box}
img{max-width:100%;height:auto}
table{border-collapse:collapse}
td{padding:8px}
input,button{font-size:17px;min-height:44px}
footer{font-size:15px;margin:24px 0}
"""


def _page(spec: SiteSpec, title: str, body: str, *, home: bool, year: int) -> str:
    d = set(spec.defects) if home else set()
    lang = "" if "missing_lang" in d else ' lang="cs"'
    vp = "" if "no_viewport" in d else '<meta name="viewport" content="width=device-width, initial-scale=1">'
    desc = "" if "missing_meta" in d else (
        f'<meta name="description" content="{html.escape(spec.name)} – {html.escape(spec.clinic["city"])}.">')
    foot = "© 2016–2019 " if "old_copyright" in d else f"© {year} "
    return (f"<!doctype html><html{lang}><head><meta charset=\"utf-8\">{vp}<title>{html.escape(title)}</title>"
            f"{desc}<link rel=\"icon\" href=\"data:,\"><style>{_CSS}</style></head><body>{body}"
            f"<footer>{foot}{html.escape(spec.name)}</footer></body></html>")


def build_site(spec: SiteSpec, year: int) -> dict:
    """Return {path: (bytes, content_type, delay_s)} for one synthetic site."""
    c, d, p = spec.clinic, set(spec.defects), f"/{spec.slug}"
    esc = html.escape
    sub = {"solo": ["sluzby", "kontakt"], "practice": ["sluzby", "cenik", "tym", "pojistovny", "galerie", "kontakt"],
           "clinic": ["sluzby", "cenik", "kontakt"], "prague_foreign": ["sluzby", "kontakt"]}[spec.kind]
    labels = {"sluzby": "Služby", "cenik": "Ceník", "tym": "Náš tým", "pojistovny": "Pojišťovny",
              "galerie": "Galerie", "kontakt": "Kde nás najdete" if "no_cta" in d else "Kontakt"}
    nav = [f'<a href="{p}/index.html">Úvod</a>'] + [f'<a href="{p}/{s}.html">{labels[s]}</a>' for s in sub]
    if "broken_links" in d:
        nav += [f'<a href="{p}/akce-leto.html">Letní akce</a>', f'<a href="{p}/stary-cenik.html">Starý ceník</a>']
    if spec.kind == "prague_foreign" and "cz_only_foreign" not in d:
        nav += [f'<a href="{p}/index.html" hreflang="cs">CZ</a>', f'<a href="{p}/en/index.html" hreflang="en">EN</a>']
    heading_tag = "div" if "missing_h1" in d else "h1"
    about = _scrub(c.get("about", "")) or "Pečujeme o zuby celé rodiny."
    tagline = _scrub(c.get("tagline", ""))
    services = [s for s in c.get("services", []) if _scrub(s)]
    prices = [x for x in c.get("prices", []) if _scrub(x["service"]) and x.get("price_czk")]
    table_style = ' style="width:1100px"' if "mobile_overflow" in d else ' style="width:100%"'
    rows = "".join(f"<tr><td>{esc(x['service'])}</td><td>{x['price_czk']} Kč</td></tr>" for x in prices)
    alt = "" if "imgs_no_alt" in d else ' alt="Ordinace"'
    imgs = f'<img src="{_GIF}" width="320" height="120"{alt}><img src="{_GIF}" width="320" height="120"{alt}>'
    if "slow_heavy" in d:
        imgs += f'<img src="{p}/foto-ordinace.bmp" width="600" alt="Fotografie ordinace">'
    phone = f"+420 000 000 {spec.idx:03d}"
    if "no_cta" in d:
        cta = f"<p>Ordinační hodiny: {esc(c.get('hours', ''))}</p>"
    elif "no_online_booking" in d:
        cta = (f'<p><a class="btn" href="tel:{phone.replace(" ", "")}">Zavolat {phone}</a></p>'
               f"<p>Ordinační hodiny: {esc(c.get('hours', ''))}</p>")
    else:
        cta = (f'<p><a class="btn" href="tel:{phone.replace(" ", "")}">Zavolat {phone}</a></p>'
               f'<form action="{p}/kontakt.html" method="get"><label for="j">Jméno</label> <input id="j" name="j"> '
               f'<button type="submit">Objednat se online</button></form>')
    foreign = "<p>Ošetřujeme i zahraniční pacienty.</p>" if spec.kind == "prague_foreign" else ""
    body = (f"<nav>{' '.join(nav)}</nav><main><{heading_tag}>{esc(spec.name)}</{heading_tag}>"
            f"<p>{esc(tagline)}</p><p>{esc(about)}</p>{foreign}{imgs}"
            f"<ul>{''.join(f'<li>{esc(s)}</li>' for s in services)}</ul>"
            f"<table{table_style}>{rows}</table>{cta}</main>")
    files = {f"{p}/index.html": (_page(spec, f"{spec.name} – {c['city']}", body, home=True, year=year), "text/html", 0)}
    for s in sub:
        files[f"{p}/{s}.html"] = (_page(spec, f"{labels[s]} – {spec.name}", f"<h1>{labels[s]}</h1>",
                                        home=False, year=year), "text/html", 0)
    if spec.kind == "prague_foreign" and "cz_only_foreign" not in d:
        files[f"{p}/en/index.html"] = (_page(spec, f"{spec.name} – English", "<h1>Dental care in Prague</h1>",
                                             home=False, year=year).replace('lang="cs"', 'lang="en"'), "text/html", 0)
    if "slow_heavy" in d:
        files[f"{p}/foto-ordinace.bmp"] = (_bmp(), "image/bmp", 3.3)
    out = {k: (v[0].encode("utf-8") if isinstance(v[0], str) else v[0], v[1], v[2]) for k, v in files.items()}
    spec.files = out
    return out


def make_specs(texts: list[dict] | None = None, providers: list[str] | None = None) -> list[SiteSpec]:
    if texts is None:
        texts, providers = load_texts()
    providers = providers or ["unknown"] * len(texts)
    prague = [i for i, t in enumerate(texts) if t.get("city") == "Praha"] or list(range(len(texts)))
    other = [i for i in range(len(texts)) if i not in prague] or list(range(len(texts)))
    pick = {"prague": itertools.cycle(prague), "other": itertools.cycle(other)}
    specs = []
    for idx, (kind, defects) in enumerate(SCHEDULE):
        ti = next(pick["prague" if kind == "prague_foreign" else "other"])
        clinic = dict(texts[ti])
        if kind == "prague_foreign":
            clinic["city"] = "Praha"
        specs.append(SiteSpec(idx, kind, list(defects), clinic, providers[ti]))
    return specs


class _Handler(BaseHTTPRequestHandler):
    files: dict = {}

    def log_message(self, *a):  # quiet
        pass

    def _serve(self, body: bool):
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            data, ctype, delay = b"User-agent: *\nAllow: /\n", "text/plain", 0
        elif path in self.files:
            data, ctype, delay = self.files[path]
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", "9")
            self.end_headers()
            if body:
                self.wfile.write(b"not found")
            return
        if body and delay:
            time.sleep(delay)
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if body:
            self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self._serve(True)

    def do_HEAD(self):  # noqa: N802
        self._serve(False)


def serve(specs: list[SiteSpec], year: int) -> tuple[ThreadingHTTPServer, str]:
    files: dict = {}
    for s in specs:
        files.update(build_site(s, year))
    handler = type("EmuHandler", (_Handler,), {"files": files})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def owner_reaction(true_high: int) -> bool:
    """SIMULATED, deterministic: the clinic owner accepts a free mockup iff >=2 real high-severity findings."""
    return true_high >= 2


def score(rows: list[dict]) -> dict:
    types = sorted(set().union(*EXPECTED.values()) | {f for r in rows for f in r["found"]})
    per = {}
    for t in types:
        tp = sum(1 for r in rows if t in r["found"] and t in r["expected"])
        fn = sum(1 for r in rows if t in r["expected"] and t not in r["found"])
        fp = sum(1 for r in rows if t in r["found"] and t not in r["allowed"])
        if tp + fn + fp:
            per[t] = {"tp": tp, "fp": fp, "fn": fn,
                      "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                      "recall": round(tp / (tp + fn), 3) if tp + fn else None}
    tp = sum(v["tp"] for v in per.values())
    fp = sum(v["fp"] for v in per.values())
    fn = sum(v["fn"] for v in per.values())
    clean = [r for r in rows if not r["defects"]]
    return {
        "per_type": per,
        "totals": {"tp": tp, "fp": fp, "fn": fn,
                   "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                   "recall": round(tp / (tp + fn), 3) if tp + fn else None},
        "fabricated_findings": [{"site": r["site"], "id": f} for r in rows for f in r["found"]
                                if f not in r["allowed"]],
        "clean_controls": len(clean),
        "clean_control_false_positives": sum(len(r["found"]) for r in clean),
        "tier_accuracy": round(sum(r["tier"] == r["expected_tier"] for r in rows) / len(rows), 3) if rows else None,
    }


def run(data_dir: str | Path, *, year: int | None = None, specs: list[SiteSpec] | None = None) -> dict:
    year = year or _dt.date.today().year
    data_dir = Path(data_dir)
    specs = specs if specs is not None else make_specs()
    srv, base = serve(specs, year)
    con = crm.connect(data_dir)
    ledger = data_dir / "site_audit_simulated_ledger.jsonl"
    rows: list[dict] = []
    try:
        for s in specs:  # strictly one browser at a time
            url = f"{base}/{s.slug}/index.html"
            t0 = time.time()
            res = A.audit_url(url, data_dir / "shots" / s.slug, current_year=year, polite_delay_s=0.0)
            d = res.to_dict()
            found = {f["id"] for f in d["findings"]}
            tier, _ = offer.choose_tier(d["signals"])
            true_high = sum(1 for f in d["findings"] if f["severity"] == "high" and f["id"] in s.expected)
            accepted = owner_reaction(true_high)
            lead = crm.upsert_lead(con, company_name=s.name, website=url, source="emulator (SIMULATED)",
                                   category="zubní ordinace", city=s.clinic.get("city"), status="simulated",
                                   notes="SIMULATED emulator site, not a real business")
            crm.record_audit(con, lead, d, tier=tier, lead_status="simulated")
            card_path = None
            if d["findings"]:
                pj, _pm = offer.write_offer_card(offer.build_offer_card(d, s.name), data_dir / "offer_cards")
                crm.add_offer_card(con, lead, pj)
                card_path = str(pj)
            row = {"site": s.slug, "kind": s.kind, "defects": s.defects, "text_provider": s.provider,
                   "expected": sorted(s.expected), "allowed": sorted(s.allowed), "found": sorted(found),
                   "tier": tier, "expected_tier": s.expected_tier, "true_high": true_high,
                   "accepted_free_mockup": accepted, "seconds": round(time.time() - t0, 1),
                   "status": res.status, "offer_card": card_path}
            rows.append(row)
            rec = {"SIMULATED": True, "real_money": 0, "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(
                timespec="seconds"), "site": s.slug, "tier": tier, "price_czk": offer.TIERS[tier]["price_czk"],
                "accepted_free_mockup": accepted,
                "simulated_pipeline_czk": offer.TIERS[tier]["price_czk"] if accepted else 0}
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8") as fh:  # append-only
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    finally:
        srv.shutdown()
        con.close()
    sc = score(rows)
    accepted = [r for r in rows if r["accepted_free_mockup"]]
    sc["simulated_pipeline"] = {
        "SIMULATED": True, "real_money": 0, "label": "SIMULATED pipeline value — not revenue",
        "sites": len(rows), "accepted_free_mockup": len(accepted),
        "pipeline_czk_if_all_accepted_buy": sum(offer.TIERS[r["tier"]]["price_czk"] for r in accepted),
        "rule": "clinic owner accepts free mockup iff >=2 real high-severity findings", "ledger": str(ledger)}
    sc["rows"] = rows
    return sc
