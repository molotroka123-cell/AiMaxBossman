#!/usr/bin/env python3
"""Local-model learning lab: baseline vs candidate on a HELD-OUT admin-triage set.

* baseline  = local model + policy prompt, no learned examples;
* candidate = the same model + the same policy + approved privacy-safe examples
  loaded from the local learning folder (``learning-data/rc19``).
The held-out set is disjoint from the examples (checked at load). Scoring is
deterministic (exact business/intent/action + safety rules). Nothing is
fine-tuned, nothing is promoted: the report only recommends.

    python tools/owner_journeys/learning_lab.py export-examples
    python tools/owner_journeys/learning_lab.py ab --runner bcc --repeats 2 \
        --out C:\\Users\\asd\\Bossman\\evidence\\rc19\\d\\learning\\ab-report.json
    python tools/owner_journeys/learning_lab.py models --out ...\\models.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.owner_journeys import triage_dataset as ds  # noqa: E402
from tools.owner_journeys.runtime_guard import lower_priority, wait_if_paused  # noqa: E402

OLLAMA = "http://127.0.0.1:11434"
DEFAULT_MODEL = "bossman-fast-qwen36-35b-a3b-q5:latest"
LEARNING_DIR = Path(r"C:\Users\asd\Bossman\learning-data\rc19")
EXAMPLES_FILE = "admin_triage_examples.jsonl"


# ------------------------------------------------------------------ examples

def _norm(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


def examples_digest(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def export_examples(folder: Path = LEARNING_DIR) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / EXAMPLES_FILE
    rows = [{**e, "approved": True, "privacy": "FAKE_NO_PII", "source": "triage_dataset.EXAMPLES",
             "approved_by": "workstream-D review (fake data only)"} for e in ds.EXAMPLES]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (folder / (EXAMPLES_FILE + ".sha256")).write_text(examples_digest(rows) + "\n", encoding="utf-8")
    return path


def load_examples(folder: Path = LEARNING_DIR) -> list[dict[str, Any]]:
    path = folder / EXAMPLES_FILE
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    sha_file = folder / (EXAMPLES_FILE + ".sha256")
    if sha_file.is_file() and sha_file.read_text().strip() != examples_digest(rows):
        raise ValueError("approved examples changed after approval (sha mismatch)")
    bad = [r for r in rows if not r.get("approved") or r.get("privacy") != "FAKE_NO_PII"]
    if bad:
        raise ValueError(f"{len(bad)} examples are not approved/privacy-safe")
    assert_disjoint(rows, ds.HELD_OUT)
    return rows


def assert_disjoint(examples: list[dict[str, Any]], held_out: list[dict[str, Any]]) -> None:
    ex = {_norm(e["text"]) for e in examples}
    clash = [h["text"] for h in held_out if _norm(h["text"]) in ex]
    if clash:
        raise ValueError(f"held-out overlaps examples: {clash[:3]}")


def system_prompt(variant: str, examples: list[dict[str, Any]]) -> str:
    if variant == "baseline":
        return ds.POLICY
    shots = "\n".join(
        f"Message: {e['text']}\nAnswer: " + json.dumps({k: e[k] for k in ds.FIELDS}, ensure_ascii=False)
        for e in examples)
    return ds.POLICY + "\n\nApproved examples (follow the same conventions):\n" + shots


def parse_answer(text: str) -> Optional[dict[str, Any]]:
    raw = (text or "").strip()
    a, b = raw.find("{"), raw.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        obj = json.loads(raw[a:b + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


# ------------------------------------------------------------------ runners

class OllamaRunner:
    name = "ollama-direct"

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def ask(self, variant: str, system: str, text: str) -> tuple[str, float]:
        def call():
            wait_if_paused()
            body = {"model": self.model, "stream": False, "think": False, "format": "json", "keep_alive": "10m",
                    "options": {"temperature": 0},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}]}
            req = urllib.request.Request(f"{OLLAMA}/api/chat", data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            t = time.time()
            with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310 - loopback Ollama
                data = json.loads(resp.read().decode())
            return str((data.get("message") or {}).get("content") or ""), time.time() - t
        return await asyncio.to_thread(call)


class BccRunner:
    """Each item is one bcc task on the product engine (agent without tools)."""

    name = "bcc-task-path"

    def __init__(self, data_dir: Path, systems: dict[str, str], model: str = DEFAULT_MODEL):
        from tools.owner_journeys.bcc_harness import BccHarness, local_adapter_factory

        self.h = BccHarness(data_dir, adapter_factory=local_adapter_factory())
        self.systems = systems
        self.model = model
        self.agents: dict[str, int] = {}

    async def __aenter__(self):
        await self.h.__aenter__()
        for variant, system in self.systems.items():
            agent = await self.h.agent(name=f"triage-{variant}", system_prompt=system, tools=[],
                                       model_name=self.model, max_steps=2)
            self.agents[variant] = agent["id"]
        return self

    async def __aexit__(self, *exc):
        await self.h.__aexit__(*exc)

    async def ask(self, variant: str, system: str, text: str) -> tuple[str, float]:
        out = await self.h.run_task(agent_id=self.agents[variant], title=f"triage-{variant}",
                                    prompt=f"Inbound message:\n{text}\nReturn only the JSON.",
                                    allowed_tools=[], timeout=300)
        return out.result, out.seconds


# ------------------------------------------------------------------ evaluation

def _pct(values: list[float], q: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(round(q * (len(v) - 1))))], 2)


async def evaluate(runner, variant: str, system: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for i, item in enumerate(items):
        try:
            text, dt = await runner.ask(variant, system, item["text"])
            err = None
        except Exception as exc:  # noqa: BLE001
            text, dt, err = "", 0.0, type(exc).__name__
        got = parse_answer(text)
        sc = ds.score(item, got)
        rows.append({"i": i, "text": item["text"], "expected": {k: item[k] for k in ds.FIELDS}, "got": got,
                     "exact": sc["exact"], "fields": sc["fields"], "safety_ok": sc["safety_ok"],
                     "latency_s": round(dt, 2), "error": err})
    lat = [r["latency_s"] for r in rows if r["error"] is None]
    return {
        "variant": variant, "n": len(rows), "exact": sum(r["exact"] for r in rows),
        "accuracy": round(sum(r["exact"] for r in rows) / max(1, len(rows)), 4),
        "field_accuracy": {f: round(sum(r["fields"][f] for r in rows) / max(1, len(rows)), 4) for f in ds.FIELDS},
        "safety_violations": [r["text"] for r in rows if not r["safety_ok"]],
        "parse_failures": sum(r["got"] is None for r in rows), "errors": sum(r["error"] is not None for r in rows),
        "latency_s": {"p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95), "mean": round(statistics.mean(lat), 2)
                      if lat else None},
        "failed_cases": [{k: r[k] for k in ("text", "expected", "got")} for r in rows if not r["exact"]],
        "rows": rows,
    }


def compare(base: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    b = {r["text"]: r["exact"] for r in base["rows"]}
    c = {r["text"]: r["exact"] for r in cand["rows"]}
    fixed = [t for t in b if not b[t] and c.get(t)]
    broke = [t for t in b if b[t] and not c.get(t)]
    return {"delta_exact": cand["exact"] - base["exact"], "fixed": fixed, "broken": broke,
            "discordant": len(fixed) + len(broke),
            "safety_regression": len(cand["safety_violations"]) > len(base["safety_violations"])}


async def run_ab(runner_kind: str, repeats: int, data_root: Path, model: str) -> dict[str, Any]:
    examples = load_examples()
    systems = {"baseline": system_prompt("baseline", []), "candidate": system_prompt("candidate", examples)}
    items = list(ds.HELD_OUT)
    runs = []
    for rep in range(repeats):
        if runner_kind == "bcc":
            from tools.owner_journeys.bcc_harness import fresh_dir
            runner = BccRunner(fresh_dir(data_root, f"lab-rep{rep + 1}"), systems, model)
        else:
            runner = OllamaRunner(model)
        async with runner:
            base = await evaluate(runner, "baseline", systems["baseline"], items)
            cand = await evaluate(runner, "candidate", systems["candidate"], items)
        runs.append({"repeat": rep + 1, "baseline": base, "candidate": cand, "comparison": compare(base, cand)})
        print(json.dumps({"repeat": rep + 1, "baseline": base["exact"], "candidate": cand["exact"], "n": len(items),
                          "safety_base": len(base["safety_violations"]),
                          "safety_cand": len(cand["safety_violations"])}), flush=True)
    gains = [r["comparison"]["delta_exact"] for r in runs]
    no_safety_regression = not any(r["comparison"]["safety_regression"] for r in runs)
    repeatable_gain = all(g > 0 for g in gains) and len(runs) >= 2
    min_gain_items = 3  # below this the difference is within run-to-run noise for n≈50
    if repeatable_gain and no_safety_regression and min(gains) >= min_gain_items:
        marker = "MEASURED_GAIN"
    else:
        marker = "NOT_PROVEN"
    return {
        "lab": "admin-inbox triage (fake, privacy-safe)", "runner": runner_kind, "model": model,
        "held_out_n": len(items), "examples_n": len(examples), "examples_file": str(LEARNING_DIR / EXAMPLES_FILE),
        "examples_sha256": examples_digest([json.loads(x) for x in (LEARNING_DIR / EXAMPLES_FILE)
                                            .read_text(encoding="utf-8").splitlines() if x.strip()]),
        "disjoint_check": "PASS", "repeats": runs, "gains_per_repeat": gains,
        "no_safety_regression": no_safety_regression, "cost_usd": 0.0,
        "suggested_marker": marker, "promotion": "FORBIDDEN_AUTOMATIC; owner decision only",
        "fine_tuning": "NOT_ATTEMPTED (Ollama does not train; LoRA/ROCm not proven on this machine)",
    }


def model_inventory() -> dict[str, Any]:
    def get(path, body=None):
        req = urllib.request.Request(f"{OLLAMA}{path}", data=None if body is None else json.dumps(body).encode(),
                                     method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    tags = get("/api/tags").get("models", [])
    out = []
    for m in tags:
        show = get("/api/show", {"model": m["name"]})
        modelfile = str(show.get("modelfile") or "")
        header = [ln for ln in modelfile.splitlines() if ln.startswith(("FROM", "# FROM", "PARAMETER"))][:8]
        out.append({"name": m["name"], "digest": m.get("digest"), "size_bytes": m.get("size"),
                    "modified_at": m.get("modified_at"), "family": (m.get("details") or {}).get("family"),
                    "parameter_size": (m.get("details") or {}).get("parameter_size"),
                    "quantization": (m.get("details") or {}).get("quantization_level"),
                    "capabilities": show.get("capabilities"), "modelfile_header": header})
    ver = get("/api/version").get("version")
    try:
        cli = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except OSError:
        cli = None
    return {"ollama_version": ver, "ollama_cli": cli, "endpoint": OLLAMA, "models": out}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export-examples")
    ab = sub.add_parser("ab")
    ab.add_argument("--runner", choices=("bcc", "ollama"), default="bcc")
    ab.add_argument("--repeats", type=int, default=2)
    ab.add_argument("--model", default=DEFAULT_MODEL)
    ab.add_argument("--data-root", default=r"C:\Users\asd\Bossman\rc19-data\d-learn\lab")
    ab.add_argument("--out", default=r"C:\Users\asd\Bossman\evidence\rc19\d\learning\ab-report.json")
    mo = sub.add_parser("models")
    mo.add_argument("--out", default=r"C:\Users\asd\Bossman\evidence\rc19\d\learning\models.json")
    args = ap.parse_args(argv)
    if args.cmd == "export-examples":
        print(export_examples())
        return 0
    if args.cmd == "models":
        inv = model_inventory()
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(inv, indent=2), encoding="utf-8")
        print(json.dumps({"models": len(inv["models"]), "ollama": inv["ollama_version"]}))
        return 0
    print(json.dumps({"priority": lower_priority()}), flush=True)
    started = time.time()
    rep = asyncio.run(run_ab(args.runner, args.repeats, Path(args.data_root), args.model))
    rep["runtime_s"] = round(time.time() - started, 1)
    rep["command"] = " ".join(["python", "tools/owner_journeys/learning_lab.py"] + list(argv or sys.argv[1:]))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"marker": rep["suggested_marker"], "gains": rep["gains_per_repeat"], "out": args.out}))
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
