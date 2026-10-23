"""Security gate BEFORE any Bossman training run (owner 23.10: «защититься в свете хакерских атак»).

    python tools/training_secure_gate.py --dataset <dir> --lock <requirements.lock> --out gate_report.json
        [--venv <python.exe>] [--allow-source jeff_kb --allow-source training_drops ...] [--max-injection-rate 0.0]

Stdlib only, offline, read-only. Checks, in this order (every check runs; verdict is FAIL if any fails):
  supply_chain  every line of the lock file is ``name==version --hash=sha256:...`` (hash-pinned), and the lock
                never names packages banned for the training box (litellm — PyPI/Docker compromise 21.10.2026).
  venv          (optional) the interpreter that will train has no banned package installed.
  manifest      ``manifest.json`` lists every data file with sha256 + bytes; the files on disk match it exactly,
                nothing extra, nothing missing (a tampered or injected file is a FAIL, not a warning).
  corpus        ``corpus.jsonl`` rows are objects with ``id``/``source``/``text``; every ``source`` is on the
                allow-list; prompt-injection patterns and secret-shaped strings are counted (text is NEVER printed
                or copied into the report — only counts and row ids); duplicates by normalised sha256 are counted.
  personal      no row text references the owner's protected locations (LOCALAPPDATA data dir, keys dir, Гречка,
                photos/voice dataset dirs) — training material must not carry pointers into private data.
  offline       the environment is prepared for an offline run: HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE / PIP_NO_INDEX
                are set to 1 (reported, and FAIL when --require-offline is given).
Exit 0 = PASS, 1 = FAIL, 2 = usage error. The report keeps counts, hashes and ids, never dataset text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BANNED_PACKAGES = ("litellm",)  # 1.82.7/1.82.8 and 1.107.0 shipped a credential stealer + RAT (2026)
LOCK_LINE = re.compile(r"^[A-Za-z0-9_.\-\[\]]+==[A-Za-z0-9_.+!-]+(\s+\\?\s*--hash=sha256:[0-9a-f]{64})+\s*$")
INJECTION = re.compile(
    r"(ignore (all |the )?(previous|prior|above) instructions|you are now|system prompt|developer message"
    r"|<\|im_start\|>|\[INST\]|BEGIN SYSTEM|do anything now|jailbreak|exfiltrat|curl\s+\S+\s*\|\s*(sh|bash)"
    r"|powershell\s+-e(nc|xecutionpolicy)|Invoke-WebRequest|base64 -d\s*\|)",
    re.I)
SECRET = re.compile(
    r"(sk-or-v1-[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{32,}|nvapi-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}"
    r"|gsk_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b[0-9]{9,10}:[A-Za-z0-9_-]{35}\b)")
PERSONAL_HINTS = ("AppData\\Local\\Bossman", "AppData/Local/Bossman", "\\keys\\provider-keys", "Гречка",
                  "lora-datasets-", "voice-train-", "\\datasets\\gf", "/datasets/gf")
DEFAULT_SOURCES = ("jeff_kb", "training_drops", "passport_facts", "web_learn", "war_terminal", "open_news",
                   "lessons", "owner_docs")
#: Sources allowed to shape WEIGHTS (owner-provenance only, decision 23.10). web_learn / war_terminal /
#: open_news are third-party text: RAG only (WEIGHTS_UNCHANGED), never fine-tuning data.
WEIGHTS_SOURCES = ("jeff_kb", "training_drops", "passport_facts", "lessons", "owner_docs")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_supply_chain(lock: Path) -> dict:
    if not lock.is_file():
        return {"name": "supply_chain", "ok": False, "detail": f"lock file missing: {lock}"}
    bad, banned, n = [], [], 0
    logical: list[tuple[int, str]] = []
    for i, raw in enumerate(lock.read_text(encoding="utf-8").splitlines(), 1):
        piece = raw.strip()
        if not piece or piece.startswith("#"):
            continue
        if logical and logical[-1][1].endswith("\\"):      # continuation of the previous requirement
            logical[-1] = (logical[-1][0], logical[-1][1][:-1].rstrip() + " " + piece)
        else:
            logical.append((i, piece))
    for i, line in logical:
        n += 1
        name = re.split(r"[=<>!~\[ ]", line, 1)[0].lower()
        if name in BANNED_PACKAGES:
            banned.append(f"line {i}: {name}")
        if not LOCK_LINE.match(line):
            bad.append(f"line {i}")
    ok = not bad and not banned and n > 0
    return {"name": "supply_chain", "ok": ok, "detail": f"{n} pinned lines; unpinned={bad[:5]}; banned={banned}"}


def check_venv(python: str | None) -> dict:
    if not python:
        return {"name": "venv", "ok": True, "detail": "skipped (no --venv)"}
    try:
        out = subprocess.run([python, "-I", "-m", "pip", "list", "--format=json"], capture_output=True,
                             text=True, timeout=120, check=False).stdout
        names = {p.get("name", "").lower() for p in json.loads(out or "[]")}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"name": "venv", "ok": False, "detail": f"cannot list packages: {type(exc).__name__}"}
    found = sorted(n for n in names if n in BANNED_PACKAGES)
    return {"name": "venv", "ok": not found, "detail": f"{len(names)} packages; banned installed={found}"}


def check_manifest(dataset: Path) -> dict:
    mpath = dataset / "manifest.json"
    if not mpath.is_file():
        return {"name": "manifest", "ok": False, "detail": "manifest.json missing"}
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        if "files" in manifest:
            files = {f["path"]: f for f in manifest["files"]}
        else:  # bcc.pit.master_parser.training_harvester manifest: one corpus file, sha256 of its content
            cp = dataset / "corpus.jsonl"
            files = {"corpus.jsonl": {"sha256": manifest["corpus_sha256"],
                                      "bytes": cp.stat().st_size if cp.is_file() else -1}}
    except (ValueError, KeyError, TypeError) as exc:
        return {"name": "manifest", "ok": False, "detail": f"manifest unreadable: {type(exc).__name__}"}
    on_disk = {p.relative_to(dataset).as_posix() for p in dataset.rglob("*")
               if p.is_file() and p.name not in ("manifest.json", "gate_report.json")}
    missing = sorted(set(files) - on_disk)
    extra = sorted(on_disk - set(files))
    changed = []
    for rel, meta in files.items():
        p = dataset / rel
        if p.is_file() and (_sha256(p) != meta.get("sha256") or p.stat().st_size != meta.get("bytes")):
            changed.append(rel)
    ok = not missing and not extra and not changed and bool(files)
    return {"name": "manifest", "ok": ok, "detail": f"{len(files)} files; missing={missing[:5]} extra={extra[:5]} "
                                                    f"changed={changed[:5]}", "dataset_id": manifest.get("dataset_id") or manifest.get("harvested_at")}


def check_corpus(dataset: Path, allowed: tuple[str, ...], max_injection_rate: float) -> tuple[dict, dict]:
    cpath = dataset / "corpus.jsonl"
    if not cpath.is_file():
        return ({"name": "corpus", "ok": False, "detail": "corpus.jsonl missing"},
                {"name": "personal", "ok": False, "detail": "corpus.jsonl missing"})
    rows = bad_rows = injected = secrets = dupes = personal = 0
    bad_sources: dict[str, int] = {}
    seen: set[str] = set()
    inj_ids: list[str] = []
    with cpath.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            rows += 1
            try:
                row = json.loads(line)
                text = str(row["text"])
                src = str(row.get("source", ""))
            except (ValueError, KeyError, TypeError):
                bad_rows += 1
                continue
            if src not in allowed:
                bad_sources[src] = bad_sources.get(src, 0) + 1
            if INJECTION.search(text):
                injected += 1
                if len(inj_ids) < 20:
                    inj_ids.append(str(row.get("id") or row.get("ref") or rows))
            if SECRET.search(text):
                secrets += 1
            if any(h.casefold() in text.casefold() for h in PERSONAL_HINTS):
                personal += 1
            digest = hashlib.sha256(" ".join(text.casefold().split()).encode("utf-8")).hexdigest()
            if digest in seen:
                dupes += 1
            seen.add(digest)
    rate = injected / rows if rows else 0.0
    ok = rows > 0 and bad_rows == 0 and not bad_sources and secrets == 0 and rate <= max_injection_rate
    corpus = {"name": "corpus", "ok": ok, "rows": rows, "bad_rows": bad_rows, "unknown_sources": bad_sources,
              "injection_rows": injected, "injection_ids": inj_ids, "secret_rows": secrets, "duplicate_rows": dupes,
              "detail": f"rows={rows} bad={bad_rows} injected={injected} ({rate:.4f}) secrets={secrets} dupes={dupes}"}
    personal_chk = {"name": "personal", "ok": personal == 0 and rows > 0,
                    "detail": f"rows pointing at protected owner locations: {personal}"}
    return corpus, personal_chk


def check_offline(require: bool) -> dict:
    flags = {k: os.environ.get(k) == "1" for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "PIP_NO_INDEX")}
    ok = all(flags.values()) or not require
    return {"name": "offline", "ok": ok, "detail": json.dumps(flags)}


def run(dataset: Path, lock: Path, *, python: str | None, allowed: tuple[str, ...], max_injection_rate: float,
        require_offline: bool) -> dict:
    corpus, personal = check_corpus(dataset, allowed, max_injection_rate)
    checks = [check_supply_chain(lock), check_venv(python), check_manifest(dataset), corpus, personal,
              check_offline(require_offline)]
    verdict = "PASS" if all(c["ok"] for c in checks) else "FAIL"
    return {"verdict": verdict, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "dataset": str(dataset),
            "lock": str(lock), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--lock", required=True)
    ap.add_argument("--venv", default=None, help="python.exe of the training venv")
    ap.add_argument("--allow-source", action="append", default=None)
    ap.add_argument("--for-weights", action="store_true",
                    help="dataset will change weights: only owner-provenance sources (WEIGHTS_SOURCES) are allowed")
    ap.add_argument("--max-injection-rate", type=float, default=0.0)
    ap.add_argument("--require-offline", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    dataset = Path(a.dataset).expanduser()
    if not dataset.is_dir():
        print(f"usage: dataset dir does not exist: {dataset}", file=sys.stderr)
        return 2
    report = run(dataset, Path(a.lock).expanduser(), python=a.venv,
                 allowed=tuple(a.allow_source) if a.allow_source else (WEIGHTS_SOURCES if a.for_weights
                                                                       else DEFAULT_SOURCES),
                 max_injection_rate=a.max_injection_rate, require_offline=a.require_offline)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    for c in report["checks"]:
        print(f"{'PASS' if c['ok'] else 'FAIL':4s} {c['name']:13s} {c['detail']}")
    print(f"VERDICT {report['verdict']}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
