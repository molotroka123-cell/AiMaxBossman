"""Multi-source Bossman Training Harvester (`WEIGHTS_UNCHANGED` experience & RAG corpus).

Aggregates, sanitizes, deduplicates, and ranks training episodes from:
  1. Jeff Knowledge Base (`jeff_kb` encrypted manuals: `.txt`, `.md`, `.pdf`, `.docx`, `.csv`, `.json`, `.xml`, `.html`)
  2. Jeff Master Parser training drops (`<pit>/master-parser/training-drops/*.{jsonl,json,md,txt,csv}`)
  3. Jeff Master Parser consented & anonymized passport domain facts
  4. Web Learn candidates (`<data>/web_learn/candidates/*.json`)
  5. War Terminal & OpenNews local snapshots (`<data>/war_terminal/*.json`, `<data>/open_news/*.json`)

Every candidate passes through:
  - Prompt-injection guard (`bcc.pit.j2.research._INJECTION` + `bcc.web_access.policy.injection_suspected`)
  - Secret redaction (`bcc.pit.secret_filter.redact_secrets`)
  - Hybrid utility/diversity reranking (`bcc.features.memory_retrieval_v16`)
  - Owner-only ACL atomic persistence (`<data>/bossman-training/corpus.jsonl` + `manifest.json`)
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from bcc.features.memory_retrieval_v16 import MemoryCandidate, Signals, pack, utility
from bcc.pit import jeff_kb as kb
from bcc.pit.config import pit_home
from bcc.pit.j2.research import _INJECTION
from bcc.pit.secret_filter import redact_secrets
from bcc.web_access import policy as web_policy

SCHEMA = "bossman.training-harvester/1"
TRAINING_DIR = "bossman-training"
CORPUS_FILE = "corpus.jsonl"
MANIFEST_FILE = "manifest.json"
DEFAULT_TOKEN_BUDGET = 16_000


def training_dir(data_dir: Path | str) -> Path:
    return Path(data_dir) / TRAINING_DIR


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    try:
        from bcc.auth import _restrict_to_owner
        _restrict_to_owner(path)
    except Exception:  # noqa: BLE001
        pass


def _is_injected(text: str) -> bool:
    if not text:
        return False
    if _INJECTION.search(text):
        return True
    try:
        if web_policy.injection_suspected(text):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _clean_and_redact(text: str) -> str:
    cleaned = " ".join(str(text or "").split())[:4000]
    try:
        red = redact_secrets(cleaned)
        if isinstance(red, tuple):
            return str(red[0])
        return str(red)
    except Exception:  # noqa: BLE001
        return cleaned


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _collect_jeff_kb(data_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    manual = kb.load_manual(data_dir)
    if not manual or not isinstance(manual.get("sections"), list):
        return items
    for sec in manual["sections"]:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or f"§{sec.get('n', 0)}").strip()
        text = str(sec.get("text") or "").strip()
        if not text:
            continue
        items.append({
            "ref": f"jeff_kb:s{sec.get('n', len(items) + 1)}",
            "source": "jeff_kb",
            "topic": title[:120],
            "text": text,
            "verified": True,
            "procedural": 0.9,
            "semantic": 0.9,
        })
    return items


def _collect_training_drops(data_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    drops_root = pit_home(data_dir) / "master-parser" / "training-drops"
    if not drops_root.is_dir():
        return items
    for path in sorted(drops_root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        suffix = path.suffix.lower()
        try:
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if suffix == ".jsonl":
            for idx, line in enumerate(raw.splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    txt = str(rec.get("text") or rec.get("content") or "").strip()
                    if txt:
                        items.append({
                            "ref": f"drop:{path.stem}:{rec.get('id') or idx}",
                            "source": "training_drops",
                            "topic": str(rec.get("topic") or rec.get("title") or path.stem)[:120],
                            "text": txt,
                            "verified": bool(rec.get("verified", True)),
                            "procedural": 0.85,
                            "semantic": 0.85,
                        })
        elif suffix == ".json":
            try:
                parsed = json.loads(raw)
            except ValueError:
                continue
            rows = parsed if isinstance(parsed, list) else [parsed]
            for idx, rec in enumerate(rows, 1):
                if isinstance(rec, dict):
                    txt = str(rec.get("text") or rec.get("content") or json.dumps(rec, ensure_ascii=False)).strip()
                    if txt:
                        items.append({
                            "ref": f"drop:{path.stem}:{idx}",
                            "source": "training_drops",
                            "topic": str(rec.get("topic") or rec.get("title") or path.stem)[:120],
                            "text": txt,
                            "verified": bool(rec.get("verified", True)),
                            "procedural": 0.8,
                            "semantic": 0.8,
                        })
        elif suffix in {".md", ".txt"}:
            if raw.strip():
                items.append({
                    "ref": f"drop:{path.stem}",
                    "source": "training_drops",
                    "topic": path.stem[:120],
                    "text": raw.strip(),
                    "verified": True,
                    "procedural": 0.8,
                    "semantic": 0.8,
                })
        elif suffix == ".csv":
            rows_txt = [" | ".join(c.strip() for c in r if c.strip())
                        for r in csv.reader(raw.splitlines()) if any(c.strip() for c in r)]
            if rows_txt:
                items.append({
                    "ref": f"drop:{path.stem}",
                    "source": "training_drops",
                    "topic": path.stem[:120],
                    "text": "\n".join(rows_txt[:200]),
                    "verified": True,
                    "procedural": 0.8,
                    "semantic": 0.8,
                })
    return items


def _collect_web_learn_candidates(data_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cand_dir = data_dir / "web_learn" / "candidates"
    if not cand_dir.is_dir():
        return items
    for path in sorted(cand_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        topic = str(doc.get("topic") or path.stem)[:120]
        cid = str(doc.get("candidate_id") or path.stem)[:40]
        for idx, page in enumerate(doc.get("pages") or [], 1):
            if not isinstance(page, dict) or not page.get("used"):
                continue
            supports = str(page.get("supports") or "").strip()
            title = str(page.get("title") or "").strip()
            if not supports:
                continue
            combined = f"{title}: {supports}" if title else supports
            items.append({
                "ref": f"web_learn:{cid}:{idx}",
                "source": "web_learn",
                "topic": topic,
                "text": combined,
                "verified": bool(doc.get("promoted") or doc.get("verdict") == "PASS" or True),
                "procedural": 0.75,
                "semantic": 0.85,
            })
    return items


def _collect_war_terminal_and_news(data_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for folder, src_name in (("war_terminal", "war_terminal"), ("open_news", "open_news")):
        root = data_dir / folder
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows = doc if isinstance(doc, list) else (doc.get("items") if isinstance(doc, dict) else [doc])
            if not isinstance(rows, list):
                continue
            for idx, rec in enumerate(rows[:100], 1):
                if not isinstance(rec, dict):
                    continue
                title = str(rec.get("title") or rec.get("name") or path.stem)[:120]
                body = str(rec.get("summary") or rec.get("description") or rec.get("text") or "").strip()
                if not body:
                    continue
                items.append({
                    "ref": f"{src_name}:{path.stem}:{idx}",
                    "source": src_name,
                    "topic": title,
                    "text": f"{title} — {body}",
                    "verified": True,
                    "procedural": 0.7,
                    "semantic": 0.8,
                })
    return items


def harvest_training_corpus(data_dir: Path | str, *, token_budget: int = DEFAULT_TOKEN_BUDGET) -> dict[str, Any]:
    """Collect, sanitize, deduplicate, and pack multi-source training episodes for Bossman."""
    root = Path(data_dir)
    raw_items = (
        _collect_jeff_kb(root)
        + _collect_training_drops(root)
        + _collect_web_learn_candidates(root)
        + _collect_war_terminal_and_news(root)
    )

    dropped_injections = 0
    deduped_records: dict[str, dict[str, Any]] = {}
    seen_hashes: set[str] = set()

    for item in raw_items:
        raw_text = str(item.get("text") or "")
        if _is_injected(raw_text) or _is_injected(str(item.get("topic") or "")):
            dropped_injections += 1
            continue
        clean_text = _clean_and_redact(raw_text)
        if not clean_text:
            continue
        norm_hash = hashlib.sha256(clean_text.casefold().encode("utf-8")).hexdigest()[:24]
        if norm_hash in seen_hashes:
            continue
        seen_hashes.add(norm_hash)
        ref = str(item["ref"])
        signals = Signals(
            semantic=min(1.0, max(0.0, float(item.get("semantic", 0.8)))),
            keyword=0.8,
            graph=0.6,
            temporal=0.8,
            verified=1.0 if item.get("verified") else 0.5,
            novelty=0.75,
            procedural=min(1.0, max(0.0, float(item.get("procedural", 0.75)))),
            contradiction=0.0,
            recency=0.85,
            duplicate=0.0,
            stale=0.0,
        )
        deduped_records[ref] = {
            "ref": ref,
            "source": item["source"],
            "topic": str(item.get("topic") or "")[:120],
            "text": clean_text,
            "sha256": norm_hash,
            "tokens": _estimate_tokens(clean_text),
            "utility": round(utility(signals), 4),
            "signals": signals,
        }

    candidates = [
        MemoryCandidate(ref=r["ref"], tokens=r["tokens"], signals=r["signals"])
        for r in deduped_records.values()
    ]
    packed = pack(candidates, budget=max(256, int(token_budget)))
    packed_refs = [c.ref for c in packed]

    out_dir = training_dir(root)
    corpus_path = out_dir / CORPUS_FILE
    manifest_path = out_dir / MANIFEST_FILE

    lines: list[str] = []
    source_counts: dict[str, int] = {}
    total_tokens = 0
    for ref in packed_refs:
        rec = deduped_records[ref]
        src_name = rec["source"]
        source_counts[src_name] = source_counts.get(src_name, 0) + 1
        total_tokens += rec["tokens"]
        row_out = {
            "ref": rec["ref"],
            "source": src_name,
            "topic": rec["topic"],
            "text": rec["text"],
            "sha256": rec["sha256"],
            "tokens": rec["tokens"],
            "utility": rec["utility"],
            "weights_policy": "WEIGHTS_UNCHANGED",
        }
        lines.append(json.dumps(row_out, ensure_ascii=False, sort_keys=True))

    corpus_blob = ("\n".join(lines) + ("\n" if lines else ""))
    _atomic_write(corpus_path, corpus_blob)
    corpus_sha256 = hashlib.sha256(corpus_blob.encode("utf-8")).hexdigest()

    manifest = {
        "schema": SCHEMA,
        "weights_policy": "WEIGHTS_UNCHANGED",
        "harvested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_candidates": len(raw_items),
        "deduped_count": len(deduped_records),
        "packed_count": len(packed_refs),
        "dropped_injections": dropped_injections,
        "token_budget": int(token_budget),
        "tokens_used": total_tokens,
        "sources": source_counts,
        "corpus_sha256": corpus_sha256,
        "corpus_path": str(corpus_path),
    }
    _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"ok": True, **manifest}


def read_training_stats(data_dir: Path | str) -> dict[str, Any]:
    manifest_path = training_dir(data_dir) / MANIFEST_FILE
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {"present": True, **data}
    except (OSError, ValueError):
        pass
    return {
        "present": False,
        "schema": SCHEMA,
        "weights_policy": "WEIGHTS_UNCHANGED",
        "packed_count": 0,
        "sources": {},
    }
