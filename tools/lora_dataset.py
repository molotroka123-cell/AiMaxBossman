"""tools/lora_dataset.py — Bossman LoRA/QLoRA dataset "database" (stdlib only + the harvester import).

    python tools/lora_dataset.py build [--data-dir <BCC data>] [--root C:/Users/asd/Bossman/train-snapshots]
    python tools/lora_dataset.py gate <dataset_id> --lock requirements-train.lock [--require-offline --for-weights]
    python tools/lora_dataset.py list | verify <dataset_id>

Drafted 23.10 by the GLM 5.3 Flash cloud worker (Bossman task 199, from a spec), finished and tested by Claude.

Pipeline (the local training loop, WEIGHTS_UNCHANGED until the owner explicitly gates a dataset):

  1. harvest    reuse the collectors/filters of bcc.pit.master_parser.training_harvester
                (_collect_jeff_kb, _collect_training_drops, _collect_web_learn_candidates,
                _collect_war_terminal_and_news; injection drop via _is_injected; redaction via
                _clean_and_redact; dedup by sha256 of casefolded text) — WITHOUT the 16k packing.
                Rows shorter than --min-chars are dropped; rows are capped by --token-budget.
  2. snapshot   write an immutable, content-addressed dataset snapshot under --root:
                corpus.jsonl (id/source/topic/text/sha256/tokens), train.jsonl + holdout.jsonl
                (chat SFT rows), cpt.jsonl (continued-pretraining rows, train ids only) and a
                manifest.json listing every data file with sha256 + bytes (schema
                "bossman.lora-dataset/1"). Every file is made read-only (stat.S_IREAD).
  3. gate       run tools/training_secure_gate.py as a subprocess over the snapshot; the gate
                report is written OUTSIDE the dataset dir (<root>/<id>.gate.json) so the
                manifest stays exact; verdict + report sha256 are recorded in registry.json.
  4. train      (out of scope here) LoRA/QLoRA fine-tune locally on train.jsonl + cpt.jsonl,
                offline, against the hash-pinned requirements.lock the gate validated.
  5. eval       score the fine-tuned model on holdout.jsonl (rows never seen in training).

Registry: <root>/registry.json is a JSON list of per-dataset rows (counts + hashes only).
Rules: dataset text is NEVER printed or logged by this tool — only counts, ids and hashes.
All writes are atomic (temp file + os.replace, fsync). Exit codes: 0 ok, 1 failure, 2 usage error.
Windows paths are used throughout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCHEMA = "bossman.lora-dataset/1"
DATA_FILES = ("corpus.jsonl", "train.jsonl", "holdout.jsonl", "cpt.jsonl")
DEFAULT_HOLDOUT = 0.1
DEFAULT_TOKEN_BUDGET = 2_000_000
DEFAULT_MIN_CHARS = 40
FALLBACK_TOPIC = "Расскажи по теме"


# --------------------------------------------------------------------------- helpers

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _import_harvester():
    """Import the Bossman training harvester (command-center/bcc/pit/master_parser)."""
    cc = _repo_root() / "command-center"
    if cc.is_dir() and str(cc) not in sys.path:
        sys.path.insert(0, str(cc))
    from bcc.pit.master_parser import training_harvester as th  # noqa: PLC0415
    return th


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(path: Path, data: str) -> None:
    """Atomic write: temp file in the same dir, fsync, os.replace (Windows-safe)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _make_read_only(path: Path) -> None:
    try:
        os.chmod(str(path), stat.S_IREAD)
    except OSError:
        pass


def _registry_path(root: Path) -> Path:
    return root / "registry.json"


def _load_registry(root: Path) -> list[dict[str, Any]]:
    p = _registry_path(root)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save_registry(root: Path, rows: list[dict[str, Any]]) -> None:
    _atomic_write(_registry_path(root), json.dumps(rows, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def _upsert_registry(root: Path, row: dict[str, Any]) -> None:
    reg = _load_registry(root)
    reg = [r for r in reg if r.get("dataset_id") != row["dataset_id"]]
    reg.append(row)
    reg.sort(key=lambda r: str(r.get("dataset_id") or ""))
    _save_registry(root, reg)


# --------------------------------------------------------------------------- harvest

def _harvest_rows(data_dir: Path, *, min_chars: int, token_budget: int) -> tuple[list[dict[str, Any]], int, int]:
    """Collect + sanitize + dedup rows (no packing). Returns (rows, dropped_injections, budget_skipped)."""
    th = _import_harvester()
    raw_items = (
        th._collect_jeff_kb(data_dir)
        + th._collect_training_drops(data_dir)
        + th._collect_web_learn_candidates(data_dir)
        + th._collect_war_terminal_and_news(data_dir)
    )

    dropped_injections = 0
    seen_text: set[str] = set()
    seen_ids: set[str] = set()
    rows: list[dict[str, Any]] = []

    for item in raw_items:
        raw_text = str(item.get("text") or "")
        if th._is_injected(raw_text) or th._is_injected(str(item.get("topic") or "")):
            dropped_injections += 1
            continue
        clean_text = th._clean_and_redact(raw_text)
        if len(clean_text) < max(1, int(min_chars)):
            continue
        norm_sha = hashlib.sha256(clean_text.casefold().encode("utf-8")).hexdigest()
        if norm_sha in seen_text:
            continue
        seen_text.add(norm_sha)
        ref = str(item.get("ref") or f"row:{len(rows) + 1}")
        row_id = ref
        n = 1
        while row_id in seen_ids:
            n += 1
            row_id = f"{ref}:{n}"
        seen_ids.add(row_id)
        rows.append({
            "id": row_id,
            "source": str(item.get("source") or "unknown"),
            "topic": str(item.get("topic") or "")[:120],
            "text": clean_text,
            "sha256": norm_sha,
            "tokens": th._estimate_tokens(clean_text),
        })

    rows.sort(key=lambda r: (r["source"], r["id"]))
    budget_skipped = 0
    if token_budget > 0:
        used = 0
        kept: list[dict[str, Any]] = []
        for r in rows:
            if used + r["tokens"] > int(token_budget):
                budget_skipped += 1
                continue
            used += r["tokens"]
            kept.append(r)
        rows = kept
    return rows, dropped_injections, budget_skipped


def _dataset_id(rows: list[dict[str, Any]], created_at: str) -> str:
    stamp = created_at.replace("-", "").replace(":", "").replace("T", "-").replace("Z", "")
    blob = "\n".join(sorted(r["sha256"] for r in rows)).encode("utf-8")
    return "ds-" + stamp + "-" + hashlib.sha256(blob).hexdigest()[:8]


# --------------------------------------------------------------------------- build

def cmd_build(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir).expanduser()
    root = Path(args.root).expanduser()
    if not data_dir.is_dir():
        print(f"usage: data dir does not exist: {data_dir}", file=sys.stderr)
        return 2
    if not 0.0 <= args.holdout < 1.0:
        print("usage: --holdout must be in [0, 1)", file=sys.stderr)
        return 2

    rows, dropped_injections, budget_skipped = _harvest_rows(
        data_dir, min_chars=args.min_chars, token_budget=args.token_budget)
    if not rows:
        print("build: no rows survived filtering; nothing to snapshot", file=sys.stderr)
        return 1

    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ds_id = _dataset_id(rows, created_at)
    ds_dir = root / ds_id

    holdout_bucket = int(args.holdout * 1000)
    source_counts: dict[str, int] = {}
    train_rows: list[str] = []
    holdout_rows: list[str] = []
    cpt_rows: list[str] = []

    for r in rows:
        source_counts[r["source"]] = source_counts.get(r["source"], 0) + 1
        prompt = r["topic"] if r["topic"] else FALLBACK_TOPIC
        sft = {"id": r["id"], "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": r["text"]},
        ]}
        is_holdout = int(r["sha256"][:8], 16) % 1000 < holdout_bucket
        if is_holdout:
            holdout_rows.append(json.dumps(sft, ensure_ascii=False, sort_keys=True))
        else:
            train_rows.append(json.dumps(sft, ensure_ascii=False, sort_keys=True))
            cpt_rows.append(json.dumps({"id": r["id"], "text": r["text"]}, ensure_ascii=False, sort_keys=True))

    corpus_rows = [
        json.dumps({"id": r["id"], "source": r["source"], "topic": r["topic"], "text": r["text"],
                    "sha256": r["sha256"], "tokens": r["tokens"]}, ensure_ascii=False, sort_keys=True)
        for r in rows
    ]
    files_payload: dict[str, str] = {
        "corpus.jsonl": "\n".join(corpus_rows) + ("\n" if corpus_rows else ""),
        "train.jsonl": "\n".join(train_rows) + ("\n" if train_rows else ""),
        "holdout.jsonl": "\n".join(holdout_rows) + ("\n" if holdout_rows else ""),
        "cpt.jsonl": "\n".join(cpt_rows) + ("\n" if cpt_rows else ""),
    }

    if ds_dir.exists():
        print(f"build: dataset dir already exists: {ds_dir}", file=sys.stderr)
        return 1
    files_meta: list[dict[str, Any]] = []
    for name in DATA_FILES:
        p = ds_dir / name
        _atomic_write(p, files_payload[name])
        files_meta.append({"path": name, "sha256": _sha256_file(p), "bytes": p.stat().st_size})

    manifest = {
        "schema": SCHEMA,
        "dataset_id": ds_id,
        "created_at": created_at,
        "source_counts": source_counts,
        "rows": len(rows),
        "train_rows": len(train_rows),
        "holdout_rows": len(holdout_rows),
        "dropped_injections": dropped_injections,
        "files": files_meta,
    }
    manifest_path = ds_dir / "manifest.json"
    _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n")

    # snapshot immutable: read-only every data file + the manifest itself
    for name in DATA_FILES + ("manifest.json",):
        _make_read_only(ds_dir / name)

    _upsert_registry(root, {
        "dataset_id": ds_id,
        "created_at": created_at,
        "rows": len(rows),
        "train_rows": len(train_rows),
        "holdout_rows": len(holdout_rows),
        "source_counts": source_counts,
        "manifest_sha256": _sha256_file(manifest_path),
        "gate": None,
    })

    print(f"dataset_id={ds_id}")
    print(f"rows={len(rows)} train={len(train_rows)} holdout={len(holdout_rows)} "
          f"dropped_injections={dropped_injections} skipped_token_budget={budget_skipped}")
    print("source_counts=" + json.dumps(source_counts, ensure_ascii=False, sort_keys=True))
    print(f"dir={ds_dir}")
    return 0


# --------------------------------------------------------------------------- gate

def cmd_gate(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    ds_dir = root / args.dataset_id
    if not ds_dir.is_dir():
        print(f"usage: dataset dir does not exist: {ds_dir}", file=sys.stderr)
        return 2
    gate_script = _repo_root() / "tools" / "training_secure_gate.py"
    if not gate_script.is_file():
        print(f"gate: script not found: {gate_script}", file=sys.stderr)
        return 2

    # gate report OUTSIDE the dataset dir so the manifest stays exact
    report_path = root / f"{args.dataset_id}.gate.json"
    cmd = [sys.executable, str(gate_script),
           "--dataset", str(ds_dir),
           "--out", str(report_path),
           "--lock", str(Path(args.lock).expanduser())]
    if args.venv:
        cmd += ["--venv", str(Path(args.venv).expanduser())]
    if args.require_offline:
        cmd += ["--require-offline"]
    if args.allow_source:
        for src in args.allow_source:
            cmd += ["--allow-source", src]
    if args.max_injection_rate is not None:
        cmd += ["--max-injection-rate", str(args.max_injection_rate)]
    if args.for_weights:
        cmd += ["--for-weights"]

    proc = subprocess.run(cmd, cwd=str(_repo_root()), check=False)  # gate prints counts/ids only
    verdict = "FAIL"
    report_sha = ""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        verdict = str(report.get("verdict", "FAIL"))
    except (OSError, ValueError):
        pass
    if report_path.is_file():
        report_sha = _sha256_file(report_path)

    reg = _load_registry(root)
    for row in reg:
        if row.get("dataset_id") == args.dataset_id:
            row["gate"] = {"verdict": verdict, "report_sha256": report_sha,
                           "report_path": str(report_path),
                           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "exit_code": proc.returncode}
            break
    _save_registry(root, reg)

    print(f"dataset_id={args.dataset_id} verdict={verdict} report={report_path}")
    if report_sha:
        print(f"report_sha256={report_sha}")
    return 0 if verdict == "PASS" else 1


# --------------------------------------------------------------------------- list / verify

def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    reg = _load_registry(root)
    if not reg:
        print("(registry empty)")
        return 0
    for row in reg:
        gate = row.get("gate") or {}
        print(f"{row.get('dataset_id', '?'):30s} created={row.get('created_at', '?')} "
              f"rows={row.get('rows', 0)} train={row.get('train_rows', 0)} holdout={row.get('holdout_rows', 0)} "
              f"manifest={str(row.get('manifest_sha256', ''))[:12]} gate={gate.get('verdict') or 'null'}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    ds_dir = root / args.dataset_id
    mpath = ds_dir / "manifest.json"
    if not ds_dir.is_dir() or not mpath.is_file():
        print(f"usage: dataset or manifest missing: {mpath}", file=sys.stderr)
        return 2
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        entries = {e["path"]: e for e in manifest["files"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"verify: manifest unreadable: {type(exc).__name__}", file=sys.stderr)
        return 2

    on_disk = {p.relative_to(ds_dir).as_posix() for p in ds_dir.rglob("*") if p.is_file()}
    mismatched: list[str] = []
    for rel, meta in sorted(entries.items()):
        p = ds_dir / rel
        if not p.is_file():
            mismatched.append(f"missing:{rel}")
        elif _sha256_file(p) != meta.get("sha256") or p.stat().st_size != meta.get("bytes"):
            mismatched.append(f"changed:{rel}")
    for rel in sorted(on_disk - set(entries) - {"manifest.json"}):
        mismatched.append(f"extra:{rel}")

    if mismatched:
        for m in mismatched:
            print(f"MISMATCH {m}")
        return 1
    print(f"verify: OK {len(entries)} files match manifest ({args.dataset_id})")
    return 0


# --------------------------------------------------------------------------- CLI

def _default_data_dir() -> str:
    return os.environ.get("BCC_DATA_DIR") or str(Path(os.environ.get("LOCALAPPDATA", ".")) / "Bossman" / "CommandCenter")


DEFAULT_ROOT = "C:/Users/asd/Bossman/train-snapshots"   # outside git, owner ACL (TRAINING_WORKFLOW_SECURE_20261023_RU)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bossman LoRA/QLoRA dataset database (harvest -> snapshot -> gate -> train -> eval)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="harvest + immutable snapshot + registry row")
    b.add_argument("--data-dir", default=_default_data_dir())
    b.add_argument("--root", default=DEFAULT_ROOT)
    b.add_argument("--holdout", type=float, default=DEFAULT_HOLDOUT)
    b.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    b.add_argument("--min-chars", type=int, default=DEFAULT_MIN_CHARS)
    g = sub.add_parser("gate", help="run tools/training_secure_gate.py on a snapshot, record the verdict")
    g.add_argument("dataset_id")
    g.add_argument("--root", default=DEFAULT_ROOT)
    g.add_argument("--lock", required=True)
    g.add_argument("--venv", default=None)
    g.add_argument("--require-offline", action="store_true")
    g.add_argument("--for-weights", action="store_true")
    g.add_argument("--allow-source", action="append", default=[])
    g.add_argument("--max-injection-rate", type=float, default=None)
    ls = sub.add_parser("list", help="registry rows (counts and hashes only)")
    ls.add_argument("--root", default=DEFAULT_ROOT)
    v = sub.add_parser("verify", help="recompute sha256 of a snapshot against its manifest")
    v.add_argument("dataset_id")
    v.add_argument("--root", default=DEFAULT_ROOT)
    args = ap.parse_args(argv)
    return {"build": cmd_build, "gate": cmd_gate, "list": cmd_list, "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
