"""Generate SYNTHETIC Czech dental-clinic copy for the site emulator and cache it as a fixture.

Run once by hand (``python -m tools.site_audit.gen_texts``); tests never call a model -- they read the
cached JSON. Providers are tried in the owner's order: free OpenRouter Nemotron Ultra -> Gemini 3 Flash ->
GLM 5.3 Flash (Bossman agent 46, chat-only) -> free Nemotron 3.5 -> built-in templates. Every batch records
which provider produced it.

Safety: prompts are static synthetic text, scanned for secret-like patterns before sending; keys/token are
read from the owner's files inside this process and never printed or written anywhere. The output contains
no person names, phones or e-mails -- the emulator adds placeholder contacts itself.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "site_audit" / "dental_texts.json"
SECRET = re.compile(r"(sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{20,}|ghp_[A-Za-z0-9]{20,}|xox[bp]-|"
                    r"BEGIN [A-Z ]*PRIVATE KEY|\b\d{8,10}:[A-Za-z0-9_-]{30,}\b)", re.I)
OR = "https://openrouter.ai/api/v1"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai"

PROMPT = (
    "Vygeneruj {n} FIKTIVNÍCH (smyšlených) zubních ordinací v Česku pro testovací data. "
    "Vrať POUZE JSON pole bez komentářů. Každý prvek: "
    '{{"base_name": "krátký smyšlený název bez slov klinika/centrum/ordinace (např. Úsměv Letná)", '
    '"city": "české město", "tagline": "1 věta", "about": "2-3 věty o péči, bez kontaktů", '
    '"services": ["4-6 služeb"], "prices": [{{"service": "...", "price_czk": číslo}}], '
    '"hours": "např. Po-Pá 8:00-16:00"}}. '
    "Žádná jména osob, telefony, e-maily, adresy ani odkazy. Nepiš o objednávání, rezervacích, "
    "kontaktech, cizincích ani jazycích. Piš česky s diakritikou. Města: {cities}."
)
CITIES = [["Praha", "Brno", "Ostrava", "Plzeň"], ["Olomouc", "Liberec", "Praha", "Hradec Králové"],
          ["České Budějovice", "Pardubice", "Zlín", "Praha"], ["Jihlava", "Brno", "Kladno", "Praha"]]


def _keys() -> dict:
    path = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "keys" / "provider-keys.env"
    env: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _openai_chat(base: str, model: str, key: str, prompt: str, max_tokens: int = 4000) -> str:
    body = json.dumps({"model": model, "max_tokens": max_tokens, "temperature": 0.9,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "HTTP-Referer": "https://bossman.local", "X-Title": "bossman-site-emulator"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        data = json.load(resp)
    return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""


def _glm_agent46(prompt: str) -> str:
    token = (Path(os.environ["LOCALAPPDATA"]) / "Bossman/CommandCenter/token").read_text().strip()
    base = "http://127.0.0.1:8801/api"

    def call(method, path, body=None):
        req = urllib.request.Request(base + path, method=method,
                                     data=json.dumps(body).encode() if body else None,
                                     headers={"X-BCC-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            return json.loads(r.read() or b"{}")

    t = call("POST", "/chat/threads", {"title": "site emulator synthetic dental copy"})
    tid = t.get("id") or t.get("thread", {}).get("id")
    r = call("POST", f"/chat/threads/{tid}/send", {"text": prompt, "agent_id": 46})
    task_id = r.get("task_id") or (r.get("task") or {}).get("id")
    for _ in range(60):
        time.sleep(4)
        task = call("GET", f"/tasks/{task_id}")
        task = task.get("task", task)
        if task.get("status") in ("completed", "failed", "stopped", "waiting_approval"):
            if task.get("status") != "completed":
                raise RuntimeError(f"agent46 task {task.get('status')}")
            return str(task.get("result") or task.get("output") or "")
    raise TimeoutError("agent46 task did not finish")


def _parse(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("no JSON array in answer")
    items = json.loads(m.group(0))
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        prices = [p for p in it.get("prices") or [] if isinstance(p, dict) and str(p.get("service", "")).strip()]
        rec = {
            "base_name": str(it.get("base_name", "")).strip()[:40],
            "city": str(it.get("city", "")).strip()[:30],
            "tagline": str(it.get("tagline", "")).strip()[:160],
            "about": str(it.get("about", "")).strip()[:600],
            "services": [str(s).strip()[:60] for s in (it.get("services") or []) if str(s).strip()][:6],
            "prices": [{"service": str(p["service"]).strip()[:60],
                        "price_czk": int(re.sub(r"\D", "", str(p.get("price_czk", "0"))) or 0)} for p in prices][:6],
            "hours": str(it.get("hours", "")).strip()[:60],
        }
        if rec["base_name"] and rec["city"] and rec["about"] and len(rec["services"]) >= 3:
            out.append(rec)
    if len(out) < 3:
        raise ValueError(f"only {len(out)} valid records")
    return out


def providers(env: dict):
    yield "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free", lambda p: _openai_chat(
        OR, "nvidia/nemotron-3-ultra-550b-a55b:free", env["OPENROUTER_API_KEY"], p)
    yield "gemini:gemini-3-flash-preview", lambda p: _openai_chat(
        GEMINI, "gemini-3-flash-preview", env["GEMINI_API_KEY"], p)
    yield "bossman-agent46:glm-5.3-flash(7api)", _glm_agent46
    yield "openrouter:nvidia/nemotron-3.5-lightning:free", lambda p: _openai_chat(
        OR, "nvidia/nemotron-3.5-lightning:free", env["OPENROUTER_API_KEY"], p, max_tokens=6000)


def generate(batches: int = 4, per_batch: int = 4) -> dict:
    env = _keys()
    out = {"SYNTHETIC": True, "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
           "note": "Fictional clinics for the site-audit emulator. Not real businesses.", "batches": []}
    for b in range(batches):
        prompt = PROMPT.format(n=per_batch, cities=", ".join(CITIES[b % len(CITIES)]))
        if SECRET.search(prompt):
            raise SystemExit("secret-like pattern in prompt; aborted")
        attempts = []
        for name, fn in providers(env):
            t0 = time.time()
            try:
                clinics = _parse(fn(prompt))
                out["batches"].append({"provider": name, "seconds": round(time.time() - t0, 1),
                                       "failed_before": attempts, "clinics": clinics})
                print(f"batch {b}: {name} OK {len(clinics)} clinics {time.time() - t0:.1f}s", flush=True)
                break
            except (KeyError, urllib.error.URLError, OSError, ValueError, RuntimeError, TimeoutError) as exc:
                msg = f"{type(exc).__name__}: {str(exc)[:100]}"
                attempts.append({"provider": name, "error": msg})
                print(f"batch {b}: {name} FAILED {msg}", flush=True)
        else:
            out["batches"].append({"provider": "builtin-templates", "failed_before": attempts, "clinics": []})
            print(f"batch {b}: all providers failed -> built-in templates", flush=True)
    return out


if __name__ == "__main__":
    data = generate()
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", FIXTURE.relative_to(REPO))
    sys.exit(0)
