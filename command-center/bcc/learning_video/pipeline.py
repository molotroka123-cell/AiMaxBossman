"""End-to-end offline pipeline over a VTT text. The extractor is injected (rule-based here, local model in a live run)."""
from __future__ import annotations

import json
from pathlib import Path

from .claims import claims_from_model_json, rule_extractor
from .dedupe import dedupe_chunks, dedupe_claims
from .transcript import chunk_cues, parse_vtt, source_label, transcript_quality


def load_store(path) -> list:
    """Read previously stored claims (JSONL) as light objects for cross-video dedupe. Corrupt lines are skipped."""
    from .claims import Claim
    p = Path(path)
    out = []
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(Claim(**json.loads(ln)))
            except (ValueError, TypeError):
                continue
    return out


def run_pipeline(vtt_text: str, *, url: str, kind: str, lang: str = "ru", extractor=None, model_extractor=None,
                 store_path=None, min_quality: float = 0.7) -> dict:
    """extractor(chunk, source_kind=, lang=) -> [Claim]; model_extractor(chunk) -> raw model text (validated here).
    Returns a report; claims are appended to store_path only when the transcript passes the quality gate."""
    label = source_label(url=url, kind=kind, lang=lang, raw_bytes=vtt_text.encode("utf-8"))
    cues = parse_vtt(vtt_text)
    q = transcript_quality(cues, lang)
    report = {"source": label, "quality": q, "chunks": 0, "chunks_dropped_dup": 0, "claims": [], "rejects": [],
              "claims_dropped_dup": 0, "stored": 0, "status": "UNVERIFIED"}
    if q["score"] < min_quality:
        report["status"] = "REJECTED_LOW_QUALITY"
        return report
    chunks = chunk_cues(cues, label["source_id"])
    kept = dedupe_chunks(chunks)
    report["chunks"], report["chunks_dropped_dup"] = len(kept), len(chunks) - len(kept)
    found = []
    for ch in kept:
        if model_extractor is not None:
            ok, bad = claims_from_model_json(model_extractor(ch), ch, source_kind=kind, lang=lang)
            found += ok
            report["rejects"] += bad
        else:
            found += (extractor or rule_extractor)(ch, source_kind=kind, lang=lang)
    known = load_store(store_path) if store_path else []
    uniq, dropped = dedupe_claims(found, known)
    report["claims"], report["claims_dropped_dup"] = uniq, dropped
    if store_path and uniq:
        with Path(store_path).open("a", encoding="utf-8") as fh:
            for c in uniq:
                fh.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
        report["stored"] = len(uniq)
    return report
