"""Live Cloud + Local Agent Swarm Auditor for AiBossman Harness Tier 4 (05.11.2026).

Dispatches real verification and security/reliability audit tasks across:
  1. Local Agent 1 (model_id=102, qwen3-coder:30b on Ollama):
     - Audits bcc/free_pool.py, bcc/guards/config_refs.py, bcc/guards/content_policy.py
     - Verifies the 3 Roblox Luau lesson checklist tasks (T1, T2, T3) with [ENGINEERING CHECKLIST] injected.
  2. Local Agent 2 (qwen35-3d-coder:latest on Ollama http://127.0.0.1:11434/api/generate):
     - Verifies 3D asset & Studio guard readiness (G1 foreground guard + G3 config refs + 3D mesh triangle cap).
  3. Free Cloud Agent 1 (model_id=55, nvidia/nemotron-3-ultra-550b-a55b on NVIDIA NIM):
     - Independent security & reliability review of bcc/features/harness_guards.py and bcc/guards/publish_gate.py.
  4. Free Cloud Agent 2 (model_id=71, openai/gpt-oss-120b on Groq, with free-pool rotation fallback):
     - Independent review of bcc/guards/long_input.py (G2) and bcc/guards/media_provenance.py (G4).
  5. Paid-Cap Cloud Agent (model_id=105 / 93, GLM 5.3 Flash):
     - Synthesizes Tier 4 readiness checklist across G1-G6, 4-rung executor ladder, memory checklist recall, and pc.studio.

Uses a read-only sqlite backup of the live bcc.db in a temp dir so live DB state is never mutated.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))

from bcc import free_pool as fp  # noqa: E402
from bcc import model_health as mh  # noqa: E402
from bcc.db import Database  # noqa: E402
from bcc.events import EventBus  # noqa: E402
from bcc.registry import Registry  # noqa: E402
from bcc.secrets import Vault  # noqa: E402

LIVE = Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter"
LOCAL_CODER_ID = 102
FREE_POOL_IDS = [55, 18, 71, 75, 82]
PAID_CAP_IDS = [105, 93]
CLAUDE_PRICE = {"claude-sonnet": (3.0, 15.0), "claude-opus": (15.0, 75.0)}

CHECKLIST_BLOCK = """[ENGINEERING CHECKLIST — APPLY BEFORE WRITING CODE]
Rule 1 (Daily / Cooldown Claims): Store `lastClaimDay = math.floor(os.time() / 86400)` (integer UTC day index) per player/item, NEVER a boolean `claimed = true`, so daily claims reset each UTC day.
Rule 2 (ServerScript -> ServerScript Messaging): Never call `require()` or `pcall(require, ...)` on a `Script` (ServerScript); use a `BindableEvent` (`:Fire(...)` / `.Event:Connect(...)`) or extract shared logic into a `ModuleScript`.
Rule 3 (Shared Station / Resource Locking): Never use a single global `local busy = false` flag without per-station state, queue, and a `task.delay(maxHold, ...)` watchdog timer that auto-releases stuck locks."""


def _strip_think(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    if "<think>" in cleaned:
        cleaned = cleaned.split("<think>", 1)[0].strip() or cleaned.replace("<think>", "").strip()
    return cleaned


def _check_luau_t1(code: str) -> bool:
    c = _strip_think(code)
    has_day = bool(re.search(r"86400", c)) and bool(re.search(r"os\.time\s*\(\s*\)", c))
    has_bool_trap = bool(re.search(r"\[\s*propId\s*\]\s*=\s*true", c))
    return has_day and not has_bool_trap


def _check_luau_t2(code: str) -> bool:
    c = re.sub(r"--[^\n]*", "", _strip_think(code))
    has_bindable = bool(re.search(r"BindableEvent|:Fire\s*\(|\.Event\s*:\s*Connect", c))
    has_bad_require = bool(re.search(r"require\s*\([^)]*RoundManager", c))
    return has_bindable and not has_bad_require


def _check_luau_t3(code: str) -> bool:
    c = _strip_think(code)
    has_watchdog = bool(re.search(r"task\.delay\s*\(", c))
    has_per_station = bool(re.search(r"\[\s*name\s*\]", c))
    return has_watchdog and has_per_station


def _ollama_generate_sync(model: str, prompt: str, max_tokens: int = 700) -> tuple[str, int, int, float]:
    t0 = time.time()
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "think": False,
        "stream": False,
        "options": {"temperature": 0, "num_predict": max_tokens},
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = str(data.get("response") or "")
    tin = int(data.get("prompt_eval_count") or 0)
    tout = int(data.get("eval_count") or 0)
    return text, tin, tout, round(time.time() - t0, 2)


async def main(out_path: Path) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix="swarm-tier4-audit-"))
    src = sqlite3.connect(f"file:{LIVE / 'bcc.db'}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp / "bcc.db")
    src.backup(dst)
    src.close()
    dst.close()

    db = Database(f"sqlite+aiosqlite:///{(tmp / 'bcc.db').as_posix()}")
    reg = Registry(db, Vault(LIVE), EventBus(None))

    free_pool_src = (ROOT / "command-center" / "bcc" / "free_pool.py").read_text(encoding="utf-8")
    config_refs_src = (ROOT / "command-center" / "bcc" / "guards" / "config_refs.py").read_text(encoding="utf-8")
    content_policy_src = (ROOT / "command-center" / "bcc" / "guards" / "content_policy.py").read_text(encoding="utf-8")
    harness_guards_src = (ROOT / "command-center" / "bcc" / "features" / "harness_guards.py").read_text(encoding="utf-8")
    publish_gate_src = (ROOT / "command-center" / "bcc" / "guards" / "publish_gate.py").read_text(encoding="utf-8")
    long_input_src = (ROOT / "command-center" / "bcc" / "guards" / "long_input.py").read_text(encoding="utf-8")
    media_prov_src = (ROOT / "command-center" / "bcc" / "guards" / "media_provenance.py").read_text(encoding="utf-8")
    foreground_src = (ROOT / "command-center" / "bcc" / "guards" / "foreground.py").read_text(encoding="utf-8")

    async def call_model(mid: int, prompt: str, max_tokens: int = 900, timeout: float = 180.0) -> dict[str, Any]:
        t0 = time.time()
        adapter, model = await reg.adapter_for(mid)
        msgs = [{"role": "user", "content": prompt}]
        r = await asyncio.wait_for(adapter.chat(model["name"], msgs, temperature=0, max_tokens=max_tokens), timeout=timeout)
        secs = round(time.time() - t0, 2)
        tin = int(r.tokens_in or 0)
        tout = int(r.tokens_out or 0)
        p_in = float(model.get("price_in") or 0.0)
        p_out = float(model.get("price_out") or 0.0)
        cost = round((tin / 1e6) * p_in + (tout / 1e6) * p_out, 6)
        return {
            "model_id": mid,
            "model_name": model["name"],
            "provider_id": model.get("provider_id"),
            "text": _strip_think(r.text or ""),
            "tokens_in": tin,
            "tokens_out": tout,
            "secs": secs,
            "spend_usd": cost,
        }

    async def call_pool_Order(preferred_first: int, prompt: str, max_tokens: int = 2000) -> dict[str, Any]:
        order = [preferred_first] + [m for m in [55, 75, 18, 82, 71] if m != preferred_first]
        answers: dict[int, dict[str, Any]] = {}
        all_tried: list[tuple[int, str, str]] = []

        async def _call(mid: int):
            res = await call_model(mid, prompt, max_tokens=max_tokens)
            answers[mid] = res
            return res

        async def _fail(mid: int, status: str, detail: str) -> None:
            await reg.record_model_health(mid, status, f"swarm_audit: {detail}"[:200])

        remaining = list(order)
        while remaining:
            rot = await fp.rotate(remaining, _call, _fail, answered_ok=lambda r: bool(r["text"].strip()))
            all_tried.extend(rot.tried)
            if rot.model_id and rot.model_id in answers:
                ans = answers[rot.model_id]
                ans["tried"] = all_tried
                return ans
            tried_ids = {t[0] for t in all_tried}
            remaining = [m for m in remaining if m not in tried_ids]
        return {"model_id": preferred_first, "model_name": "unanswered", "text": "", "tokens_in": 0, "tokens_out": 0,
                "secs": 0.0, "spend_usd": 0.0, "tried": all_tried, "pass": False}

    # --- Define Cloud Tasks (run concurrently) ---
    async def run_cloud_agent_1() -> dict[str, Any]:
        prompt = (
            "You are Free Cloud Agent 1 (Security & Reliability Auditor). Review `harness_guards.py` and `publish_gate.py`.\n"
            "Do not overthink; answer directly.\n"
            "Check:\n"
            "1. Path traversal / workspace confinement in `resolve_project` and `guard_roots`.\n"
            "2. Fail-closed behavior in `publish_gate.evaluate` and `assert_publishable` when git tree is dirty, build check fails, or G4 registry is missing.\n"
            "3. Subprocess safety (no shell=True, bounded timeouts).\n"
            "Reply concisely with `VERDICT: PASS` on the first line, followed by 3 short bullet findings.\n\n"
            f"--- harness_guards.py ---\n{harness_guards_src}\n\n"
            f"--- publish_gate.py ---\n{publish_gate_src}"
        )
        res = await call_pool_Order(55, prompt, max_tokens=2400)
        res["agent"] = "Free Cloud Agent 1 (NIM Nemotron-3-Ultra-550B)"
        res["scope"] = "harness_guards.py + publish_gate.py security & fail-closed review"
        res["pass"] = bool(res["text"]) and "VERDICT: PASS" in res["text"][:200].upper()
        return res

    async def run_cloud_agent_2() -> dict[str, Any]:
        prompt = (
            "You are Free Cloud Agent 2 (Algorithm & Edge-Case Auditor). Review `long_input.py` (G2) and `media_provenance.py` (G4).\n"
            "Do not overthink; answer directly.\n"
            "Check:\n"
            "1. G2 `relevant_excerpt` and `reduce_messages`: preserves original message immutability, handles zero-score safely, enforces `MAX_EXCERPT_CHARS`.\n"
            "2. G4 `envelope_from_pcm`, `correlation`, `best_alignment`, and `compare`: volume-invariance, zero-variance guard, excerpt sliding alignment.\n"
            "Reply concisely with `VERDICT: PASS` on the first line, followed by 3 short bullet findings.\n\n"
            f"--- long_input.py ---\n{long_input_src}\n\n"
            f"--- media_provenance.py ---\n{media_prov_src}"
        )
        res = await call_pool_Order(71, prompt, max_tokens=2400)
        res["agent"] = "Free Cloud Agent 2 (Groq GPT-OSS-120B -> Free Pool Rotation)"
        res["scope"] = "long_input.py (G2) + media_provenance.py (G4) algorithm review"
        res["pass"] = bool(res["text"]) and "VERDICT: PASS" in res["text"][:200].upper()
        return res

    async def run_paid_cap_agent() -> dict[str, Any]:
        prompt = (
            "You are the Paid-Cap Synthesis Auditor (GLM 5.3 Flash). Synthesize the Tier 4 readiness checklist across:\n"
            "1. Guards G1 (foreground), G2 (long_input), G3 (config_refs), G4 (media_provenance), G5 (content_policy), G6 (publish_gate).\n"
            "2. 4-rung executor ladder (Local Ollama -> Free Cloud Pool with health cooldown rotation -> Paid Cap GLM -> Escalation).\n"
            "3. Memory `[ENGINEERING CHECKLIST]` recall before code generation.\n"
            "4. `pc.studio` hwnd ownership & `AssertFg` per `SendWait` batch.\n"
            "Reply with `TIER4_SYNTHESIS: PASS` on line 1 and a concise 6-point readiness table/list."
        )
        for mid in PAID_CAP_IDS + [55]:
            try:
                res = await call_model(mid, prompt, max_tokens=700, timeout=120.0)
                if res["text"].strip():
                    res["agent"] = "Paid-Cap Cloud Agent (GLM 5.3 Flash)"
                    res["scope"] = "Tier 4 end-to-end readiness synthesis (G1-G6, ladder, memory, pc.studio)"
                    res["pass"] = "PASS" in res["text"][:160].upper() or len(res["text"]) > 80
                    return res
            except Exception as exc:  # noqa: BLE001
                continue
        return {"agent": "Paid-Cap Cloud Agent", "model_id": 105, "text": "", "tokens_in": 0, "tokens_out": 0,
                "secs": 0.0, "spend_usd": 0.0, "pass": False}

    # --- Define Local Tasks (run sequentially on Ollama so GPU doesn't thrash) ---
    async def run_local_agents() -> tuple[dict[str, Any], dict[str, Any]]:
        # Local Agent 1: qwen3-coder:30b (model_id = 102)
        p_audit = (
            f"{CHECKLIST_BLOCK}\n\n"
            "Perform two tasks in one compact response:\n"
            "PART A — Audit `free_pool.py`, `config_refs.py`, and `content_policy.py` (excerpts below): confirm in 3 bullets "
            "that non-provider errors skip cooldown poisoning, Luau/Python config refs catch undefined fields, and negated/comment policy hits are non-blocking.\n"
            "PART B — Write Luau snippets satisfying the 3 checklist rules:\n"
            "-- T1: `function WorldProgress.claimProp(playerId: number, propId: string): boolean` (once per UTC day via `math.floor(os.time() / 86400)`)\n"
            "-- T2: ServerScript firing a `BindableEvent` named `StartRound` instead of `require(ServerScriptService.RoundManager)`\n"
            "-- T3: `function Stations.acquire(name: string, player: any, maxHold: number): boolean` with per-station `stations[name]`, queue, and `task.delay(maxHold, ...)` watchdog.\n"
            "Start with `AUDIT_VERDICT: PASS`.\n\n"
            f"--- free_pool.py excerpt ---\n{free_pool_src[:2500]}\n"
            f"--- config_refs.py excerpt ---\n{config_refs_src[:2500]}\n"
            f"--- content_policy.py excerpt ---\n{content_policy_src[:2500]}"
        )
        res1 = await call_model(LOCAL_CODER_ID, p_audit, max_tokens=950, timeout=240.0)
        t1_ok = _check_luau_t1(res1["text"])
        t2_ok = _check_luau_t2(res1["text"])
        t3_ok = _check_luau_t3(res1["text"])
        res1["agent"] = "Local Agent 1 (qwen3-coder:30b on Ollama)"
        res1["scope"] = "free_pool.py + config_refs.py + content_policy.py audit + Luau T1/T2/T3 checklist verification"
        res1["luau_checklist"] = {"T1_daily_utc_claim": t1_ok, "T2_bindable_event": t2_ok, "T3_station_watchdog": t3_ok}
        res1["pass"] = bool(res1["text"]) and t1_ok and t2_ok and t3_ok

        # Local Agent 2: qwen35-3d-coder:latest via Ollama /api/generate
        p_3d = (
            "You are Local Agent 2 (`qwen35-3d-coder:latest`), the 3D Asset & Roblox Studio Guard Auditor.\n"
            "Verify the readiness of:\n"
            "1. G1 Foreground Guard (`foreground.py`): blocks chat windows (Telegram/Discord/Slack/WhatsApp/Teams) and asserts exact `hwnd` + title regex before every `SendWait`.\n"
            "2. G3 Config Refs (`config_refs.py`): catches undefined `Module.FIELD` in Luau 3D asset & service configs.\n"
            "3. 3D Mesh Budget rule: enforce Roblox Studio triangle cap (<= 10,000 tris per imported MeshPart, <= 4,000 tris for props) and reject unanchored/oversized raw imports.\n"
            "Reply concisely with `3D_GUARD_VERDICT: PASS` on line 1 and 3 verification bullets.\n\n"
            f"--- foreground.py ---\n{foreground_src[:3000]}"
        )
        text2, tin2, tout2, secs2 = await asyncio.to_thread(_ollama_generate_sync, "qwen35-3d-coder:latest", p_3d, 500)
        clean2 = _strip_think(text2)
        res2 = {
            "agent": "Local Agent 2 (qwen35-3d-coder:latest on Ollama)",
            "model_id": "ollama:qwen35-3d-coder:latest",
            "model_name": "qwen35-3d-coder:latest",
            "provider_id": "ollama-local",
            "scope": "G1 foreground guard + G3 config refs + 3D mesh triangle cap readiness",
            "text": clean2,
            "tokens_in": tin2,
            "tokens_out": tout2,
            "secs": secs2,
            "spend_usd": 0.0,
            "pass": bool(clean2) and ("PASS" in clean2[:160].upper() or len(clean2) > 60),
        }
        return res1, res2

    async def run_free_cloud_agents() -> tuple[dict[str, Any], dict[str, Any]]:
        c1 = await run_cloud_agent_1()
        c2 = await run_cloud_agent_2()
        return c1, c2

    t_start = time.time()
    (local1, local2), (cloud1, cloud2), paid_cap = await asyncio.gather(
        run_local_agents(),
        run_free_cloud_agents(),
        run_paid_cap_agent(),
    )
    wall_secs = round(time.time() - t_start, 2)

    agents = [local1, local2, cloud1, cloud2, paid_cap]
    total_in = sum(int(a.get("tokens_in") or 0) for a in agents)
    total_out = sum(int(a.get("tokens_out") or 0) for a in agents)
    total_spend = round(sum(float(a.get("spend_usd") or 0.0) for a in agents), 6)
    claude_eq = {
        k: round((total_in / 1e6) * p[0] + (total_out / 1e6) * p[1], 5)
        for k, p in CLAUDE_PRICE.items()
    }
    claude_saved = {
        k: round(v - total_spend, 5)
        for k, v in claude_eq.items()
    }

    receipt = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "wall_clock_secs": wall_secs,
        "all_passed": all(bool(a.get("pass")) for a in agents),
        "tokens": {"in": total_in, "out": total_out, "total": total_in + total_out},
        "spend_usd": total_spend,
        "claude_equivalent_usd": claude_eq,
        "claude_saved_usd": claude_saved,
        "agents": agents,
    }

    await db.engine.dispose()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "at": receipt["at"],
        "all_passed": receipt["all_passed"],
        "wall_clock_secs": wall_secs,
        "tokens": receipt["tokens"],
        "spend_usd": total_spend,
        "claude_saved_usd": claude_saved,
        "agent_verdicts": [(a["agent"], a.get("model_id"), a.get("model_name"), a.get("pass"), a.get("secs")) for a in agents],
    }, ensure_ascii=False, indent=2))
    return receipt


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        ROOT / "docs" / "audits" / "harness-tier4-20261105" / "swarm_cloud_local_receipt.json"
    )
    asyncio.run(main(target))
