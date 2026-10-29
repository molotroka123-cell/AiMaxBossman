"""Coding swarm for Bossman 2.5 (owner 25.10): one task -> Kimi K3.1 Pro + Qwen 3.8 Flash + free models, answers saved.

    python tools/coding_swarm.py review --doc docs/owner/X.md --ask "..." --out <dir>
    python tools/coding_swarm.py task --ask "..." --file path [--file path ...] --out <dir> [--models kimi,qwen,...]
    python tools/coding_swarm.py limits

Chat only: repository text goes in the message, never personal data; every prompt is scanned for secret-like strings
first and refused on a hit. Keys are read in-process (7API from the Bossman vault, the rest from provider-keys.env)
and never printed. Each call records model, seconds, tokens and the cost from the published per-token price.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("tma", ROOT / "tools" / "three_model_audit.py")
tma = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tma)

# name: (base url, model, key source, usd per Mtok in, usd per Mtok out)
MODELS = {
    "kimi": ("https://7api.st/v1", "kimi-k3.1-pro", "7api", 0.10, 0.10),
    "qwen": ("https://openrouter.ai/api/v1", "qwen/qwen3.8-flash", "OPENROUTER_API_KEY", 0.15, 0.47),
    "glm": ("https://7api.st/v1", "glm-5.3-flash-uncensored", "7api", 0.028, 0.028),
    "nemotron": ("https://openrouter.ai/api/v1", "nvidia/nemotron-3-ultra-550b-a55b:free", "OPENROUTER_API_KEY", 0.0, 0.0),
    # the same Nemotron Ultra on the NVIDIA NIM free tier (provider 7 in bcc.db); used when OpenRouter :free queues
    "nemotron-nim": ("https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-ultra-550b-a55b", "NVIDIA_API_KEY", 0.0, 0.0),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3-flash-preview", "GEMINI_API_KEY", 0.0, 0.0),
    # TZ 2.5 M3: a free provider out of its daily limit falls back to the local model, never silently to a paid one
    "local": ("http://127.0.0.1:11434/v1", "qwen3-coder:30b", "local", 0.0, 0.0),
}
DEFAULT = ["kimi", "qwen", "nemotron", "gemini"]
REASONING = {"kimi": {"reasoning_effort": "medium"}, "qwen": {"reasoning": {"max_tokens": 6000}}}
MAX_PROMPT_CHARS = 120_000


# ------------------------------------------------------------------ TZ 2.5 M3: ContextSanitizer (before every cloud call)

_SECRET_EXTRA = re.compile(r"(sk-or-v1-[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}"
                           r"|(?i:(?:api[_-]?key|token|secret|password)\s*[=:]\s*['\"][A-Za-z0-9_\-]{20,}['\"]))")
# Repository files that never leave the PC even though git tracks them (or could): keys, vault, env, tailnet, databases.
_DENY_PATH = re.compile(r"(^|/)(\.env[^/]*|[^/]*vault[^/]*|[^/]*secret[^/]*|[^/]*\.pem|[^/]*\.key|provider-keys[^/]*|"
                        r"[^/]*tailscale[^/]*|[^/]*tailnet[^/]*|[^/]*\.db|[^/]*\.sqlite[^/]*|keys)(/|$)", re.I)
# Stored in pieces so this very file can be sent to the swarm without matching its own list.
_PERSONAL_HINTS = tuple("".join(parts) for parts in (("Греч", "ка"), ("lora-", "datasets-"), ("voice-", "train-"),
                                                     ("\\datasets", "\\gf"), ("/datasets", "/gf"), ("telegram-", "companion"),
                                                     ("companion", ".env")))
# Personal-but-incidental strings in source code are redacted, not refused: home paths, env-var homes, e-mail, LAN/tailnet IPs.
_REDACT = [
    (re.compile(r"\b[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'`]+", re.I), "<HOME>"),
    (re.compile(r"/(?:home|Users)/[^/\s\"'`]+"), "<HOME>"),
    (re.compile(r"%(?:LOCALAPPDATA|APPDATA|USERPROFILE)%", re.I), "<APPDATA>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<EMAIL>"),
    (re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
                r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})\b"), "<LAN_IP>"),
]


class SanitizerRefusal(SystemExit):
    """Raised before any network call; the message lists every reason (never the matched secret itself)."""


class ContextSanitizer:
    """Path allowlist from `git ls-files`, secret scan, personal-data redaction/refusal, EVAL500 holdout guard."""

    def __init__(self, root: Path = ROOT, tracked: set[str] | None = None):
        self.root = root.resolve()
        self._tracked = tracked

    @property
    def tracked(self) -> set[str]:
        if self._tracked is None:
            out = subprocess.run(["git", "-C", str(self.root), "ls-files", "-z"], capture_output=True, check=True).stdout
            self._tracked = {p for p in out.decode("utf-8", "replace").split("\0") if p}
        return self._tracked

    def check_path(self, path: str) -> str:
        """Return the repository-relative posix path, or refuse (outside the repo, untracked, or a denied file)."""
        p = Path(path)
        full = (p if p.is_absolute() else self.root / p).resolve()
        try:
            rel = full.relative_to(self.root).as_posix()
        except ValueError:
            raise SanitizerRefusal(f"file outside the repository: refused ({path})") from None
        if _DENY_PATH.search(rel):
            raise SanitizerRefusal(f"protected file (keys/vault/env/tailnet/db): refused ({rel})")
        if rel not in self.tracked:
            raise SanitizerRefusal(f"file not tracked by git (not on the allowlist): refused ({rel})")
        return rel

    def read_file(self, path: str) -> tuple[str, str]:
        rel = self.check_path(path)
        return rel, (self.root / rel).read_text(encoding="utf-8")

    def sanitize(self, text: str) -> tuple[str, dict]:
        reasons = []
        if tma.SECRET.search(text) or _SECRET_EXTRA.search(text):
            reasons.append("secret-like string")
        folded = text.casefold()
        hits = [h for h in _PERSONAL_HINTS if h.casefold() in folded]
        if hits:
            reasons.append("personal-data location: " + ", ".join(sorted(set(hits))))
        if leaks_eval_holdout(text):
            reasons.append("EVAL500 sealed holdout text or location (TZ 2.5 M5)")
        if reasons:
            raise SanitizerRefusal("prompt refused by ContextSanitizer: " + "; ".join(reasons))
        counts = {}
        for rx, repl in _REDACT:
            text, n = rx.subn(repl, text)
            if n:
                counts[repl] = counts.get(repl, 0) + n
        return text, {"redacted": counts}


# ------------------------------------------------------------------ TZ 2.5 M3: provider limits (free -> local, paid -> skip)

STATE_DIR = Path(os.environ.get("BOSSMAN_SWARM_STATE", r"D:\Bossman\work\coding-swarm"))
FREE_DAILY_CALLS = {"nemotron": 50, "nemotron-nim": 40, "gemini": 250}
PAID_DAILY_USD = {"qwen": 2.0}
PAID_TOTAL_USD = {"7api": 5.0}        # the 7API sub-key (kimi + glm) is a $5 budget in total


def _ledger_path() -> Path:
    return STATE_DIR / "limits.json"


def _load_ledger() -> dict:
    try:
        led = json.loads(_ledger_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        led = None
    if not isinstance(led, dict):
        return {"days": {}, "totals": {}}
    return {"days": led.get("days") if isinstance(led.get("days"), dict) else {},
            "totals": led.get("totals") if isinstance(led.get("totals"), dict) else {}}


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def plan_models(models: list[str], ledger: dict | None = None) -> tuple[list[str], list[dict]]:
    """Apply the limits before calling: exhausted free models become `local`, exhausted paid models are dropped."""
    ledger = ledger if ledger is not None else _load_ledger()
    day = ledger.get("days", {}).get(_today(), {})
    planned, notes = [], []
    for m in models:
        used = day.get(m, {})
        if m in FREE_DAILY_CALLS and used.get("calls", 0) >= FREE_DAILY_CALLS[m]:
            notes.append({"model": m, "action": "fallback_local", "reason": f"free daily limit {FREE_DAILY_CALLS[m]} calls"})
            m = "local"
        elif m in PAID_DAILY_USD and used.get("usd", 0.0) >= PAID_DAILY_USD[m]:
            notes.append({"model": m, "action": "skipped", "reason": f"paid daily cap ${PAID_DAILY_USD[m]}"})
            continue
        elif MODELS.get(m, ("", "", ""))[2] == "7api" and ledger.get("totals", {}).get("7api", 0.0) >= PAID_TOTAL_USD["7api"]:
            notes.append({"model": m, "action": "skipped", "reason": f"7API budget ${PAID_TOTAL_USD['7api']} used up"})
            continue
        if m not in planned:
            planned.append(m)
    return planned, notes


def limits_report() -> dict:
    """Read-only report of today's ledger and the remaining free/paid allowances (patch by Kimi, reviewed by Qwen +
    Nemotron, integrated by Claude 29.10 — the T5 swarm-flow proof)."""
    ledger = _load_ledger()
    date = _today()
    day = ledger["days"].get(date) or {}
    totals = ledger["totals"]
    remaining = {m: {"calls": max(0, n - (day.get(m) or {}).get("calls", 0))} for m, n in FREE_DAILY_CALLS.items()}
    remaining.update({m: {"usd": round(max(0.0, n - (day.get(m) or {}).get("usd", 0.0)), 6)} for m, n in PAID_DAILY_USD.items()})
    remaining.update({m: {"usd": round(max(0.0, n - totals.get(m, 0.0)), 6)} for m, n in PAID_TOTAL_USD.items()})
    return {"date": date, "today": day, "7api_total_usd": totals.get("7api", 0.0), "remaining": remaining}


def record_usage(results: list[dict]) -> dict:
    ledger = _load_ledger()
    day = ledger.setdefault("days", {}).setdefault(_today(), {})
    for r in results:
        if r.get("error") and not r.get("tokens_in"):
            continue                      # a refused/failed connection is not a provider call
        row = day.setdefault(r["model"], {"calls": 0, "usd": 0.0})
        row["calls"] += 1
        row["usd"] = round(row["usd"] + float(r.get("cost_usd") or 0.0), 6)
        if MODELS.get(r["model"], ("", "", ""))[2] == "7api":
            tot = ledger.setdefault("totals", {})
            tot["7api"] = round(tot.get("7api", 0.0) + float(r.get("cost_usd") or 0.0), 6)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _ledger_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, _ledger_path())
    return ledger


def leaks_eval_holdout(prompt: str) -> bool:
    """TZ 2.5 M5: a sealed EVAL500 holdout scenario never reaches a coder/swarm/auditor model."""
    import sys as _sys
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in _sys.path:
        _sys.path.insert(0, tools_dir)
    from eval500.holdout_guard import leaks_holdout
    return leaks_holdout(prompt)


def _call(name: str, prompt: str, max_tokens: int = 24000, timeout: float = 900) -> dict:
    # 24k: Kimi K3.1 Pro and Qwen 3.8 Flash spent all of 8k on reasoning and returned no content (25.10)
    base, model, src, pin, pout = MODELS[name]
    t0 = time.time()
    try:
        key = "ollama" if src == "local" else tma._seven_api_key() if src == "7api" else tma._env_keys().get(src, "")
        if not key:
            raise RuntimeError("no key")
        body = {"model": model, "stream": True, "max_tokens": max_tokens, "temperature": 0.2,
                "messages": [{"role": "user", "content": prompt}]}
        if "openrouter" in base:
            body["stream_options"] = {"include_usage": True}
        # 25.10: on real coding prompts Kimi and Qwen burned the whole output cap on reasoning and answered nothing.
        # Measured on a short prompt: Qwen reasoning.max_tokens cuts thinking ~14x; Kimi reasoning_effort lowers it
        # and Kimi's output ceiling is 32k. Reasoning is still collected (owner wants Kimi's traces for the dataset).
        body.update(REASONING.get(name, {}))
        if name == "kimi":
            body["max_tokens"] = max(max_tokens, 32000)
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                              "User-Agent": "Bossman/1.0", "X-Title": "bossman-coding-swarm"})
        parts, thinking, usage = [], [], {}
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                try:
                    obj = json.loads(line[5:])
                except ValueError:
                    continue
                usage = obj.get("usage") or usage
                for ch in obj.get("choices") or []:
                    delta = ch.get("delta") or {}
                    parts.append(delta.get("content") or "")
                    thinking.append(delta.get("reasoning_content") or delta.get("reasoning") or "")
        text = "".join(parts)
        tin = int(usage.get("prompt_tokens") or len(prompt) // 4)
        tout = int(usage.get("completion_tokens") or len(text) // 4)
        return {"model": name, "id": model, "ok": bool(text.strip()), "seconds": round(time.time() - t0, 1),
                "tokens_in": tin, "tokens_out": tout, "tokens_estimated": not usage,
                "reasoning_chars": len("".join(thinking)), "reasoning": "".join(thinking),
                "cost_usd": round(tin / 1e6 * pin + tout / 1e6 * pout, 6), "text": text}
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
        return {"model": name, "id": model, "ok": False, "seconds": round(time.time() - t0, 1), "cost_usd": 0.0,
                "error": type(exc).__name__ + ": " + str(exc)[:200]}


# Owner 25.10: «если боссману доступен thinking process Kimi — пусть учится у него». Reasoning traces are kept LOCALLY
# (never in git) with provenance; using them for training waits for a check of the provider terms (Moonshot / 7API).
DATASET = Path(r"D:\Bossman\datasets\cloud-reasoning")


TRAINING_USE = {"PENDING_TERMS_CHECK", "ALLOWED_BY_TERMS", "FORBIDDEN"}
M4_FIELDS = ("id", "ts", "provider", "model_id", "task_id", "source_sha", "prompt_sha256", "prompt", "reasoning", "answer",
             "verifier_outcome", "cost_usd", "training_use", "terms_ref")


def _source_sha() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def validate_row(row: dict) -> None:
    """TZ 2.5 M4: a dataset row without a valid training_use, missing a field, or carrying secrets/personal data is refused."""
    missing = [f for f in M4_FIELDS if f not in row]
    if missing:
        raise ValueError(f"dataset row missing fields: {missing}")
    if row["training_use"] not in TRAINING_USE:
        raise ValueError(f"dataset row has invalid training_use: {row['training_use']!r}")
    if row["training_use"] == "ALLOWED_BY_TERMS" and not str(row.get("terms_ref") or "").strip():
        raise ValueError("ALLOWED_BY_TERMS needs terms_ref (link to the provider terms text)")
    blob = "\n".join(str(row.get(k) or "") for k in ("prompt", "reasoning", "answer"))
    try:
        _, rep = ContextSanitizer(tracked=set()).sanitize(blob)
    except SanitizerRefusal as exc:
        raise ValueError(f"dataset row refused: {exc}") from None
    if rep["redacted"]:
        raise ValueError(f"dataset row carries personal data: {sorted(rep['redacted'])}")


def _save_reasoning(prompt: str, results: list[dict], label: str) -> dict:
    """M4 rows for every call that returned reasoning; prompt is the SANITIZED prompt. Returns {written, refused}."""
    sha, ts = _source_sha(), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    written, refused = 0, []
    for r in results:
        if not r.get("reasoning"):
            continue
        row = {"id": uuid.uuid4().hex, "ts": ts, "provider": urllib.parse.urlsplit(MODELS[r["model"]][0]).hostname or "",
               "model_id": r["id"], "task_id": label, "source_sha": sha,
               "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "prompt": prompt,
               "reasoning": r["reasoning"], "answer": r.get("text", ""), "verifier_outcome": "NOT_RUN",
               "cost_usd": float(r.get("cost_usd") or 0.0), "training_use": "PENDING_TERMS_CHECK", "terms_ref": ""}
        try:
            validate_row(row)
        except ValueError as exc:
            refused.append({"model": r["model"], "reason": str(exc)[:200]})
            continue
        DATASET.mkdir(parents=True, exist_ok=True)
        with open(DATASET / f"{time.strftime('%Y-%m-%d')}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        written += 1
    return {"written": written, "refused": refused}


def export_training_rows(rows: list[dict]) -> list[dict]:
    """The only way out of the dataset into a training set. Refuses the whole export while ANY row is not cleared by
    the provider terms (TZ 2.5 M4: no training while PENDING_TERMS_CHECK); output stays local, never in git."""
    blocked = [r.get("id") for r in rows if r.get("training_use") != "ALLOWED_BY_TERMS" or not str(r.get("terms_ref") or "").strip()]
    if blocked:
        raise PermissionError(f"training export refused: {len(blocked)} row(s) not cleared by provider terms "
                              "(training_use must be ALLOWED_BY_TERMS with terms_ref, set only after the owner's decision)")
    for r in rows:
        validate_row(r)
    return [{"prompt": r["prompt"], "reasoning": r["reasoning"], "answer": r["answer"], "source_id": r["id"]} for r in rows]


def run(prompt: str, models: list[str], out: Path, label: str, sanitizer: ContextSanitizer | None = None) -> dict:
    prompt, san = (sanitizer or ContextSanitizer(tracked=set())).sanitize(prompt)   # refuses before any call
    if len(prompt) > MAX_PROMPT_CHARS:
        raise SystemExit(f"prompt too long ({len(prompt)} > {MAX_PROMPT_CHARS})")
    unknown = [m for m in models if m not in MODELS]
    if unknown:
        raise SystemExit(f"unknown models: {unknown}")
    models, limit_notes = plan_models(models)
    if not models:
        raise SystemExit("every requested model is over its limit; nothing called: " + json.dumps(limit_notes))
    with ThreadPoolExecutor(max_workers=len(models)) as ex:
        results = list(ex.map(lambda m: _call(m, prompt), models))
    record_usage(results)
    out.mkdir(parents=True, exist_ok=True)
    ds = _save_reasoning(prompt, results, label)
    for r in results:
        (out / f"{label}-{r['model']}.md").write_text(r.get("text") or r.get("error", ""), encoding="utf-8")
    doc = {"schema": "bossman.coding-swarm.v2", "label": label, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "prompt_chars": len(prompt), "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
           "sanitizer": san, "limits": limit_notes, "dataset": ds,
           "calls": [{k: v for k, v in r.items() if k not in ("text", "reasoning")} for r in results],
           "cost_usd_total": round(sum(r.get("cost_usd", 0) for r in results), 6)}
    (out / f"{label}-receipt.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["review", "task", "limits"])
    ap.add_argument("--ask")
    ap.add_argument("--doc")
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--models", default=",".join(DEFAULT))
    ap.add_argument("--out")
    ap.add_argument("--label", default="")
    a = ap.parse_args(argv)
    if a.mode == "limits":
        print(json.dumps(limits_report(), ensure_ascii=False))
        return 0
    if a.ask is None or a.out is None:
        ap.error("--ask and --out are required for review/task")
    paths = ([a.doc] if a.doc else []) + a.file
    san = ContextSanitizer()
    files = [san.read_file(p) for p in paths]        # allowlist: tracked repository files only, never keys/env/vault/db
    ctx = "".join(f"\n\n### {rel}\n```\n{text}\n```" for rel, text in files)
    doc = run(a.ask + ctx, a.models.split(","), Path(a.out), a.label or a.mode, sanitizer=san)
    print(json.dumps([(c["model"], c["ok"], c["seconds"], c.get("cost_usd")) for c in doc["calls"]] +
                     [("total_usd", doc["cost_usd_total"])], ensure_ascii=False))
    return 0 if any(c["ok"] for c in doc["calls"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
