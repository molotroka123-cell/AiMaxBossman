#!/usr/bin/env python3
"""Local model bake-off A–G: the same seven prompts for every model, graded by execution.

Ported from the owner's lab bake-off of 2026-09-22 into the shipped tools so
that ``Owner-Run.cmd self-improve-mvcr compare`` runs it from the archive alone: standard library only (no httpx), proxies from the
environment are ignored (the endpoint is loopback), and the model's code runs in
a throw-away directory under ``python -I`` with a timeout.

The prompts and the graders are unchanged from the lab run, so a result from the
archive is comparable with the lab table (GPT-OSS-120B 7/7, Qwen3.8-27B Q5/Q4 6/7).

    python model_bakeoff.py --url http://127.0.0.1:8081/v1 --tag main-q5 --out results/ --json

Each task is PASS only when its check passes: task A and C execute the model's
code, D and F inspect the tool call it made, E parses its JSON, B and G look for
one exact answer. A harness or HTTP error is a FAIL of that task with the error
written next to it — never a silent skip. The tool measures; it does not choose
a production model and it changes no weights.

Exit codes: 0 — the run finished (whatever the score), 2 — the endpoint gave no
model to test, 1 — the harness could not run at all.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

BUGGY = '''def moving_sum(xs, k):
    """Sum of every window of k consecutive items."""
    out = []
    for i in range(len(xs) - k):
        out.append(sum(xs[i:i + k]))
    return out
'''

TREE = """command-center/bcc/engine.py            task engine: claims runs, calls models, tool loop
command-center/bcc/registry.py          providers/models registry, model test probe
command-center/bcc/health.py            health snapshot of models/providers
command-center/bcc/studio/runtime.py    Studio model listing and generation planes
command-center/bcc/telegram_companion/service.py   Telegram bot dispatch
command-center/bcc/telegram_companion/agent_bridge.py  /claude and /codex from Telegram
command-center/bcc/v2/memory/lifecycle_wiring.py   memory recall at task start
bossman-core/bossman/toolkit/files.py   fs.write / fs.edit for agents
tools/windows_bundle_lock.py            Windows build input lock
"""

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file from the repo",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "run_tests", "description": "Run pytest on a path",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "k": {"type": "string"}},
                    "required": ["path"]}}},
    {"type": "function", "function": {"name": "search_code", "description": "Search the repository for a regex",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}}},
]

Chat = Callable[..., dict]


def utf8_console() -> None:
    """Печать не имеет права падать на кириллице (раннеры идут под `-I`)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, io.UnsupportedOperation):
            pass


def code_block(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text or "", flags=re.S)
    return max(blocks, key=len) if blocks else (text or "")


def run_py(src: str, timeout: float = 30) -> tuple[int, str]:
    """Model-written code runs isolated: temp dir, `-I`, a hard timeout."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.py"
        p.write_text(src, encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, "-I", str(p)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout, cwd=d)
        except subprocess.TimeoutExpired:
            return 124, f"timeout after {timeout}s"
        return r.returncode, (r.stdout + r.stderr)[-400:]


def task_a(chat: Chat) -> tuple[bool, str]:
    ans = chat([{"role": "user", "content": "Find the bug and return ONLY the corrected function in one python code "
                 "block.\n\n```python\n" + BUGGY + "```"}])
    code = code_block(ans["text"])
    test = code + ("\nassert moving_sum([1,2,3,4], 2) == [3,5,7]\nassert moving_sum([5], 1) == [5]\n"
                   "assert moving_sum([], 3) == []\nprint('ok')\n")
    rc, out = run_py(test)
    return rc == 0, out


def task_b(chat: Chat) -> tuple[bool, str]:
    ans = chat([{"role": "user", "content": "Repository map:\n" + TREE + "\nWhich single file should be changed so "
                 "that a model that thinks before answering is not marked silent by the model test? Answer with the "
                 "path only."}])
    return "bcc/registry.py" in (ans["text"] or ""), (ans["text"] or "")[:120]


def task_c(chat: Chat) -> tuple[bool, str]:
    ans = chat([{"role": "user", "content": "The function below is buggy. Return ONE python code block containing: "
                 "the fixed function AND a function `test_regression()` that fails on the buggy version and passes "
                 "on the fixed one (plain asserts, no pytest import).\n\n```python\n" + BUGGY + "```"}])
    code = code_block(ans["text"])
    if "def test_regression" not in code:
        return False, "no test_regression"
    rc_fixed, out1 = run_py(code + "\ntest_regression()\nprint('ok')\n")
    tests_only = re.sub(r"def moving_sum\(.*?\n(?=def |\Z)", "", code, flags=re.S)
    rc_buggy, _ = run_py(BUGGY + "\n" + tests_only + "\ntest_regression()\n")
    return rc_fixed == 0 and rc_buggy != 0, f"fixed_rc={rc_fixed} buggy_rc={rc_buggy} {out1[-120:]}"


def task_d(chat: Chat) -> tuple[bool, str]:
    ans = chat([{"role": "user", "content": "Run only the tests in command-center/tests/test_health_model_components.py."}],
               tools=TOOLS)
    calls = ans["tool_calls"]
    ok = bool(calls) and calls[0]["function"]["name"] == "run_tests" and \
        "test_health_model_components" in calls[0]["function"].get("arguments", "")
    return ok, json.dumps(calls)[:200]


def task_e(chat: Chat) -> tuple[bool, str]:
    ans = chat([{"role": "user", "content": "Return ONLY a JSON object (no prose) with keys: file (string), line "
                 "(integer), severity (one of low|medium|high), fix (string), for this finding: moving_sum in "
                 "utils.py line 4 skips the last window; high severity; fix the range bound."}])
    txt = (ans["text"] or "").strip()
    txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
    try:
        d = json.loads(txt)
        ok = (isinstance(d, dict) and set(d) == {"file", "line", "severity", "fix"} and isinstance(d["line"], int)
              and d["severity"] in ("low", "medium", "high"))
        return ok, txt[:160]
    except ValueError:
        return False, f"invalid json: {txt[:120]}"


def task_f(chat: Chat) -> tuple[bool, str]:
    msgs = [{"role": "user", "content": "Read the file command-center/bcc/health.py."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": json.dumps({"path": "bcc/health.py"})}}]},
            {"role": "tool", "tool_call_id": "c1",
             "content": "ERROR: file not found: bcc/health.py (paths are relative to the repository root)"}]
    ans = chat(msgs, tools=TOOLS)
    calls = ans["tool_calls"]
    ok = bool(calls) and calls[0]["function"]["name"] == "read_file" and \
        "command-center/bcc/health.py" in calls[0]["function"].get("arguments", "")
    return ok, json.dumps(calls)[:200] if calls else (ans["text"] or "")[:120]


def g_prompt() -> str:
    filler = [f"Record {i}: service node-{i % 37} reported status nominal at tick {i * 7}." for i in range(700)]
    filler.insert(430, "Record 430: the rollback password for the lab build is AMBER-4471.")
    return "\n".join(filler) + "\n\nWhat is the rollback password for the lab build? Answer with the password only."


def task_g(chat: Chat) -> tuple[bool, str]:
    ans = chat([{"role": "user", "content": g_prompt()}])
    return "AMBER-4471" in (ans["text"] or ""), (ans["text"] or "")[:80]


TASKS: dict[str, Callable[[Chat], tuple[bool, str]]] = {
    "A_bug": task_a, "B_repo_nav": task_b, "C_patch_regression": task_c, "D_tool_select": task_d,
    "E_json": task_e, "F_tool_recovery": task_f, "G_context": task_g}


def _opener() -> urllib.request.OpenerDirector:
    # Loopback endpoint: a proxy from the environment only lies about it.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(url: str, payload: dict | None, timeout: float) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                          "Accept": "application/json"})
    with _opener().open(req, timeout=timeout) as resp:  # noqa: S310 — loopback model server
        return json.loads(resp.read().decode("utf-8", "replace") or "null")


def make_chat(url: str, model: str, stats: list[dict], *, timeout: float, max_tokens: int) -> Chat:
    def chat(messages: list[dict], tools: list[dict] | None = None) -> dict:
        body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
        if tools:
            body["tools"] = tools
        t = time.time()
        r = _request(url + "/chat/completions", body, timeout)
        m = r["choices"][0]["message"]
        tm = r.get("timings") or {}
        stats.append({"s": round(time.time() - t, 1), "gen_tps": tm.get("predicted_per_second"),
                      "pp_tps": tm.get("prompt_per_second"), "out": (r.get("usage") or {}).get("completion_tokens")})
        return {"text": m.get("content") or "", "tool_calls": m.get("tool_calls") or []}
    return chat


def latency_probe(url: str, model: str, *, timeout: float, server_pid: int | None = None) -> dict:
    """One extra streamed request. Refusals are visible; no inferred TTFT/RAM."""
    body = {"model": model, "messages": [{"role": "user", "content": "Reply with one word: ready"}],
            "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 16, "temperature": 0}
    started = time.monotonic()
    request = urllib.request.Request(url + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    memory: dict[str, Any] = {"server_pid": server_pid, "peak_process_rss_bytes": None,
                              "note": "process RSS, not AMD GPU allocation or system peak"}
    stopped = threading.Event()
    sampler = None
    if server_pid is not None:
        try:
            import psutil  # noqa: PLC0415 — optional; explicit UNMEASURED if unavailable
            proc = psutil.Process(server_pid)
            def sample() -> None:
                while True:
                    try:
                        rss = proc.memory_info().rss
                        memory["peak_process_rss_bytes"] = max(memory["peak_process_rss_bytes"] or 0, rss)
                    except psutil.Error:
                        break
                    if stopped.wait(0.05):
                        break
            sampler = threading.Thread(target=sample, daemon=True)
            sampler.start()
        except Exception as exc:  # noqa: BLE001 — optional measurement must not fail the bake-off
            memory["unmeasured_reason"] = type(exc).__name__
    try:
        first_ms = None
        tokens = 0
        prompt_tps = None
        received = 0
        with _opener().open(request, timeout=timeout) as response:
            if "text/event-stream" not in response.headers.get("Content-Type", ""):
                raise ValueError("model did not return an SSE stream")
            for line in response:
                received += len(line)
                if received > 1_048_576:
                    raise ValueError("stream exceeded 1 MiB")
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    break
                obj = json.loads(payload)
                if isinstance(obj.get("timings"), dict):
                    prompt_tps = obj["timings"].get("prompt_per_second") or prompt_tps
                delta = (((obj.get("choices") or [{}])[0]).get("delta") or {})
                if first_ms is None and any(delta.get(k) for k in ("content", "reasoning", "tool_calls")):
                    first_ms = round((time.monotonic() - started) * 1000, 1)
                tokens += int((obj.get("usage") or {}).get("completion_tokens") or 0)
        return {"status": "MEASURED" if first_ms is not None else "NO_TOKEN_EVENT", "ttft_ms": first_ms,
                "prefill_tps_reported": prompt_tps, "prefill_note": "provider timings only; null if omitted",
                "stream_elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                "usage_completion_tokens_reported": tokens or None, "memory": memory}
    except (OSError, ValueError, KeyError, IndexError, urllib.error.URLError) as exc:
        return {"status": "UNMEASURED", "reason": f"{type(exc).__name__}: {str(exc)[:180]}",
                "ttft_ms": None, "prefill_tps_reported": None, "memory": memory}
    finally:
        stopped.set()
        if sampler is not None:
            sampler.join(timeout=1)


def run_tasks(chat: Chat, stats: list[dict], *, tag: str = "", log: Callable[[str], None] = print) -> dict:
    results: dict[str, dict] = {}
    for name, fn in TASKS.items():
        n = len(stats)
        try:
            ok, note = fn(chat)
        except Exception as exc:  # noqa: BLE001 — one broken answer is that task's FAIL, not the run's
            ok, note = False, f"harness/model error: {type(exc).__name__}: {str(exc)[:120]}"
        results[name] = {"pass": bool(ok), "note": note, "calls": stats[n:]}
        log(f"{tag} {name}: {'PASS' if ok else 'FAIL'} {[c['s'] for c in stats[n:]]}")
    return results


def summarize(tag: str, model: str, url: str, results: dict, stats: list[dict]) -> dict:
    tps = sorted(c["gen_tps"] for c in stats if c.get("gen_tps"))
    return {"schema": "bossman.model_bakeoff.v1", "tag": tag, "model": model, "url": url,
            "passed": sum(r["pass"] for r in results.values()), "total": len(TASKS),
            "seconds": round(sum(c["s"] for c in stats), 1),
            "gen_tps_median": tps[len(tps) // 2] if tps else None,
            "failed": [k for k, r in results.items() if not r["pass"]],
            "results": results, "weights_unchanged": True}


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="OpenAI-compatible base, e.g. http://127.0.0.1:8081/v1")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=None, help="model id (default: the first one /models lists)")
    ap.add_argument("--timeout", type=float, default=900.0, help="seconds per request")
    ap.add_argument("--max-tokens", type=int, default=3000)
    ap.add_argument("--json", action="store_true", help="print the summary as one JSON line on stdout")
    ap.add_argument("--latency-probe", action="store_true", help="one extra streaming request for measured TTFT")
    ap.add_argument("--server-pid", type=int, help="optional server PID for process RSS sampling during the probe")
    a = ap.parse_args(argv)
    url = a.url.rstrip("/")
    log = (lambda line: print(line, file=sys.stderr, flush=True)) if a.json else (lambda line: print(line, flush=True))
    model = a.model
    if not model:
        try:
            listing = _request(url + "/models", None, 30)
            model = str(((listing or {}).get("data") or [{}])[0].get("id") or "")
        except (urllib.error.URLError, OSError, ValueError, TypeError, IndexError, AttributeError) as exc:
            print(json.dumps({"tag": a.tag, "status": "NO_MODEL", "error": f"{type(exc).__name__}: {exc}"}))
            return 2
        if not model:
            print(json.dumps({"tag": a.tag, "status": "NO_MODEL", "error": "the endpoint lists no model"}))
            return 2
    stats: list[dict] = []
    results = run_tasks(make_chat(url, model, stats, timeout=a.timeout, max_tokens=a.max_tokens), stats,
                        tag=a.tag, log=log)
    summary = summarize(a.tag, model, url, results, stats)
    if a.latency_probe:
        summary["latency_probe"] = latency_probe(url, model, timeout=min(a.timeout, 120), server_pid=a.server_pid)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.tag}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    brief = {k: summary[k] for k in ("tag", "model", "passed", "total", "seconds", "gen_tps_median", "failed")}
    brief["status"] = "DONE"
    brief["result_file"] = str(out / f"{a.tag}.json")
    print(json.dumps(brief, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
