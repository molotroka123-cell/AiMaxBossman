"""Contacts (companies met at events) and tender <-> company matching, re-ranked by the owner's 👍/👎.

Matching is deterministic and explainable (no model):
  * CPV: a tender code INSIDE a company CPV prefix = full overlap (1.0); a BROADER tender code (its significant prefix is
    a prefix of the company's) = partial overlap (depth ratio squared: 35 vs 35613 -> 0.16). Significant prefix = code
    without the check digit and without trailing zeros (35613000-4 -> 35613).
  * keywords (en/uk/cs/ru): whole word on accent-folded text (rules.fold, the sanctions-screening normalizer) with the
    Prozorro relevance construction (?<!\\w)word(?!\\w); a trailing * marks a word beginning for Slavic endings
    («радар*» matches «радари»). The buyer name is never used (as in prozorro.relevance: a unit buying bread is not
    a defence tender).
  base score = 60 x best CPV overlap + 15 per distinct keyword found (at most 40), 0..100.

Learning is ranking from feedback, NOT model training: each 👍/👎 on a (tender, company) pair stores that pair's
features at vote time (cpv:<company prefix>, kw:<keyword>, src:<tender source>, bc:<buyer country>). The latest vote
of each pair is its label; weight of a feature = ln((up + 1) / (down + 1)) (Laplace-smoothed log-odds), recomputed
after every vote. learned score = base + 10 x sum of the pair's feature weights. A vote never creates a match: pairs
without any CPV or keyword overlap are not listed.

Privacy: a person's name/phone exist only when the owner types them; they never reach the audit log, the pitch, the
Bossman lesson or any export, and a viewer token does not see them.
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import date, datetime, timezone

from . import rules
from .ai import redact

STATUSES = ("new", "contacted", "meeting", "partner", "declined")
VOTES = ("up", "down")
KW_LANGS = ("en", "uk", "cs", "ru")
PERSON_FIELDS = ("person_name", "person_phone")
FIELDS = ("company", "country", "website", "public_email", "public_email_source", *PERSON_FIELDS, "products", "cpv_prefixes",
          "keywords", "event", "source", "notes", "status", "next_step", "next_step_date")

CPV_POINTS, KW_POINTS, KW_CAP, LEARN_POINTS = 60, 15, 40, 10
K = 5                      # precision@K
MIN_LABELS = 20            # below this the metric is INSUFFICIENT_EVIDENCE
TEST_BUCKETS = 5           # sha256(tender|contact) % 5 == 0 -> held-out test set (20 %)
TEXT_FIELDS = ("title", "title_en", "title_uk", "description", "description_en")

MILITARY_BADGE = "военный материал — нужна лицензия MPO (CZ)"
DUAL_USE_BADGE = "двойное назначение — проверить 2021/821"
# CPV 2008 (verified against the CPV 2008 table): 35613 UAV, 34711200 non-piloted aircraft, 3572 ISTAR/radar,
# 3573 electronic warfare, 38115 radar apparatus. (38113 is SONAR, not radar.)
DUAL_USE_CPV = ("35613", "347112", "3572", "3573", "38115")
DUAL_USE_WORDS = ("drone*", "uav*", "uas", "unmanned", "radar*", "electronic warfare", "jammer*", "counter uas", "c uas",
                  "dron*", "bezpilot*", "radiolokator*", "rusic*", "дрон*", "бпла", "безпілотн*", "беспилотн*", "радар*", "рлс",
                  "реб", "рэб", "радіолокац*", "радиолокац*")
CPV_RX = re.compile(r"^\d{2,8}(-\d)?$")
EMAIL_RX = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
PHONE_RX = re.compile(r"^\+?[\d\s().-]{5,40}$")
WEB_RX = re.compile(r"^(https?://)?[\w-]+(\.[\w-]+)+(/\S*)?$", re.I)
SLUG_RX = re.compile(r"[^a-z0-9]+")


class ContactError(ValueError):
    def __init__(self, fields: list[str], messages: list[str]):
        super().__init__("; ".join(messages))
        self.fields, self.messages = fields, messages


# ------------------------------------------------------------------ text + keywords
def flat(text: str) -> str:
    """Accent-folded, case-folded, every run of non-word characters -> one space (counter-drone == counter drone)."""
    return " ".join(t for t in re.split(r"[\W_]+", rules.fold(text or "")) if t)


def keyword_rx(kw: str) -> re.Pattern | None:
    """Whole-word phrase regex on flat() text; «word*» = word beginning. None when nothing word-like remains."""
    words: list[tuple[str, bool]] = []
    for tok in str(kw or "").split():
        star = tok.endswith("*")
        core = flat(tok.rstrip("*")).split()
        if not core:
            continue
        words += [(w, False) for w in core[:-1]] + [(core[-1], star)]
    if not words:
        return None
    last = len(words) - 1
    parts = [re.escape(w) + ((r"\w*" if i < last else "") if star else r"(?!\w)") for i, (w, star) in enumerate(words)]
    return re.compile(r"(?<!\w)" + " ".join(parts))


_DUAL_RX = [keyword_rx(w) for w in DUAL_USE_WORDS]


def sig(code) -> str:
    """Significant CPV prefix: 35613000-4 -> 35613, 35000000 -> 35, 30000000 -> 30 (division kept)."""
    c = str(code or "").split("-")[0].strip()
    s = c.rstrip("0")
    return s if len(s) >= 2 else c[:2]


# ------------------------------------------------------------------ validation (API and seed share it)
def _blank(v):
    return None if v is None or (isinstance(v, str) and not v.strip()) else (v.strip() if isinstance(v, str) else v)


def clean(doc: dict) -> dict:
    """Normalize one contact: country -> ISO2, CPV without check digit, keywords validated per language, e-mail only with
    the public material it was printed on, dates real. Raises ContactError with the bad field names."""
    bad: list[str] = []
    msg: list[str] = []
    out = {k: _blank(doc.get(k)) for k in FIELDS if k not in ("cpv_prefixes", "keywords")}
    if not out.get("company") or len(out["company"]) < 2:
        bad.append("company")
        msg.append("company: название компании, не короче 2 символов")
    if out.get("country"):
        c2 = rules.country2(out["country"])
        if not c2:
            bad.append("country")
            msg.append(f"country: страна не распознана ({out['country']})")
        out["country"] = c2
    if out.get("website") and not WEB_RX.match(out["website"]):
        bad.append("website")
        msg.append("website: адрес сайта вида www.example.cz или https://...")
    if out.get("public_email"):
        if not EMAIL_RX.match(out["public_email"]):
            bad.append("public_email")
            msg.append("public_email: неверный адрес")
        if not out.get("public_email_source"):
            bad.append("public_email_source")
            msg.append("public_email_source: e-mail сохраняется только с указанием, где он напечатан (листовка, сайт, стенд)")
    else:
        out["public_email_source"] = None
    if out.get("person_phone") and not PHONE_RX.match(out["person_phone"]):
        bad.append("person_phone")
        msg.append("person_phone: цифры, пробел, + ( ) - .")
    cpv = []
    for c in doc.get("cpv_prefixes") or []:
        c = str(c).strip()
        if not CPV_RX.match(c):
            bad.append("cpv_prefixes")
            msg.append(f"cpv_prefixes: «{c}» — код CPV из 2–8 цифр")
            continue
        c = c.split("-")[0]
        if c not in cpv:
            cpv.append(c)
    out["cpv_prefixes"] = cpv
    kws: dict[str, list[str]] = {}
    for lang, words in (doc.get("keywords") or {}).items():
        if lang not in KW_LANGS:
            bad.append("keywords")
            msg.append(f"keywords: язык {lang} не поддерживается ({', '.join(KW_LANGS)})")
            continue
        keep: list[str] = []
        for w in words or []:
            w = " ".join(str(w).split())
            stems_ok = all(len(flat(t.rstrip("*"))) >= 3 for t in w.split() if t.endswith("*"))
            if not w:
                continue
            if keyword_rx(w) is None or len(w) > 60 or not stems_ok:
                bad.append("keywords")
                msg.append(f"keywords.{lang}: «{w}» — слово/фраза до 60 символов; основа со * не короче 3 букв")
                continue
            if w not in keep:
                keep.append(w)
        if keep:
            kws[lang] = keep
    out["keywords"] = kws
    if (out.get("status") or "new") not in STATUSES:
        bad.append("status")
        msg.append(f"status: одно из {', '.join(STATUSES)}")
    out["status"] = out.get("status") or "new"
    if out.get("next_step_date"):
        try:
            date.fromisoformat(out["next_step_date"])
        except ValueError:
            bad.append("next_step_date")
            msg.append("next_step_date: дата ГГГГ-ММ-ДД")
    out["products"] = out.get("products") or ""
    out["notes"] = out.get("notes") or ""
    if bad:
        raise ContactError(sorted(set(bad)), msg)
    return out


def slug(company: str, prefix: str) -> str:
    return prefix + ":" + SLUG_RX.sub("-", flat(company)).strip("-")


def public_view(contact: dict, can_see_person: bool) -> dict:
    """What a role may read: a viewer never receives the person's name/phone, only whether they exist."""
    out = {k: v for k, v in contact.items() if can_see_person or k not in PERSON_FIELDS}
    out["has_person"] = bool(contact.get("person_name") or contact.get("person_phone"))
    return out


# ------------------------------------------------------------------ matching
class Profile:
    """One company compiled for matching."""

    def __init__(self, contact: dict) -> None:
        self.contact = contact
        self.prefixes: list[str] = []
        for p in contact.get("cpv_prefixes") or []:
            s = sig(p)
            if s not in self.prefixes:
                self.prefixes.append(s)
        self.keywords: list[tuple[str, str, re.Pattern]] = []
        for lang in KW_LANGS:
            for w in (contact.get("keywords") or {}).get(lang) or []:
                rx = keyword_rx(w)
                if rx is not None:
                    self.keywords.append((lang, w, rx))


def tender_text(t: dict) -> str:
    return flat(" \n ".join(str(t.get(k)) for k in TEXT_FIELDS if t.get(k)))


def buyer_country(t: dict) -> str:
    return rules.country2(t.get("country")) or rules.country2(t.get("jurisdiction")) or "?"


def cpv_hits(tender_cpv: list[str], prefixes: list[str]) -> list[dict]:
    """Best overlap per company prefix: inside (tender code within the prefix) = 1.0; broader = depth ratio squared."""
    hits = []
    for p in prefixes:
        best = None
        for code in tender_cpv or []:
            t = sig(code)
            if t.startswith(p):
                h = {"company_prefix": p, "tender_cpv": str(code), "kind": "inside", "strength": 1.0}
            elif p.startswith(t):                        # squared: a bare division (35 = all security/defence) is a weak hint
                h = {"company_prefix": p, "tender_cpv": str(code), "kind": "broader", "strength": round((len(t) / len(p)) ** 2, 3)}
            else:
                continue
            if best is None or h["strength"] > best["strength"]:
                best = h
        if best:
            hits.append(best)
    return hits


def match(t: dict, prof: Profile, text: str | None = None) -> dict:
    """Base score, why (CPV + keywords), learning features. base_score 0 = no match."""
    text = tender_text(t) if text is None else text
    hits = cpv_hits(t.get("cpv") or [], prof.prefixes)
    spans = []
    for order, (lang, w, rx) in enumerate(prof.keywords):
        m = rx.search(text)
        if m:
            end = m.end()
            while end < len(text) and text[end] != " ":   # report the whole word: «jammer*» -> «jammers»
                end += 1
            spans.append((m.start(), end, order, lang, w))
    taken: list[tuple[int, int]] = []
    found: list[dict] = []
    # overlapping hits count once, longest phrase first: «drone*»/«dron*» on one word, or «ground surveillance radar*»
    # vs «radar*» inside it, are ONE keyword hit
    for start, end, order, lang, w in sorted(spans, key=lambda s: (-(s[1] - s[0]), s[2])):
        if any(start < e and s < end for s, e in taken):
            continue
        taken.append((start, end))
        found.append({"keyword": w, "lang": lang, "found": text[start:end], "_at": start})
    found.sort(key=lambda f: f.pop("_at"))
    cpv_pts = CPV_POINTS * max((h["strength"] for h in hits), default=0.0)
    kw_pts = min(KW_CAP, KW_POINTS * len(found))
    base = round(cpv_pts + kw_pts, 1)
    reasons = []
    for h in hits:
        if h["kind"] == "inside":
            reasons.append(f"CPV тендера {h['tender_cpv']} входит в профиль компании {h['company_prefix']}*")
        else:
            reasons.append(f"CPV тендера {h['tender_cpv']} шире профиля {h['company_prefix']}* (частичное совпадение {h['strength']:.0%})")
    for f in found:
        reasons.append(f"ключевое слово «{f['keyword']}» ({f['lang']}) найдено в тексте тендера: «{f['found']}»")
    features = ([f"cpv:{h['company_prefix']}" for h in hits] + [f"kw:{flat(f['keyword'])}{'*' if f['keyword'].endswith('*') else ''}" for f in found]
                + [f"src:{t.get('source_id') or t.get('source') or '?'}", f"bc:{buyer_country(t)}"])
    return {"base_score": base, "cpv": hits, "keywords": found, "reasons": reasons, "features": features,
            "matched": bool(hits or found)}


# ------------------------------------------------------------------ compliance badges (every match carries them)
def assess(t: dict, prof: Profile, m: dict, sanctions) -> tuple[list[dict], list[str]]:
    """(badges, block reasons). Blocks: restricted jurisdiction (seed list) or an exact sanctions hit on buyer or company."""
    badges, blocks = [], []
    c = prof.contact
    matched = [h["company_prefix"] for h in m["cpv"]]
    tender_sigs = [sig(x) for x in t.get("cpv") or []]
    if rules.is_controlled(t) or any(p.startswith(rules.CONTROLLED_CPV_PREFIXES) for p in matched):
        badges.append({"id": "military_licence", "level": "REVIEW_REQUIRED", "text": MILITARY_BADGE,
                       "why": "CPV 353–357 или военная номенклатура в названии (та же проверка, что controlled_goods)"})
    text = tender_text(t)
    if any(s.startswith(DUAL_USE_CPV) for s in tender_sigs + matched) or any(rx.search(text) for rx in _DUAL_RX):
        badges.append({"id": "dual_use", "level": "REVIEW_REQUIRED", "text": DUAL_USE_BADGE,
                       "why": "БПЛА / радар / РЭБ по CPV или тексту: классификация по Регламенту (ЕС) 2021/821"})
    tc, cc = rules.country2(t.get("country")) or rules.country2(t.get("jurisdiction")), c.get("country")
    if tc in rules.RESTRICTED_SEED:
        blocks.append(f"страна заказчика {tc}: ограничения/эмбарго по seed-списку")
    if cc in rules.RESTRICTED_SEED:
        blocks.append(f"страна компании {cc}: ограничения/эмбарго по seed-списку")
    if not sanctions:
        badges.append({"id": "sanctions_not_loaded", "level": "NOT_CONNECTED",
                       "text": "санкционные списки не загружены — заказчик и компания не проверены", "why": "Источники → «Загрузить санкции»"})
    else:
        for who, name in (("заказчик", t.get("buyer") or ""), ("компания", c.get("company") or "")):
            hit = rules.screen_party(name, sanctions) if name else None
            if hit and hit["exact"]:
                blocks.append(f"{who}: совпадение со списком санкций ({hit['entry']})")
            elif hit:
                badges.append({"id": "sanctions_possible", "level": "REVIEW_REQUIRED",
                               "text": f"{who}: возможное совпадение со списком санкций ({hit['entry']}) — ручная сверка", "why": "screen_party"})
    return badges, blocks


# ------------------------------------------------------------------ learning from votes
def effective_labels(rows: list[dict]) -> dict[tuple[str, int], dict]:
    """Latest vote per (tender, company) pair."""
    out: dict[tuple[str, int], dict] = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        out[(r["tender_key"], int(r["contact_id"]))] = r
    return out


def weights_from(labels) -> dict[str, dict]:
    counts: dict[str, dict] = {}
    for r in labels:
        for f in set(r.get("features") or []):
            c = counts.setdefault(f, {"up": 0, "down": 0})
            c["up" if r["vote"] == "up" else "down"] += 1
    return {f: dict(c, w=round(math.log((c["up"] + 1) / (c["down"] + 1)), 4)) for f, c in sorted(counts.items())}


def adjustment(features: list[str], weights: dict[str, dict]) -> tuple[float, list[dict]]:
    contrib = [{"feature": f, "weight": weights[f]["w"], "points": round(LEARN_POINTS * weights[f]["w"], 1),
                "up": weights[f]["up"], "down": weights[f]["down"]} for f in features if f in weights and weights[f]["w"]]
    return round(sum(c["points"] for c in contrib), 1), contrib


def is_test(tender_key: str, contact_id: int) -> bool:
    return int(hashlib.sha256(f"{tender_key}|{contact_id}".encode("utf-8")).hexdigest(), 16) % TEST_BUCKETS == 0


def precision_report(rows: list[dict]) -> dict:
    """precision@5 on a held-out 20 % of the labels: learned ranking (weights from the other 80 %) vs the unweighted base
    score. Queries = companies; per company the test pairs are ranked and the share of 👍 in the top min(5, n) is taken
    (macro average). Below MIN_LABELS labels, or with no test pair, the answer is INSUFFICIENT_EVIDENCE."""
    labels = effective_labels(rows)
    test = {k: r for k, r in labels.items() if is_test(*k)}
    train = [r for k, r in labels.items() if k not in test]
    out = {"k": K, "n_labels": len(labels), "n_up": sum(r["vote"] == "up" for r in labels.values()), "n_train": len(train), "n_test": len(test),
           "min_labels": MIN_LABELS, "split": "sha256(tender_key|contact_id) % 5 == 0 -> test (20 %), deterministic",
           "method": "smoothed log-odds per feature (CPV prefix / keyword / source / buyer country) from the owner's votes; ranking, not model training",
           "precision_at_5": None, "n_queries": 0, "n_queries_ranked": 0}
    if len(labels) < MIN_LABELS or not test:
        why = f"меток {len(labels)} < {MIN_LABELS}" if len(labels) < MIN_LABELS else "в тестовой части нет меток"
        return out | {"status": "INSUFFICIENT_EVIDENCE", "note": f"недостаточно данных: {why}; качество ранжирования не измерено"}
    w = weights_from(train)
    by_q: dict[int, list[dict]] = {}
    for (_, cid), r in test.items():
        by_q.setdefault(cid, []).append(r)

    def p_at_k(items, key):
        ranked = sorted(items, key=lambda r: (-key(r), r["tender_key"]))[:K]
        return sum(r["vote"] == "up" for r in ranked) / len(ranked)
    base = [p_at_k(v, lambda r: float(r.get("base_score") or 0)) for v in by_q.values()]
    learned = [p_at_k(v, lambda r: float(r.get("base_score") or 0) + adjustment(r.get("features") or [], w)[0]) for v in by_q.values()]
    b, lv = sum(base) / len(base), sum(learned) / len(learned)
    ranked = sum(len(v) > K for v in by_q.values())
    return out | {"status": "OK", "n_queries": len(by_q), "n_queries_ranked": ranked,
                  "precision_at_5": {"baseline": round(b, 3), "learned": round(lv, 3), "delta": round(lv - b, 3)},
                  "note": ("ранжирование влияет только на компании с > 5 тестовыми парами: таких " + str(ranked)
                           + "; при малом числе меток разница может быть случайной")}


# ------------------------------------------------------------------ ranking over the store
TENDER_BRIEF = ("key", "title", "title_en", "buyer", "country", "jurisdiction", "status", "deadline", "estimated_value", "currency",
                "source_url", "source_id", "retrieved_at", "cpv")
WEIGHTS_KV = "match_weights"


def current_weights(store) -> dict[str, dict]:
    doc = store.kv_get(WEIGHTS_KV)
    if doc is None:                                       # older DB without the cached table: same numbers from the votes
        return weights_from(effective_labels(store.match_feedback()).values())
    return doc.get("weights") or {}


def is_pitch_recommendable(item: dict, reference: datetime | date | None = None) -> bool:
    """True only when a tender match is open, not expired in UTC, not blocked, and has no REVIEW_REQUIRED military/dual-use/sanctions gate."""
    t = item.get("tender") or {}
    if t and rules.effective_status(t, reference) != "open":
        return False
    if item.get("blocked"):
        return False
    if any(b.get("level") == "REVIEW_REQUIRED" for b in (item.get("badges") or [])):
        return False
    if t and rules.is_controlled(t):
        return False
    return True


def _item(t: dict, prof: Profile, m: dict, weights: dict, sanctions, vote: str | None, *,
          reference: datetime | date | None = None) -> dict:
    badges, blocks = assess(t, prof, m, sanctions)
    adj, contrib = adjustment(m["features"], weights)
    has_review_req = any(b.get("level") == "REVIEW_REQUIRED" for b in badges) or rules.is_controlled(t)
    eff_st = rules.effective_status(t, reference)
    recommendable = (not blocks) and (not has_review_req) and (eff_st == "open")
    return {"score": round(m["base_score"] + adj, 1), "base_score": m["base_score"], "learned_adjustment": adj, "contributions": contrib,
            "reasons": m["reasons"], "cpv": m["cpv"], "keywords": m["keywords"], "badges": badges, "blocked": blocks, "vote": vote,
            "recommendable": recommendable,
            "recommendation_status": "RECOMMENDED" if recommendable else "REVIEW_REQUIRED_DO_NOT_PITCH"}


def _order(items: list[dict], key_of) -> list[dict]:
    return sorted(items, key=lambda i: (-i["score"], -i["base_score"], key_of(i)))


def rank_for_contact(store, contact: dict, sanctions, *, status: str = "open", k: int = 10,
                     today: date | datetime | None = None, tenders: list[dict] | None = None) -> dict:
    prof, weights = Profile(contact), current_weights(store)
    votes = {key: r["vote"] for key, r in effective_labels(store.match_feedback()).items() if key[1] == contact["id"]}
    items, blocked = [], []
    for t in (tenders if tenders is not None else store.latest_tenders()):
        eff_st = rules.effective_status(t, today)
        if status == "open" and eff_st != "open":
            continue
        t_view = {**t, "status": eff_st} if eff_st != t.get("status") else t
        m = match(t_view, prof)
        if not m["matched"]:
            continue
        it = _item(t_view, prof, m, weights, sanctions, votes.get((t_view["key"], contact["id"])), reference=today)
        it["tender"] = {f: t_view.get(f) for f in TENDER_BRIEF}
        (blocked if it["blocked"] else items).append(it)
    items = _order(items, lambda i: i["tender"]["key"])
    return {"contact_id": contact["id"], "company": contact["company"], "status_filter": status, "k": k, "total": len(items),
            "items": items[:k], "blocked_total": len(blocked),
            "blocked": [{"tender_key": b["tender"]["key"], "title": b["tender"]["title"], "reasons": b["blocked"]} for b in blocked[:20]],
            "learning": {"labels": len(votes), "method": "base (CPV + keywords) + 10 x sum of smoothed log-odds feature weights from owner votes"}}


def rank_for_tender(store, tender: dict, sanctions, *, k: int = 10, include_declined: bool = False,
                    today: date | datetime | None = None) -> dict:
    weights = current_weights(store)
    votes = {key: r["vote"] for key, r in effective_labels(store.match_feedback()).items() if key[0] == tender["key"]}
    eff_st = rules.effective_status(tender, today)
    t_view = {**tender, "status": eff_st} if eff_st != tender.get("status") else tender
    text = tender_text(t_view)
    items, blocked, declined = [], [], 0
    for c in store.list_contacts():
        if c.get("status") == "declined" and not include_declined:
            declined += 1
            continue
        prof = Profile(c)
        m = match(t_view, prof, text)
        if not m["matched"]:
            continue
        it = _item(t_view, prof, m, weights, sanctions, votes.get((t_view["key"], c["id"])), reference=today)
        it["contact"] = {"id": c["id"], "company": c["company"], "country": c.get("country"), "status": c.get("status"),
                         "products": (c.get("products") or "")[:200]}
        (blocked if it["blocked"] else items).append(it)
    items = _order(items, lambda i: i["contact"]["id"])
    return {"tender_key": t_view["key"], "k": k, "total": len(items), "items": items[:k], "skipped_declined": declined,
            "blocked_total": len(blocked),
            "blocked": [{"contact_id": b["contact"]["id"], "company": b["contact"]["company"], "reasons": b["blocked"]} for b in blocked]}


def record_vote(store, tender: dict, contact: dict, vote: str, reason: str | None, actor: str) -> dict:
    """Store the vote with the pair's features at vote time, then update the feature weights (online, deterministic)."""
    m = match(tender, Profile(contact))
    doc = {"features": m["features"], "base_score": m["base_score"], "matched": m["matched"]}
    fid = store.add_match_feedback(tender["key"], contact["id"], vote, reason, doc, actor)
    labels = effective_labels(store.match_feedback())
    weights = weights_from(labels.values())
    store.kv_set(WEIGHTS_KV, {"weights": weights, "labels": len(labels), "last_feedback_id": fid})
    return {"id": fid, "vote": vote, "features": m["features"], "base_score": m["base_score"], "matched": m["matched"],
            "labels": len(labels), "weights": {f: weights[f] for f in m["features"] if f in weights}}


# ------------------------------------------------------------------ outputs: pitch (markdown) and Bossman lesson
def _one_line(s) -> str:
    return " ".join(str(s or "").split())


def _site(url: str | None) -> str | None:
    if not url:
        return None
    return url if url.lower().startswith(("http://", "https://")) else "https://" + url


def pitch_markdown(contact: dict, items: list[dict], today: date | datetime | None = None, *,
                   recommendations_only: bool = False) -> str:
    """Sales follow-up draft for the owner. No person data, no e-mail, no notes: only public company facts and tenders.
    Expired or blocked tenders never enter the pitch. Military/dual-use REVIEW_REQUIRED tenders are excluded from
    sales recommendations (when cleared recommendations exist or `recommendations_only=True`) and explicitly flagged as
    non-recommendable (`REVIEW_REQUIRED — НЕ РЕКОМЕНДАЦИЯ К ПРОДАЖЕ`) when listing a defence-only research view."""
    ref = today if today is not None else datetime.now(timezone.utc)
    day_iso = (ref.astimezone(timezone.utc).date() if ref.tzinfo else ref.date()).isoformat() if isinstance(ref, datetime) else ref.isoformat()
    c = contact
    open_items = [
        it for it in (items or [])
        if not it.get("blocked")
        and rules.effective_status(it.get("tender") or {}, ref) == "open"
    ]
    rec_items = [it for it in open_items if is_pitch_recommendable(it, ref)]
    if recommendations_only or (rec_items and len(rec_items) < len(open_items)):
        shown_items = rec_items
    else:
        shown_items = open_items
    lines = [f"# {_one_line(c['company'])}: подходящие открытые тендеры", "",
             f"Подготовлено War Terminal {day_iso} для владельца. Черновик для продажи: приложение никому ничего не отправляет.", "",
             f"- Компания: {_one_line(c['company'])}" + (f" ({c['country']})" if c.get("country") else ""),
             *([f"- Сайт: {_site(c['website'])}"] if c.get("website") else []),
             *([f"- Продукты: {_one_line(redact(c['products']))}"] if c.get("products") else []),
             f"- Профиль CPV: {', '.join(c.get('cpv_prefixes') or []) or 'не указан'}",
             *([f"- Источник контакта: {_one_line(c['source'])}"] if c.get("source") else []), ""]
    if not shown_items:
        lines += ["Открытых подходящих тендеров сейчас нет.", ""]
    for i, it in enumerate(shown_items, 1):
        t = it["tender"]
        rec_ok = is_pitch_recommendable(it, ref)
        rec_line = (
            "RECOMMENDED (открыт, без REVIEW_REQUIRED блокировок)"
            if rec_ok
            else "REVIEW_REQUIRED — НЕ РЕКОМЕНДАЦИЯ К ПРОДАЖЕ (требуется лицензия MPO / юридическая проверка)"
        )
        value = f"{t['estimated_value']:,.0f} {t.get('currency') or ''}".replace(",", " ").strip() if t.get("estimated_value") is not None else "не указана"
        lines += [f"## {i}. {_one_line(t.get('title'))}", "",
                  f"- Заказчик: {_one_line(t.get('buyer')) or 'не указан'} ({t.get('country') or t.get('jurisdiction') or '—'})",
                  f"- Стоимость: {value}",
                  f"- Срок подачи: {(t.get('deadline') or 'не указан')[:10]}",
                  f"- Ссылка: {t.get('source_url')} (источник {t.get('source_id')}, получено {str(t.get('retrieved_at') or '')[:16]})",
                  f"- Почему подходит: {'; '.join(it['reasons'])}",
                  f"- Оценка: {it['score']} (база {it['base_score']}, по отзывам владельца {it['learned_adjustment']:+})",
                  f"- Проверки: {'; '.join(b['text'] for b in it['badges']) or 'нет'}",
                  f"- Статус рекомендации: {rec_line}", ""]
    lines += ["---", "Оценка соответствия — эвристика по CPV и ключевым словам с поправкой по отзывам владельца; "
              "это не вероятность победы и не юридическое заключение. Военные и двухцелевые товары — только после лицензии и юридической проверки."]
    return "\n".join(lines) + "\n"


def lesson(contact: dict, labels: list[dict], weights: dict[str, dict], top: list[dict], metric: dict) -> tuple[str, str]:
    """(title, markdown) for Bossman memory. Company-level facts, learned features, current top tenders; no person data,
    no e-mail, no notes, no vote reasons (free text)."""
    c = contact
    up = [r for r in labels if r["vote"] == "up"]
    down = [r for r in labels if r["vote"] == "down"]

    def feats(rows):
        n: dict[str, int] = {}
        for r in rows:
            for f in r.get("features") or []:
                n[f] = n.get(f, 0) + 1
        return ", ".join(f"{f} (x{k}, вес {weights.get(f, {}).get('w', 0):+.2f})" for f, k in sorted(n.items(), key=lambda x: (-x[1], x[0]))[:8]) or "—"
    title = f"War Terminal: что совпадает для {_one_line(c['company'])}"
    body = [f"# {title}", "",
            "Источник: War Terminal (локально). Метод: совпадение CPV и ключевых слов + поправка по отзывам владельца "
            "(сглаженные log-odds по признакам cpv/kw/src/bc). Это ранжирование по отзывам, не обучение модели.", "",
            f"- Компания: {_one_line(c['company'])}" + (f" ({c['country']})" if c.get("country") else ""),
            f"- Профиль CPV: {', '.join(c.get('cpv_prefixes') or []) or 'не указан'}",
            *([f"- Продукты: {_one_line(redact(c['products']))}"] if c.get("products") else []),
            f"- Отзывы владельца по компании: 👍 {len(up)}, 👎 {len(down)}" + ("" if labels else " — отзывов пока нет, урок содержит только профиль и текущие совпадения"),
            f"- Признаки в 👍: {feats(up)}",
            f"- Признаки в 👎: {feats(down)}",
            f"- Метрика precision@5 (все компании): {metric['status']}"
            + (f", база {metric['precision_at_5']['baseline']} → с отзывами {metric['precision_at_5']['learned']} (тест n={metric['n_test']})"
               if metric.get("precision_at_5") else f" (меток {metric['n_labels']}, нужно ≥ {metric['min_labels']})"), "",
            "## Правило для следующего подбора", "",
            "Предлагать этой компании открытые тендеры, где совпадают признаки из 👍; понижать тендеры с признаками из 👎. "
            "Военные товары — только после лицензии MPO и юридической проверки; БПЛА/радар/РЭБ — проверка по 2021/821.", "",
            "## Лучшие открытые тендеры сейчас", ""]
    body += [f"- {_one_line(it['tender'].get('title'))} — {it['tender'].get('country') or it['tender'].get('jurisdiction') or '—'}, "
             f"оценка {it['score']}, {it['tender'].get('source_url')}" for it in top[:5]] or ["- открытых подходящих тендеров нет"]
    return title, "\n".join(body) + "\n"


def today_utc() -> date:
    return datetime.now(timezone.utc).date()
