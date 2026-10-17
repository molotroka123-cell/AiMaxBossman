"""Highlight finder: transcript windows aligned to phrase boundaries (15-60 s), scored by transparent signals
(audio energy, speech density, hook words, scene changes, clean ending) and by a local LLM rating hook strength.
Scores rank moments inside ONE source; they are not a prediction of views."""
from __future__ import annotations

import re
from typing import Callable

import numpy as np

from .llm import parse_json

HOOK_WORDS = re.compile(r"\b(why|how|what|never|secret|nobody|mistake|truth|imagine|stop|wait|actually|"
                        r"почему|как|что|никогда|секрет|никто|ошибк\w*|правд\w*|представь\w*|стоп|подожди|на самом деле)\b", re.I)


def candidate_windows(segments: list[dict], min_s: float = 15.0, max_s: float = 60.0) -> list[dict]:
    out = []
    for i in range(len(segments)):
        st = segments[i]["start"]
        for j in range(i, len(segments)):
            en = segments[j]["end"]
            d = en - st
            if d > max_s:
                break
            if d >= min_s:
                out.append({"i": i, "j": j, "start": round(st, 3), "end": round(en, 3), "dur": round(d, 3)})
    return out


def _signals(w: dict, segments: list[dict], energy: dict, cuts: list[float]) -> dict:
    segs = segments[w["i"]: w["j"] + 1]
    text = " ".join(s["text"] for s in segs)
    first = segs[0]["text"]
    n_words = sum(len(s.get("words") or s["text"].split()) for s in segs)
    t = np.array(energy.get("t") or [0.0])
    z = np.array(energy.get("z") or [0.0])
    m = (t >= w["start"]) & (t < w["end"])
    ez = z[m] if m.any() else np.array([0.0])
    n_cuts = sum(1 for c in cuts if w["start"] <= c < w["end"])
    return {"energy_mean_z": round(float(ez.mean()), 3), "energy_p90_z": round(float(np.percentile(ez, 90)), 3),
            "words_per_s": round(n_words / max(w["dur"], 1e-6), 2), "hook_first_phrase": bool(HOOK_WORDS.search(first)) or first.rstrip().endswith(("?", "!")),
            "has_number": bool(re.search(r"\d", first)), "cuts_per_10s": round(10 * n_cuts / max(w["dur"], 1e-6), 2),
            "clean_end": segs[-1]["text"].rstrip().endswith((".", "!", "?", "…")), "text": text, "first": first}


def heuristic(sig: dict, dur: float) -> tuple[float, list[str]]:
    parts, why = [], []
    e = min(1.0, max(0.0, 0.5 + 0.35 * sig["energy_mean_z"] + 0.15 * sig["energy_p90_z"]))
    parts.append((0.30, e))
    if sig["energy_mean_z"] > 0.3:
        why.append(f"громкость выше обычной (+{sig['energy_mean_z']:.1f}σ)")
    wps = sig["words_per_s"]
    dens = 1.0 if 2.0 <= wps <= 3.6 else max(0.0, 1 - abs(wps - 2.8) / 2.8)
    parts.append((0.20, dens))
    hook = 1.0 if sig["hook_first_phrase"] else (0.6 if sig["has_number"] else 0.2)
    parts.append((0.25, hook))
    if sig["hook_first_phrase"]:
        why.append("сильная первая фраза (вопрос/крючок)")
    cuts = min(1.0, sig["cuts_per_10s"] / 3.0) if sig["cuts_per_10s"] <= 6 else 0.5
    parts.append((0.10, cuts))
    if sig["cuts_per_10s"] >= 1:
        why.append(f"смена планов {sig['cuts_per_10s']}/10с")
    parts.append((0.10, 1.0 if sig["clean_end"] else 0.3))
    parts.append((0.05, max(0.0, 1 - abs(dur - 32) / 30)))
    return round(sum(wt * v for wt, v in parts), 4), why


def _overlap(a: dict, b: dict) -> float:
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return inter / min(a["dur"], b["dur"])


def pick_non_overlapping(items: list[dict], k: int, max_overlap: float = 0.0) -> list[dict]:
    out = []
    for it in items:
        if all(_overlap(it, o) <= max_overlap + 1e-9 for o in out):
            out.append(it)
        if len(out) >= k:
            break
    return out


LLM_SYSTEM = ("You rate moments of a video for a YouTube Short. For each candidate transcript excerpt give hook_score 0-10: "
              "how strongly the FIRST seconds make a viewer keep watching and whether the excerpt is understandable on its own "
              "and ends cleanly. Be strict; 9-10 is rare. Reply ONLY with JSON: "
              '{"items":[{"id":<int>,"hook_score":<int>,"reason":"<one short sentence>"}]}')


def llm_rate(cands: list[dict], chat: Callable | None) -> dict:
    if chat is None:
        return {"status": "NOT_CONNECTED", "scores": {}}
    payload = "\n\n".join(f"id={c['id']} ({c['dur']:.0f} s): {c['text'][:900]}" for c in cands)
    try:
        res = parse_json(chat([{"role": "system", "content": LLM_SYSTEM}, {"role": "user", "content": payload}]))
        scores = {}
        for it in res.get("items") or []:
            try:
                scores[int(it["id"])] = {"hook_score": max(0, min(10, int(round(float(it["hook_score"]))))),
                                         "reason": str(it.get("reason", ""))[:240]}
            except (KeyError, TypeError, ValueError):
                continue
        return {"status": "OK" if scores else "ERROR", "scores": scores}
    except Exception as exc:  # noqa: BLE001 - the heuristic ranking still works; reported, not hidden
        return {"status": "ERROR", "scores": {}, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def find_highlights(transcript: dict, energy: dict, cuts: list[float], *, k: int = 3, chat: Callable | None = None,
                    min_s: float = 15.0, max_s: float = 60.0, llm_pool: int = 8) -> dict:
    segs = [s for s in transcript.get("segments", []) if s.get("text")]
    span = (segs[-1]["end"] - segs[0]["start"]) if segs else 0.0
    # a short source cannot hold k long windows: shrink the max length until k disjoint windows fit
    caps = [max_s] + [c for c in (span / k, span / (k + 1)) if min_s <= c < max_s]
    for cap in caps:
        wins = candidate_windows(segs, min_s, cap)
        for w in wins:
            w["signals"] = _signals(w, segs, energy, cuts)
            w["heuristic"], w["why"] = heuristic(w["signals"], w["dur"])
        wins.sort(key=lambda w: -w["heuristic"])
        if len(pick_non_overlapping(wins, k, max_overlap=0.0)) >= k:
            break
    disjoint = pick_non_overlapping(wins, k, max_overlap=0.0)
    pool = list(disjoint)
    pool += [w for w in pick_non_overlapping(wins, llm_pool, max_overlap=0.35) if w not in pool][: max(0, llm_pool - len(pool))]
    for n, c in enumerate(pool):
        c["id"], c["text"] = n, c["signals"]["text"]
    rated = llm_rate(pool, chat)
    for c in pool:
        r = rated["scores"].get(c["id"])
        if r:
            c["llm"] = r
            c["score"] = round(0.6 * r["hook_score"] / 10 + 0.4 * c["heuristic"], 4)
            c["why"] = c["why"] + [f"LLM {r['hook_score']}/10: {r['reason']}"]
        else:
            c["llm"] = None
            c["score"] = c["heuristic"]
    pool.sort(key=lambda c: -c["score"])
    chosen = pick_non_overlapping(pool, k, max_overlap=0.0)
    if len(chosen) < len(disjoint):           # a top window blocked two others: keep k disjoint moments instead
        chosen = sorted(disjoint, key=lambda c: -c["score"])
    for c in chosen:
        c.pop("i", None)
        c.pop("j", None)
    return {"candidates_total": len(wins), "rated": len(pool), "llm_status": rated["status"], "llm_error": rated.get("error"),
            "highlights": chosen, "note": "оценка ранжирует моменты внутри источника, это не прогноз просмотров"}
