"""Stage 2.2 proof runner: the REAL local uncensored Qwen as an L1-Lab operator, driven through the pult.

Phases (all in a TEST data dir, never the live bcc.db; no port is bound; no Telegram message is sent):

  ctx    measure the 256k context on the real model in stages (RAM / latency / loaded context length)
  task   `/work <natural language task>` through the real Companion + real Core + real backend app:
         planner (local Qwen) -> free cloud worker -> coder sidecar (local Qwen, tool loop) -> Bossman's own test
         verdict -> lab commit -> independent verifier -> receipts; then promotion / push refusal paths

Usage:  python tools/stage22_operator_proof.py ctx|task [--out DIR]
Secrets: provider keys are read from the owner's key file in-process and are never printed or written.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))
sys.path.insert(0, str(ROOT / "bossman-core"))

import httpx  # noqa: E402

from bcc.api import create_app  # noqa: E402
from bcc.auth import HEADER  # noqa: E402
from bcc.config import Settings  # noqa: E402
from bcc.features import qwen_operator as qo  # noqa: E402

KEYS_FILE = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "keys" / "provider-keys.env"
DATA = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "stage22-proof" / "data"
TASK_RU = ("В файле command-center/bcc/portcheck.py у функции is_loopback_host нет докстринга: добавь короткий "
           "докстринг на русском (что считается loopback). Попроси независимую бесплатную облачную модель "
           "предложить формулировку. Добавь новый тест command-center/tests/test_portcheck_loopback_host.py: "
           "127.0.0.1, localhost и ::1 — loopback, а 192.168.0.1 и example.com — нет. Запусти новый тест.")


def key(name: str) -> str | None:
    try:
        for line in KEYS_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"') or None
    except OSError:
        pass
    return None


def free_ram_gb() -> float:
    import ctypes

    class MS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong),
                    ("ap", ctypes.c_ulonglong), ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong),
                    ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
    m = MS()
    m.l = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return round(m.ap / 2 ** 30, 1)


async def phase_ctx(out: Path) -> dict:
    model = next((m for m in qo.LOCAL_UNCENSORED if await qo.ollama_has(m)), None)
    rows = []
    for num_ctx in (32768, 131072, qo.CTX_256K):
        before = free_ram_gb()
        res = await qo.ollama_chat(model, [{"role": "user", "content": "Ответь одним словом: готов?"}],
                                   num_ctx=num_ctx, num_predict=16)
        after = free_ram_gb()
        rows.append({"num_ctx": num_ctx, "ok": res.get("ok"), "seconds": res.get("seconds"),
                     "load_seconds": res.get("load_seconds"), "context_length_loaded": res.get("context_length_loaded"),
                     "size_gb": round((res.get("size_bytes") or 0) / 2 ** 30, 1),
                     "size_vram_gb": round((res.get("size_vram_bytes") or 0) / 2 ** 30, 1),
                     "free_ram_gb_before": before, "free_ram_gb_after": after, "error": res.get("error"),
                     "answer": res.get("text", "")[:60]})
        print(json.dumps(rows[-1], ensure_ascii=False))
        if not res.get("ok") or after < 12:
            rows[-1]["stopped_escalation"] = "failed or free RAM < 12 GB"
            break
    result = {"model": model, "stages": rows,
              "largest_that_works": max((r["num_ctx"] for r in rows if r["ok"]), default=None)}
    (out / "ctx_measurement.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


async def phase_task(out: Path) -> dict:
    from bcc.telegram_companion import operator_bridge as ob
    from bcc.telegram_companion.adapters import Core
    from bcc.telegram_companion.config import Person, Settings as CSettings
    from bcc.telegram_companion.service import Companion
    from bcc.telegram_companion.store import Store

    DATA.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=DATA, database_url=f"sqlite+aiosqlite:///{DATA / 'bcc.db'}", ui_dir=DATA / "no-ui")
    app = create_app(settings, announce_token=False)
    svc = app.state.svc
    await svc.start()
    api = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                            headers={HEADER: svc.auth.token}, timeout=60)
    evidence: dict = {"data_dir": str(DATA), "live_db_touched": False}

    async def post(path, body):
        r = await api.post(path, json=body)
        r.raise_for_status()
        return r.json()

    # --- registry through the normal API: local uncensored Qwen + free cloud models (keys never printed)
    local = await post("/api/providers", {"name": "local-ollama-11434", "kind": "openai_compat",
                                          "base_url": "http://127.0.0.1:11434/v1"})
    lm = await post("/api/models", {"provider_id": local["id"], "name": qo.LOCAL_UNCENSORED[0],
                                    "alias": "owner-local-community-uncensored", "kind": "local",
                                    "context_window": qo.CTX_256K, "price_in": 0, "price_out": 0})
    registered = ["owner-local-community-uncensored"]
    if key("OPENROUTER_API_KEY"):
        orp = await post("/api/providers", {"name": "OpenRouter", "kind": "openai_compat",
                                            "base_url": "https://openrouter.ai/api/v1",
                                            "api_key": key("OPENROUTER_API_KEY")})
        await post("/api/models", {"provider_id": orp["id"], "name": "nvidia/nemotron-3-ultra-550b-a55b:free",
                                   "alias": "nemotron-ultra-free", "kind": "cloud", "context_window": 262144,
                                   "price_in": 0, "price_out": 0, "caps": {"chat": True}})
        registered.append("nemotron-ultra-free (OpenRouter :free)")
    if key("NVIDIA_API_KEY"):
        nvp = await post("/api/providers", {"name": "NVIDIA NIM", "kind": "openai_compat",
                                            "base_url": "https://integrate.api.nvidia.com/v1",
                                            "api_key": key("NVIDIA_API_KEY")})
        await post("/api/models", {"provider_id": nvp["id"], "name": "nvidia/nemotron-3-super-120b-a12b",
                                   "alias": "nim-nemotron-super", "kind": "cloud", "context_window": 131072,
                                   "price_in": 0, "price_out": 0, "caps": {"chat": True, "free_tier": True}})
        registered.append("nim-nemotron-super (NVIDIA NIM)")
    agent = await post("/api/agents", qo.agent_values(lm["id"]))
    evidence.update(registered_models=registered, operator_agent={k: agent[k] for k in
                                                                  ("id", "name", "role", "model_id", "tools",
                                                                   "permissions", "budget_usd")})

    # --- the pult: real Companion + real Core over the in-process backend; Telegram is a recording fake
    owner = Person(11111, 11111, "owner", None)
    csettings = CSettings((owner,), local_url="http://127.0.0.1:8083/v1", local_model="best", bot_token="x",
                          core_url="http://test", core_token=svc.auth.token)

    class RecTelegram:
        def __init__(self):
            self.sent = []

        async def send(self, person, text, keyboard=None):
            self.sent.append({"at": time.strftime("%H:%M:%S"), "text": str(text),
                              "buttons": [lab for row in (keyboard or []) for lab, _ in row]})

        async def call(self, *a, **k):
            return True

    tg = RecTelegram()
    core = Core(csettings, transport=httpx.ASGITransport(app=app))
    store_dir = out / "pult-store"
    store_dir.mkdir(exist_ok=True)
    pult = Companion(csettings, Store(store_dir), tg, core, None)
    ob.POLL_SECONDS = 5.0
    started = time.time()
    reply = await pult.handle(owner, {"from": {"id": 11111, "is_bot": False},
                                      "chat": {"id": 11111, "type": "private"}, "text": "/work " + TASK_RU})
    evidence["pult_reply"] = getattr(reply, "text", str(reply))
    print("pult:", evidence["pult_reply"][:200])
    await pult.agent_jobs["operator"]
    evidence["wall_seconds"] = round(time.time() - started, 1)
    evidence["pult_messages"] = tg.sent
    rid = (await api.get("/api/qwen-operator/tasks")).json()["items"][0]["id"]
    rec = (await api.get(f"/api/qwen-operator/tasks/{rid}")).json()
    evidence["task"] = rec

    # --- promotion refusal + one owner-decision parked through the same guard (not executed)
    r = await api.post(f"/api/qwen-operator/tasks/{rid}/promote")
    evidence["promote_attempt"] = {"status_code": r.status_code, "body": r.json()}
    push = (await api.post(f"/api/qwen-operator/tasks/{rid}/command",
                           json={"argv": ["git", "push", "origin", "HEAD"], "why": "опубликовать ветку лаборатории",
                                 "recommendation": "не публиковать до одобрения Claude+Codex того же SHA",
                                 "concerns": "изменение уйдёт за пределы лаборатории"})).json()
    evidence["push_attempt"] = push
    final = (await api.get(f"/api/qwen-operator/tasks/{rid}")).json()
    evidence["after_attempts"] = {"decisions": final["decisions"], "refused_attempts": final.get("refused_attempts")}
    pending = [d for d in final["decisions"] if d["status"] == "PENDING"]
    if pending:   # what the owner would see in the pult for the push decision
        from bcc.telegram_companion.operator_bridge import decision_text
        evidence["push_decision_pult_text"] = decision_text(rid, pending[-1])
    lab = Path(final["lab"]["path"])
    import subprocess
    evidence["git"] = {
        "lab_log": subprocess.run(["git", "-C", str(lab), "log", "--oneline", "-3"], capture_output=True,
                                  text=True).stdout,
        "show_stat": subprocess.run(["git", "-C", str(lab), "show", "--stat", "--format=%H%n%an%n%s", "HEAD"],
                                    capture_output=True, text=True).stdout,
        "source_branch_head": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                                             text=True).stdout.strip(),
    }
    (out / "task_receipt.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=1, default=str),
                                           encoding="utf-8")
    await api.aclose()
    await core.close()
    await svc.stop()
    return evidence


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=("ctx", "task"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "owner" / "evidence" / "stage22"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    res = asyncio.run(phase_ctx(out) if args.phase == "ctx" else phase_task(out))
    print(json.dumps({k: (v if k != "task" else {"status": v.get("status"), "commit_sha": v.get("commit_sha")})
                      for k, v in res.items() if k in ("stages", "largest_that_works", "task", "wall_seconds")},
                     ensure_ascii=False, indent=1, default=str)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
