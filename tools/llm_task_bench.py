"""Measure any OpenAI-compatible model on small coding tasks checked by REAL unit tests (pass@1, latency, tokens).

Synthetic tasks only - no Bossman data, chats or photos are ever sent. Generated code runs in a temp dir with a scrubbed
environment (no keys) and a timeout. The API key is read from the environment (or the Bossman provider-keys.env) and is
never printed or stored in the result file.

  python tools/llm_task_bench.py --model minimax/minimax-m3.1-flash:free --key-env OPENROUTER_API_KEY --out mm.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SYSTEM = "You are a senior Python engineer. Reply with ONE ```python code block containing the complete solution module and nothing else."

TASKS = [
    ("money_round",
     "Write Python `quote(amount: str, rate: str, margin: str, side: str) -> str` using decimal.Decimal only. side='sell' means the client "
     "receives amount*rate*(1-margin) rounded DOWN to 2 decimals; side='buy' means the client pays amount*rate*(1+margin) rounded UP to "
     "2 decimals. Return the result as a string with exactly 2 decimals. Raise ValueError for negative amount, margin outside [0, 0.1], "
     "or unknown side.",
     r"""
from sol import quote
import pytest
assert quote('1000','25','0.03','sell') == '24250.00'
assert quote('1000','25','0.03','buy') == '25750.00'
assert quote('1','0.333','0','sell') == '0.33'
assert quote('1','0.331','0','buy') == '0.34'
assert quote('0.1','3','0','buy') == '0.30'
for bad in [('-1','25','0.03','sell'),('1','25','0.2','buy'),('1','25','0.03','swap')]:
    with pytest.raises(ValueError): quote(*bad)
"""),
    ("tqdm_parse",
     "Write Python `parse(line: str) -> dict | None` that parses a tqdm progress line like '  37%|###7      | 11/30 [01:02<01:47,  5.60s/it]' "
     "or '100%|##########| 30/30 [02:48<00:00, 5.61s/it]'. Return {'step': int, 'total': int, 'pct': int, 'eta_s': int} where eta_s is the "
     "remaining time (the part after '<') in seconds (support mm:ss and h:mm:ss). Return None for lines that are not tqdm bars, and also "
     "None for download bars whose rate unit ends with 'B/s'.",
     r"""
from sol import parse
assert parse('  37%|###7      | 11/30 [01:02<01:47,  5.60s/it]') == {'step':11,'total':30,'pct':37,'eta_s':107}
assert parse('100%|##########| 30/30 [02:48<00:00, 5.61s/it]') == {'step':30,'total':30,'pct':100,'eta_s':0}
assert parse(' 12%|#2 | 3/25 [00:10<1:01:13, 3.35it/s]')['eta_s'] == 3673
assert parse('Loading model weights...') is None
assert parse(' 45%|####5 | 450M/1.00G [00:05<00:06, 90.1MB/s]') is None
"""),
    ("lru_ttl",
     "Write a Python class `TTLCache(maxsize: int, ttl: float, clock=time.monotonic)` with get(key, default=None) and set(key, value). Entries "
     "expire ttl seconds after set; the least-recently-used entry (by get or set) is evicted when size exceeds maxsize; expired entries must "
     "never be returned. Thread-safe with a lock.",
     r"""
from sol import TTLCache
t=[0.0]
c=TTLCache(2, 10, clock=lambda: t[0])
c.set('a',1); c.set('b',2); assert c.get('a')==1
c.set('c',3); assert c.get('b') is None and c.get('a')==1 and c.get('c')==3
t[0]=11; assert c.get('a') is None and c.get('c','x')=='x'
c.set('d',4); t[0]=20.9; assert c.get('d')==4; t[0]=21.1; assert c.get('d') is None
"""),
    ("safe_path",
     "Write Python `safe_join(base: str, user_path: str) -> pathlib.Path` that joins a user-supplied relative path under base and raises "
     "ValueError if the result escapes base (via '..', absolute paths, drive letters like 'C:\\x', or UNC paths). Work on Windows using "
     "os.path and return the resolved path.",
     r"""
import os, pytest, tempfile, pathlib
from sol import safe_join
b = tempfile.mkdtemp()
assert safe_join(b, 'a/b.txt') == pathlib.Path(b, 'a', 'b.txt').resolve()
for bad in ['../x', 'a/../../x', os.path.abspath(os.sep) + 'etc', 'C:\\Windows', '\\\\srv\\share\\x']:
    with pytest.raises(ValueError): safe_join(b, bad)
"""),
]


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", text or "", re.S)
    return max(blocks, key=len) if blocks else (text or "").strip()


def run_check(code: str, check: str, timeout: float = 30.0) -> tuple[bool, str]:
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PATHEXT", "COMSPEC")
    with tempfile.TemporaryDirectory() as d:
        Path(d, "sol.py").write_text(code, encoding="utf-8")
        Path(d, "check.py").write_text(check, encoding="utf-8")
        try:
            p = subprocess.run([sys.executable, "check.py"], cwd=d, capture_output=True, text=True, timeout=timeout,
                               env={k: v for k, v in os.environ.items() if k in keep})
        except subprocess.TimeoutExpired:
            return False, "timeout"
    return p.returncode == 0, (p.stderr or p.stdout).strip()[-300:]


def resolve_key(env_name: str) -> str | None:
    if os.environ.get(env_name):
        return os.environ[env_name]
    base = Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "CommandCenter")
    for f in (base / "provider-keys.env", base.parent / "provider-keys.env", base.parent / "keys" / "provider-keys.env"):
        if f.is_file():
            for ln in f.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                k, sep, v = ln.partition("=")
                if sep and k.strip() == env_name and v.strip():
                    return v.strip().strip('"').strip("'")
    return None


def make_chat(base_url: str, model: str, key: str, max_tokens: int, temperature: float, timeout: float = 300.0,
              reasoning_max_tokens: int = 0):
    def chat(messages: list[dict]):
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if reasoning_max_tokens > 0:
            payload["reasoning"] = {"max_tokens": reasoning_max_tokens}      # OpenRouter unified reasoning control
        body = json.dumps(payload).encode()
        for attempt in range(5):
            req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body, headers={
                "Authorization": "Bearer " + key, "Content-Type": "application/json", "X-Title": "Bossman task bench"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 - owner-chosen https API
                    d = json.loads(r.read().decode())
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 502, 503) and attempt < 4:
                    time.sleep(15 * (attempt + 1))
                    continue
                raise RuntimeError(f"HTTP {e.code}") from None
        if not d.get("choices"):                                        # provider error body with HTTP 200
            raise RuntimeError(str((d.get("error") or d))[:160])
        ch = d["choices"][0]
        return ch["message"].get("content") or "", d.get("usage") or {}, ch.get("finish_reason")
    return chat


def _one(name: str, prompt: str, check: str, i: int, chat, clock) -> dict:
    t0 = clock()
    try:
        text, usage, finish = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
        ok, note = run_check(extract_code(text), check)
        err = None
    except Exception as e:  # noqa: BLE001
        usage, finish, ok, note, err = {}, None, False, "", str(e)[:100]
    return {"task": name, "sample": i, "passed": ok, "latency_s": round(clock() - t0, 1),
            "tokens_in": usage.get("prompt_tokens"), "tokens_out": usage.get("completion_tokens"),
            "finish": finish, "note": note if not ok else "", "error": err}


def run_bench(tasks, chat, samples: int = 1, clock=time.perf_counter, workers: int = 1) -> dict:
    jobs = [(name, prompt, check, i) for name, prompt, check in tasks for i in range(samples)]
    if workers > 1:
        with ThreadPoolExecutor(workers) as ex:
            rows = list(ex.map(lambda j: _one(*j, chat, clock), jobs))
    else:
        rows = [_one(*j, chat, clock) for j in jobs]
    n = len(rows)
    return {"rows": rows, "n": n, "passed": sum(r["passed"] for r in rows),
            "pass_rate": round(sum(r["passed"] for r in rows) / n, 3) if n else None,
            "avg_latency_s": round(sum(r["latency_s"] for r in rows) / n, 1) if n else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    ap.add_argument("--key-env", default="OPENROUTER_API_KEY")
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=24000)
    ap.add_argument("--reasoning-max-tokens", type=int, default=6000, help="0 = do not send a reasoning budget")
    ap.add_argument("--suite", choices=["core", "extended", "all"], default="core")
    ap.add_argument("--workers", type=int, default=1, help="parallel requests (keep small: provider rate limits)")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    key = resolve_key(a.key_env)
    if not key:
        print(f"no key: set {a.key_env} or add it to provider-keys.env", file=sys.stderr)
        return 2
    try:
        from tools.llm_bench_tasks import TASKS_EXTENDED            # repo root on sys.path (pytest, -m)
    except ModuleNotFoundError:                                    # run as a script: python tools/llm_task_bench.py
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from llm_bench_tasks import TASKS_EXTENDED
    tasks = {"core": TASKS, "extended": TASKS_EXTENDED, "all": TASKS + TASKS_EXTENDED}[a.suite]
    res = run_bench(tasks, make_chat(a.base_url, a.model, key, a.max_tokens, a.temperature,
                                     reasoning_max_tokens=a.reasoning_max_tokens), a.samples, workers=a.workers)
    res.update(model=a.model, base_url=a.base_url, suite=a.suite, max_tokens=a.max_tokens,
               reasoning_max_tokens=a.reasoning_max_tokens, ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
    for r in res["rows"]:
        print(f"{r['task']:12s} {'PASS' if r['passed'] else 'FAIL'} {r['latency_s']:6.1f}s in={r['tokens_in']} out={r['tokens_out']} "
              f"{r['finish']} {r['error'] or r['note'][:80]}")
    print(f"{a.model}: {res['passed']}/{res['n']} passed, avg {res['avg_latency_s']}s")
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
