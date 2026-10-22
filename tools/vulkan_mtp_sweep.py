#!/usr/bin/env python3
"""Controlled Vulkan + MTP & Prompt Cache A/B Sweep (`CP-PERF-02`).

Executes `C:\\Users\\asd\\Bossman\\llama-vulkan\\llama-cli.exe` against local Qwen GGUF
models on AMD Radeon 8060S (Vulkan) when Resource Governor is IDLE, without
replacing or disrupting the live Ollama `:11434` daemon.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

LLAMA_CLI = Path(r"C:\Users\asd\Bossman\llama-vulkan\llama-cli.exe")
QWEN38_27B_Q4 = Path(r"C:\Users\asd\Bossman\models\qwen38-27b\Qwen3.8-27B-UD-Q4_K_M.gguf")
QWEN36_35B_A3B = Path(r"C:\Users\asd\Bossman\models\qwen36-35b-a3b\Qwen3.6-35B-A3B-UD-Q5_K_XL.gguf")

PERF_LINE_RE = re.compile(r"\[\s*Prompt:\s*([0-9.]+)\s*t/s\s*\|\s*Generation:\s*([0-9.]+)\s*t/s\s*\]")


def _check_governor_idle() -> dict[str, Any]:
    r = subprocess.run(
        [sys.executable, "tools/resource_governor/governor.py", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        data = json.loads(r.stdout.strip())
    except Exception:
        data = {"phase": "UNKNOWN", "live_jobs": 0}
    if int(data.get("live_jobs") or 0) > 0:
        raise RuntimeError(f"Aborting Vulkan sweep: live media job active ({data})")
    return data


def _run_case(
    *,
    label: str,
    model_path: Path,
    extra_args: list[str],
    prompt: str,
    n_predict: int = 48,
) -> dict[str, Any]:
    cmd = [
        str(LLAMA_CLI),
        "-m",
        str(model_path),
        "-ngl",
        "99",
        "-c",
        "2048",
        "-n",
        str(n_predict),
        "-st",
        "--no-display-prompt",
        "--temp",
        "0",
        "-p",
        prompt,
        *extra_args,
    ]
    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    wall_s = time.perf_counter() - t0
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    m = PERF_LINE_RE.search(combined)
    prompt_tps = float(m.group(1)) if m else 0.0
    gen_tps = float(m.group(2)) if m else 0.0
    # Check if llama.cpp logged any MTP / draft message
    mtp_notes = [
        line.strip()
        for line in combined.splitlines()
        if any(k in line.lower() for k in ("mtp", "draft", "speculative", "vulkan", "acceptance", "cache"))
    ][:12]
    return {
        "label": label,
        "model": model_path.name,
        "model_size_gb": round(model_path.stat().st_size / 1e9, 2),
        "extra_args": extra_args,
        "n_predict": n_predict,
        "exit_code": proc.returncode,
        "wall_time_s": round(wall_s, 3),
        "prompt_eval_tps": prompt_tps,
        "decode_tps": gen_tps,
        "notes": mtp_notes,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    gov_before = _check_governor_idle()
    prompt = (
        "Return a compact JSON object with keys status, tool, and command to check git status "
        "and run pytest in a Python repository without explanation:"
    )

    cases: list[dict[str, Any]] = []
    if QWEN38_27B_Q4.is_file():
        for label, extra in [
            ("qwen38_27b_q4_mtp_off", ["--spec-type", "none"]),
            ("qwen38_27b_q4_draft_mtp_n1", ["--spec-type", "draft-mtp", "--spec-draft-n-max", "1"]),
            ("qwen38_27b_q4_draft_mtp_n2", ["--spec-type", "draft-mtp", "--spec-draft-n-max", "2"]),
            ("qwen38_27b_q4_draft_mtp_n3", ["--spec-type", "draft-mtp", "--spec-draft-n-max", "3"]),
            ("qwen38_27b_q4_ngram_cache", ["--spec-type", "ngram-cache", "--cache-ram", "8192"]),
        ]:
            _check_governor_idle()
            res = _run_case(label=label, model_path=QWEN38_27B_Q4, extra_args=extra, prompt=prompt, n_predict=48)
            cases.append(res)

    if QWEN36_35B_A3B.is_file():
        for label, extra in [
            ("qwen36_35b_a3b_q5_mtp_off", ["--spec-type", "none"]),
            ("qwen36_35b_a3b_q5_draft_mtp_n2", ["--spec-type", "draft-mtp", "--spec-draft-n-max", "2"]),
        ]:
            _check_governor_idle()
            res = _run_case(label=label, model_path=QWEN36_35B_A3B, extra_args=extra, prompt=prompt, n_predict=48)
            cases.append(res)

    gov_after = _check_governor_idle()
    base_decode = next((c["decode_tps"] for c in cases if c["label"] == "qwen38_27b_q4_mtp_off"), 0.0)
    for c in cases:
        c["speedup_vs_qwen38_27b_mtp_off_x"] = (
            round(c["decode_tps"] / base_decode, 3) if base_decode > 0 and c["decode_tps"] > 0 else 0.0
        )

    doc = {
        "schema": "bossman.perf2x.vulkan_mtp_sweep/1",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "llama_cli_path": str(LLAMA_CLI),
        "llama_build": "b11223-4da633776",
        "device": "AMD Radeon(TM) 8060S Graphics (128 GB UMA, Vulkan)",
        "governor_before": {
            "phase": gov_before.get("phase"),
            "live_jobs": gov_before.get("live_jobs"),
            "ram_available_gb": gov_before.get("ram_available_gb"),
            "cache_gb": gov_before.get("cache_gb"),
        },
        "governor_after": {
            "phase": gov_after.get("phase"),
            "live_jobs": gov_after.get("live_jobs"),
            "ram_available_gb": gov_after.get("ram_available_gb"),
            "cache_gb": gov_after.get("cache_gb"),
        },
        "cases": cases,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(cases, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
