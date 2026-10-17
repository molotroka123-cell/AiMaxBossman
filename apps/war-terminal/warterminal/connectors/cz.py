"""Czech national public procurement: ISVZ "Nová Open Data" (Registr veřejných zakázek, MMR ČR), no key.

Official bulk export, one ZIP per month and category, published around the 5th of the following month:
  https://isvz.nipez.cz/opendata/nova  ->  https://isvz.nipez.cz/sites/default/files/content/opendata-rvz/VZ-MM-YYYY.zip
JSON format documented by MMR (swagger.json inside dokumentace_<ver>.zip on
https://isvz.nipez.cz/centrum-podpory/napoveda/webovy-portal-isvz/open-data/nova-open-data-dokumentace-json-formatu).
Data come from Věstník veřejných zakázek, NEN, Tender arena and TenderMarket. The newest file lags 5-35 days, so
statuses/deadlines are "as of the end of that month" (obdobi_do), not live. One monthly VZ file is ~180 MB ZIP /
~1.3 GB JSON: it is parsed as a stream and only the selected records are kept.
Terms: published as open data ("otevřená data", zákon 106/1999 Sb. §3 odst. 11: reuse not restricted); no separate
licence text on the portal; curator ospevz@mmr.gov.cz. NOTE: isvz.nipez.cz/robots.txt says "Disallow: /" for all
agents. This connector downloads only the documented dataset files on an explicit run (no crawling, no HTML).
"""
from __future__ import annotations

import hashlib
import heapq
import io
import json
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from ..store import sha
from .ted import category_of

PAGE = "https://isvz.nipez.cz/opendata/nova"
FILE_URL = "https://isvz.nipez.cz/sites/default/files/content/opendata-rvz/VZ-{m:02d}-{y}.zip"
NEN_URL = "https://nen.nipez.cz/verejne-zakazky/detail-zakazky/{}"
VVZ_URL = "https://vvz.nipez.cz/formulare-zakazky/{}"           # route used by the VVZ site for "Evidenční číslo zakázky"
LICENCE = "ISVZ Open Data (MMR ČR), otevřená data dle zákona 106/1999 Sb. §3 odst. 11; robots.txt isvz.nipez.cz: Disallow / (подтвердить у ospevz@mmr.gov.cz)"

OPEN_STATES = {"Aktivní/Neukončen"}
CLOSED_STATES = {"Dokončen/Zadán", "Ukončeno plnění smlouvy", "Neúspěšný"}
CANCELLED_STATES = {"Zrušen"}
SUBMISSION = ("Lhůta pro podání nabídky", "Lhůta pro podání žádosti o účast", "Lhůta pro podání předběžné nabídky")
# bulky sub-trees (bids, documents, results) are dropped from kept records: not needed for a tender card
DROP_ZP = ("ucastnici", "dokumenty", "pravidla_pro_hodnoceni", "vysledek", "podane_nabidky_nebo_zadosti_o_ucast",
           "informace_o_otevirani_podani", "hodnoty_podanych_nabidek", "ucastnici_jimz_zanikla_ucast_v_zadavacim_postupu")
_DEC = json.JSONDecoder()


def _day(s) -> str | None:
    s = str(s or "")[:10]
    return s if len(s) == 10 and s[4] == "-" else None


def _num(v) -> Decimal | None:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


def iter_records(stream):
    """Yield items of the top-level "data" array from a binary JSON stream, one at a time; also yields the header once."""
    r = io.TextIOWrapper(stream, encoding="utf-8")
    buf = ""
    while '"data":[' not in buf and '"data": [' not in buf:
        chunk = r.read(65536)
        if not chunk:
            raise ValueError("ISVZ open data: no 'data' array")
        buf += chunk
    key = '"data":[' if '"data":[' in buf else '"data": ['
    head = buf[:buf.index(key)].rstrip(", \r\n\t") + "}"
    try:
        yield {"__header__": json.loads(head)}
    except json.JSONDecodeError:
        yield {"__header__": {}}
    buf = buf[buf.index(key) + len(key):]
    eof = False
    while True:
        buf = buf.lstrip(" \r\n\t,")
        if buf.startswith("]"):
            return
        try:
            obj, end = _DEC.raw_decode(buf)
        except json.JSONDecodeError:
            if eof:
                raise
            chunk = r.read(1 << 20)
            eof = not chunk
            buf += chunk
            continue
        yield obj
        buf = buf[end:]


def _parts(rec: dict) -> list[dict]:
    return (rec.get("verejna_zakazka") or {}).get("casti_verejne_zakazky") or []


def _zp(part: dict) -> dict:
    return part.get("zadavaci_postup_pro_cast") or {}


def _deadline_day(rec: dict) -> str | None:
    days = []
    for kind in SUBMISSION:                                 # offer deadline first, request-to-participate as fallback
        days = [d for p in _parts(rec) for l in (_zp(p).get("lhuty") or []) if l.get("druh_lhuty") == kind
                for d in [_day(l.get("datum_a_cas_konce_lhuty"))] if d]
        if days:
            break
    return max(days) if days else None


def _published_day(rec: dict) -> str | None:
    days = [d for p in _parts(rec) for d in [_day(_zp(p).get("datum_zahajeni_zadavaciho_postupu"))] if d]
    return min(days) if days else None


def _status(rec: dict, deadline_day: str | None, today: date) -> str:
    states = {_zp(p).get("stav") for p in _parts(rec)} - {None}
    if states & OPEN_STATES:
        if deadline_day:
            return "open" if date.fromisoformat(deadline_day) >= today else "closed"
        return "unknown"                                    # active procedure without a published submission deadline
    if states and states <= CANCELLED_STATES:
        return "cancelled"
    if states and states <= CLOSED_STATES | CANCELLED_STATES:
        return "closed"
    return "unknown"


def _value(vz: dict) -> tuple[Decimal | None, str | None, str | None]:
    v = _num(vz.get("predpokladana_hodnota_bez_DPH"))
    if v is not None:
        return v, vz.get("predpokladana_hodnota_bez_DPH_mena") or None, "estimated value excl. VAT"
    vals = [(_num(p.get("predpokladana_hodnota_casti_bez_DPH")), p.get("predpokladana_hodnota_casti_bez_DPH_mena")) for p in _parts({"verejna_zakazka": vz})]
    vals = [(x, c) for x, c in vals if x is not None]
    if vals and len({c for _, c in vals}) == 1:
        return sum(x for x, _ in vals), vals[0][1] or None, "sum of lot estimates excl. VAT"
    return None, None, None


def _vvz_number(vz: dict) -> str | None:
    for zp in vz.get("zadavaci_postupy") or []:
        if zp.get("evidencni_cislo_zadavaciho_postupu_ve_vestniku_verejnych_zakazek"):
            return str(zp["evidencni_cislo_zadavaciho_postupu_ve_vestniku_verejnych_zakazek"])
    return next((str(t["identifikator"]) for t in vz.get("identifikatory_v_elektronickem_nastroji") or []
                 if t.get("kod_nastroje") == "VVZ" and t.get("identifikator")), None)


def _source_url(vz: dict) -> str | None:
    """Official notice page: NEN detail, else the VVZ procedure page, else the buyer profile on a certified e-tool."""
    for t in vz.get("identifikatory_v_elektronickem_nastroji") or []:
        if t.get("kod_nastroje") == "NEN" and t.get("identifikator"):
            return NEN_URL.format(str(t["identifikator"]).replace("/", "-"))
    vvz = _vvz_number(vz)
    if vvz:
        return VVZ_URL.format(vvz)
    for p in vz.get("casti_verejne_zakazky") or []:
        u = _zp(p).get("odkaz_na_profil")
        if u and str(u).startswith("https://"):
            return str(u)                                    # buyer profile on a certified e-tool (Tender arena, ...)
    return None


def normalize(rec: dict, retrieved_at: str, snapshot_sha: str, today: date | None = None, file_url: str = PAGE, header: dict | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    vz = rec.get("verejna_zakazka") or {}
    nid = str(vz.get("identifikator_NIPEZ") or "")
    deadline_day = _deadline_day(rec)
    val, cur, basis = _value(vz)
    pred = vz.get("predmet") or {}
    main = [pred.get("hlavni_kod_CPV"), *((p.get("predmet") or {}).get("hlavni_kod_CPV") for p in _parts(rec))]
    cpv_main = sorted({str(c) for c in main if c})
    cpv = cpv_main + sorted({str(c) for c in (pred.get("vedlejsi_kod_CPV") or []) if c} - set(cpv_main))
    buyers = [(z.get("subjekt") or {}).get("nazev_subjektu") for zp in vz.get("zadavaci_postupy") or []
              for z in ((zp.get("zadavatel_zadavaciho_postupu") or {}).get("zadavatele") or [])]
    buyer = next((b for b in buyers if b), "")
    vvz_no = _vvz_number(vz)
    zdroj = str(rec.get("zdroj_dat") or "")
    url = _source_url(vz)
    header = header or {}
    quality = "ok" if (val is not None and deadline_day and url) else "partial"
    return {
        "id": f"cz:{nid}", "official_notice_id": nid, "title": str(vz.get("nazev_verejne_zakazky") or nid), "jurisdiction": "CZ", "country": "CZ",
        "category": category_of(cpv), "status": _status(rec, deadline_day, today),
        "deadline": f"{deadline_day}T23:59:59Z" if deadline_day else None,
        "estimated_value": float(val) if val is not None else None, "currency": cur, "value_basis": basis,
        "source_url": url or file_url, "source_id": "cz_nen" if zdroj == "Národní elektronický nástroj" else "cz_vvz",
        "retrieved_at": retrieved_at, "published_at": _published_day(rec),
        "source_version": f"ISVZ RVZ open data {header.get('verze', '?')} {(header.get('obdobi_do') or '')[:10]}; zdroj_dat={zdroj}; vvz={vvz_no or '-'}",
        "checksum": sha(json.dumps(rec, sort_keys=True, ensure_ascii=False)), "data_quality": quality,
        "buyer": buyer, "cpv": cpv, "demo": False, "snapshot_sha256": snapshot_sha,
    }


def _prune(rec: dict) -> dict:
    rec = {k: v for k, v in rec.items() if k != "historie_lhut"}
    vz = dict(rec.get("verejna_zakazka") or {})
    vz["casti_verejne_zakazky"] = [{**p, "zadavaci_postup_pro_cast": {k: v for k, v in _zp(p).items() if k not in DROP_ZP}} for p in _parts(rec)]
    rec["verejna_zakazka"] = vz
    return rec


def months_for(days: int, today: date) -> list[tuple[int, int]]:
    """Months touched by [today-days, today], newest first, plus one earlier month as fallback (file appears ~5th)."""
    since = today - timedelta(days=days)
    out, y, m = [], today.year, today.month
    while (y, m) >= (since.year, since.month):
        out.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    out.append((y, m))
    return out


def select(stream, *, since: date, today: date, limit: int, heap: list, seen: set) -> dict:
    """Stream one monthly JSON, keep the best `limit` records: open first, then procedures started since `since`."""
    header, total = {}, 0
    for rec in iter_records(stream):
        if "__header__" in rec:
            header = rec["__header__"]
            continue
        total += 1
        nid = (rec.get("verejna_zakazka") or {}).get("identifikator_NIPEZ")
        if not nid or nid in seen:
            continue
        dl, pub = _deadline_day(rec), _published_day(rec)
        is_open = _status(rec, dl, today) == "open"
        if not is_open and not (pub and pub >= since.isoformat()):
            continue
        seen.add(nid)
        item = ((1 if is_open else 0, pub or "", nid), _prune(rec))
        if len(heap) < limit:
            heapq.heappush(heap, item)
        elif item[0] > heap[0][0]:
            heapq.heapreplace(heap, item)
    return {**{k: header.get(k) for k in ("verze", "obdobi_od", "obdobi_do")}, "records_in_file": total}


def fetch(get, *, days: int = 30, limit: int = 100, today: date | None = None):
    """get(url) -> bytes (ZIP). Returns (request_description, raw) where raw keeps only the selected records."""
    today = today or date.today()
    since = today - timedelta(days=days)
    heap, seen, files, misses = [], set(), [], []
    months = months_for(days, today)
    for i, (y, m) in enumerate(months):
        if i == len(months) - 1 and files:
            break                                           # fallback month only when nothing in the window is published yet
        url = FILE_URL.format(m=m, y=y)
        try:
            blob = get(url)
        except Exception as e:                              # noqa: BLE001  not published yet (404) or network error
            misses.append({"url": url, "error": type(e).__name__})
            continue
        if not blob or blob[:2] != b"PK":
            misses.append({"url": url, "error": "not a zip"})
            continue
        z = zipfile.ZipFile(io.BytesIO(blob))
        name = next(n for n in z.namelist() if n.lower().endswith(".json"))
        meta = select(z.open(name), since=since, today=today, limit=limit, heap=heap, seen=seen)
        files.append({"url": url, "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob), **meta})
    picked = [rec for _, rec in sorted(heap, key=lambda kv: kv[0], reverse=True)]
    req = {"page": PAGE, "files": [f["url"] for f in files], "missing": misses, "since": since.isoformat(), "today": today.isoformat(), "limit": limit}
    return req, {"source": "isvz_rvz_opendata", "licence": LICENCE, "files": files, "data": picked}


def normalize_all(raw: dict, retrieved_at: str, snapshot_sha: str, today: date | None = None) -> list[dict]:
    files = raw.get("files") or [{}]
    header = files[0]
    url = header.get("url") or PAGE
    return [normalize(r, retrieved_at, snapshot_sha, today, url, header) for r in raw.get("data", [])]
