"""Duplicate linking: keep the FIRST request, link the rest, never silently drop.

A later item is merged into an earlier request only on evidence:

========================  ==========================================================  ==========
rule                      evidence                                                    confidence
========================  ==========================================================  ==========
A_phone / A_email /       the same normalised phone / e-mail / @handle, inside the    high
A_handle                  window, services compatible (equal or one side unknown)
C_same_text               same sender, near-identical text, inside the window         high / medium
C_same_sender_service     same sender, same service, inside the window                medium
B_name_service            same multi-word name (transliteration tolerant), same       medium
                          service, inside the window, senders not different
========================  ==========================================================  ==========

Anything weaker is NOT merged: it is reported as ``possible_duplicate`` on both rows and sent to manual review.
A conflicting known value (other phone, other amount in another currency, other deadline) always blocks a merge, and
two requests from the same person for DIFFERENT services stay two requests. The window is measured from the kept
(first) request. Amounts of different currencies are never reconciled.
"""
from __future__ import annotations

import copy
import datetime as dt
import difflib
import re
from dataclasses import dataclass, field
from typing import Optional

from .extract import Extracted

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "č": "c", "š": "s", "ž": "z", "ř": "r", "ě": "e", "ů": "u", "á": "a", "é": "e", "í": "i", "ó": "o",
    "ú": "u", "ý": "y", "ň": "n", "ď": "d", "ť": "t",
}
_PRIORITY = {"A_phone": 0, "A_email": 1, "A_handle": 2, "C_same_text": 3, "C_same_sender_service": 4,
             "B_name_service": 5}
_CONF_RANK = {"high": 2, "medium": 1}


# ----------------------------------------------------------------------------- normalisation keys
def email_key(email: str) -> str:
    """Lower-cased address; for gmail.com/googlemail.com also dots and +tag in the local part are ignored."""
    e = (email or "").strip().lower()
    if "@" not in e:
        return e
    local, domain = e.rsplit("@", 1)
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def handle_key(handle: str) -> str:
    return (handle or "").strip().lower().lstrip("@")


def _translit(s: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in s.lower())


def name_tokens(name: str) -> list:
    return [t for t in re.split(r"[^a-z0-9]+", _translit(name)) if t]


def name_similarity(a: str, b: str) -> float:
    ta, tb = sorted(name_tokens(a)), sorted(name_tokens(b))
    if not ta or not tb:
        return 0.0
    return difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()


def text_key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()[:400]


def text_similarity(a: str, b: str) -> float:
    ka, kb = text_key(a), text_key(b)
    if not ka or not kb:
        return 0.0
    return 1.0 if ka == kb else difflib.SequenceMatcher(None, ka, kb).ratio()


def _phones(e: Extracted) -> set:
    return ({e.phone} if e.phone else set()) | set(e.phone_alt)


def _services(e: Extracted) -> set:
    return {e.service} if e.service else set(e.service_candidates)


def _conflicts(p: Extracted, x: Extracted) -> list:
    out = []
    pp, xp = _phones(p), _phones(x)
    if pp and xp and not (pp & xp):
        out.append("phone")
    if p.email and x.email and email_key(p.email) != email_key(x.email):
        out.append("email")
    if p.handle and x.handle and handle_key(p.handle) != handle_key(x.handle):
        out.append("handle")
    if p.amount is not None and x.amount is not None and (p.amount != x.amount or p.currency != x.currency):
        out.append("amount")
    if p.deadline_date and x.deadline_date and p.deadline_date != x.deadline_date:
        out.append("deadline")
    return out


# ----------------------------------------------------------------------------- result types
@dataclass
class Link:
    dup: Extracted
    kept_src_id: str
    rule: str
    confidence: str
    delta_min: Optional[float]
    window_verified: bool
    similarity: Optional[float]
    new_info: list = field(default_factory=list)


@dataclass
class Cluster:
    row: Extracted                      # the kept request, enriched only into EMPTY fields (see ``filled``)
    members: list                       # original Extracted objects, first one is the kept request
    links: list = field(default_factory=list)
    filled: list = field(default_factory=list)      # ["phone<-tg:1008", ...]
    possible: list = field(default_factory=list)    # [{"other": Cluster, "why": str}]
    seq: int = 0                                    # creation order, makes tie-breaking deterministic

    @property
    def first_src_id(self) -> str:
        return self.members[0].src_id


@dataclass
class DedupeResult:
    clusters: list
    links: list


# ----------------------------------------------------------------------------- matching
def _compare(p: Extracted, x: Extracted, window_ok: bool, min_rank: int) -> Optional[dict]:
    """-> {"kind": merge|possible, "rule", "confidence", "similarity", "why"} or None."""
    pp, xp = _phones(p), _phones(x)
    rules = []
    if pp & xp:
        rules.append("A_phone")
    if p.email and x.email and email_key(p.email) == email_key(x.email):
        rules.append("A_email")
    if p.handle and x.handle and handle_key(p.handle) == handle_key(x.handle):
        rules.append("A_handle")
    sp, sx = _services(p), _services(x)
    overlap = bool(sp & sx)
    disjoint = bool(sp) and bool(sx) and not overlap
    conflicts = _conflicts(p, x)
    if rules:
        if disjoint:
            return None                 # same person, a different request
        if conflicts:
            return {"kind": "possible", "rule": rules[0], "confidence": "high", "similarity": None,
                    "why": "same " + rules[0][2:] + " but differing " + ",".join(conflicts)}
        return {"kind": "merge", "rule": rules[0], "confidence": "high", "similarity": None, "why": ""}
    if not window_ok:
        return None
    same_sender = bool(p.sender) and p.sender == x.sender and p.source == x.source
    diff_sender = bool(p.sender) and bool(x.sender) and p.sender != x.sender
    if same_sender:
        sim = text_similarity(p.text, x.text)
        if sim >= 0.9 and not conflicts:
            conf = "high" if sim == 1.0 else "medium"
            return _decide("C_same_text", conf, sim, min_rank, "same sender, near-identical text")
        if overlap and not conflicts:
            return _decide("C_same_sender_service", "medium", None, min_rank, "same sender and service")
        if overlap and conflicts:
            return {"kind": "possible", "rule": "C_same_sender_service", "confidence": "medium", "similarity": None,
                    "why": "same sender and service but differing " + ",".join(conflicts)}
    if p.name and x.name and overlap and not conflicts:
        nsim = name_similarity(p.name, x.name)
        if nsim >= 0.86:
            multi = min(len(name_tokens(p.name)), len(name_tokens(x.name))) >= 2
            if multi and not diff_sender:
                return _decide("B_name_service", "medium", nsim, min_rank, "same name and service")
            return {"kind": "possible", "rule": "B_name_service", "confidence": "low", "similarity": nsim,
                    "why": ("single-word name" if not multi else "different senders") + " with the same service"}
    return None


def _decide(rule: str, conf: str, sim: Optional[float], min_rank: int, why: str) -> dict:
    if _CONF_RANK[conf] >= min_rank:
        return {"kind": "merge", "rule": rule, "confidence": conf, "similarity": sim, "why": why}
    return {"kind": "possible", "rule": rule, "confidence": conf, "similarity": sim,
            "why": why + f" (confidence {conf} is below the merge threshold)"}


# ----------------------------------------------------------------------------- enrichment
def _refresh_missing(p: Extracted) -> None:
    drop = set()
    if p.name:
        drop.add("name_missing")
    if p.phone or p.phone_raw:
        drop.add("phone_missing")
    if p.email or p.email_raw:
        drop.add("email_missing")
    if p.service or p.service_candidates:
        drop.add("service_missing")
    if p.amount is not None or p.amount_raw or p.amount_candidates:
        drop.add("amount_missing")
    if p.deadline_raw and p.deadline_basis != "urgent":
        drop.add("deadline_missing")
    if p.phone or p.email or p.handle:
        drop.add("contact_missing")
    p.flags = [f for f in p.flags if f not in drop]


def _fill(p: Extracted, x: Extracted) -> list:
    """Copy fields of ``x`` into EMPTY, unambiguous fields of ``p``. -> list of field names (filled or blocked)."""
    info: list = []
    if x.name and not p.name:
        p.name = x.name
        info.append("name")
    if x.phone and not p.phone:
        if "phone_ambiguous" in p.flags:
            info.append("phone(not filled: ambiguous in kept row)")
        else:
            p.phone, p.phone_alt = x.phone, list(x.phone_alt)
            info.append("phone")
    if x.email and not p.email:
        if "email_invalid" in p.flags:
            info.append("email(not filled: invalid in kept row)")
        else:
            p.email = x.email
            info.append("email")
    if x.handle and not p.handle:
        p.handle = x.handle
        info.append("handle")
    if x.service and not p.service:
        if p.service_candidates:
            info.append("service(not filled: ambiguous in kept row)")
        else:
            p.service = x.service
            info.append("service")
    if x.amount is not None and p.amount is None:
        if p.amount_raw or p.amount_candidates:
            info.append("amount(not filled: ambiguous in kept row)")
        elif "amount_ambiguous" in x.flags or "amount_multiple" in x.flags:
            pass
        else:
            p.amount, p.currency, p.amount_raw, p.amount_candidates = x.amount, x.currency, x.amount_raw, \
                list(x.amount_candidates)
            if "amount_currency_missing" in x.flags and "amount_currency_missing" not in p.flags:
                p.flags.append("amount_currency_missing")
            info.append("amount")
    if x.deadline_raw and x.deadline_basis != "urgent" and not p.deadline_raw and \
            "deadline_ambiguous" not in x.flags:
        p.deadline_raw, p.deadline_date, p.deadline_basis = x.deadline_raw, x.deadline_date, x.deadline_basis
        info.append("deadline")
    if "urgent" in x.flags and "urgent" not in p.flags:
        p.flags.append("urgent")
    _refresh_missing(p)
    return info


def _merge_answered(p: Extracted, x: Extracted) -> None:
    if x.answered is True and p.answered is not True:
        p.answered, p.answered_basis = True, x.answered_basis
    elif x.answered is False and p.answered is None:
        p.answered, p.answered_basis = False, x.answered_basis


# ----------------------------------------------------------------------------- main
class _Index:
    """Candidate lookup so that matching stays near-linear: an item is only compared with clusters that share a
    phone / e-mail / @handle / sender or a name token prefix with it."""

    def __init__(self):
        self.by_key: dict = {}

    def _put(self, key, cluster) -> None:
        self.by_key.setdefault(key, {})[cluster.seq] = cluster

    @staticmethod
    def keys(e: Extracted) -> list:
        keys = [("phone", p) for p in _phones(e)]
        if e.email:
            keys.append(("email", email_key(e.email)))
        if e.handle:
            keys.append(("handle", handle_key(e.handle)))
        if e.sender:
            keys.append(("sender", e.source, e.sender))
        if e.name:
            keys += [("name", t[:2]) for t in name_tokens(e.name)]
        return keys

    def add(self, cluster) -> None:
        for k in self.keys(cluster.row):
            self._put(k, cluster)

    def candidates(self, x: Extracted) -> list:
        found: dict = {}
        for k in self.keys(x):
            found.update(self.by_key.get(k, {}))
        return [found[s] for s in sorted(found)]


def dedupe(items: list, window_min: int = 1440, min_confidence: str = "medium") -> DedupeResult:
    """Cluster ``Extracted`` request items. ``items`` are not modified; each cluster holds a deep-copied kept row."""
    if min_confidence not in _CONF_RANK:
        raise ValueError("min_confidence must be 'high' or 'medium'")
    min_rank = _CONF_RANK[min_confidence]
    window = dt.timedelta(minutes=window_min)
    order = sorted(range(len(items)),
                   key=lambda i: (items[i].ts_utc is None, items[i].ts_utc or dt.datetime.min, i))
    clusters: list = []
    index = _Index()
    links: list = []
    for i in order:
        x = items[i]
        merges, possibles = [], []
        for c in index.candidates(x):
            kept_ts = c.members[0].ts_utc
            if x.ts_utc is not None and (kept_ts is None or x.ts_utc - kept_ts > window):
                continue                # the window runs from the kept (first) request
            delta = (x.ts_utc - kept_ts).total_seconds() / 60 if (x.ts_utc and kept_ts) else None
            res = _compare(c.row, x, window_ok=delta is not None and abs(delta) <= window_min, min_rank=min_rank)
            if res is None:
                continue
            res["cluster"], res["delta"] = c, delta
            (merges if res["kind"] == "merge" else possibles).append(res)
        if merges:
            best = min(merges, key=lambda r: (_PRIORITY[r["rule"]],
                                              abs(r["delta"]) if r["delta"] is not None else 1e18,
                                              r["cluster"].seq))
            c = best["cluster"]
            info = _fill(c.row, x)
            _merge_answered(c.row, x)
            link = Link(dup=x, kept_src_id=c.first_src_id, rule=best["rule"], confidence=best["confidence"],
                        delta_min=None if best["delta"] is None else round(best["delta"], 1),
                        window_verified=best["delta"] is not None, similarity=best["similarity"], new_info=info)
            c.members.append(x)
            c.links.append(link)
            c.filled += [f"{k}<-{x.src_id}" for k in info if "(" not in k]
            links.append(link)
            index.add(c)                # the kept row may have gained a phone / e-mail / name
            for pos in possibles:
                if pos["cluster"] is not c:
                    _note_possible(c, pos["cluster"], pos["why"])
            continue
        new = Cluster(row=copy.deepcopy(x), members=[x], seq=len(clusters))
        for pos in possibles:
            _note_possible(new, pos["cluster"], pos["why"])
        clusters.append(new)
        index.add(new)
    return DedupeResult(clusters=clusters, links=links)


def _note_possible(a: Cluster, b: Cluster, why: str) -> None:
    if all(p["other"] is not b for p in a.possible):
        a.possible.append({"other": b, "why": why})
    if all(p["other"] is not a for p in b.possible):
        b.possible.append({"other": a, "why": why})
    for c in (a, b):
        if "possible_duplicate" not in c.row.flags:
            c.row.flags.append("possible_duplicate")
