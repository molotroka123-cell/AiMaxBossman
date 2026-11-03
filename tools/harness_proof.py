"""Bossman Harness ladder on REAL backends (owner table 03.11 item 5): local -> free pool (rotation) -> paid cap.

What runs for real: Bossman's own Registry.adapter_for (governance + spend caps + vault-decrypted keys, which never
leave the adapter), the live model health records (cooldowns), bcc.free_pool ordering and rotation, the real local
Ollama model and real free cloud models. What is simulated: the local outage in rung 2 (the local adapter is
replaced by one that raises 503 — the owner's PC is not broken to prove this).

The live DB is copied with the sqlite backup API into a temp dir; health observations of this proof land in the
copy, never in the owner's DB. Prints a JSON receipt; no keys, prompts of the owner or chat contents are read.
usage: python tools/harness_proof.py [out.json]"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))

from bcc import free_pool as fp  # noqa: E402
from bcc import model_health as mh  # noqa: E402
from bcc.db import Database  # noqa: E402
from bcc.events import EventBus  # noqa: E402
from bcc.providers import ProviderError  # noqa: E402
from bcc.registry import Registry  # noqa: E402
from bcc.secrets import Vault  # noqa: E402

LIVE = Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter"
LOCAL_MODEL = 102                      # qwen3-coder:30b on Ollama (agent 79 / Kev)
HARNESS_POOL = [55, 18, 71, 75, 82]    # NIM nemotron-ultra, NIM deepseek-v4.1-flash, Groq gpt-oss-120b, Gemini 2.5/3.5 flash
PAID_CAP_MODEL = 105                   # GLM 5.3 Flash via 7API, $2/day cap — must NOT be called while free answers
TASK = ("Write Python 3.11: class DailyClaim with claim(self, player_id: str, now_ts: int) -> bool that pays at most "
        "once per UTC day per player (day = now_ts // 86400); True if paid now, False if already claimed today. "
        "Reply with one ```python block, implementation only.")
CHECK = ("d=DailyClaim(); assert d.claim('a',100); assert not d.claim('a',86399); assert d.claim('a',86400); "
         "assert d.claim('b',86400); assert not d.claim('b',86401)")
#: what the same tokens would cost on Claude (list price $/Mtok in, out) — the «limits saved» yardstick
CLAUDE_PRICE = {"claude-sonnet": (3.0, 15.0), "claude-opus": (15.0, 75.0)}


def passes(text: str) -> bool:
    import re
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", re.sub(r"<think>.*?</think>", "", text, flags=re.S), re.S)
    code = max(blocks, key=len) if blocks else text
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "c.py"
        f.write_text(code + "\n" + CHECK.replace("; ", "\n") + "\nprint('OK')\n", encoding="utf-8")
        r = subprocess.run([sys.executable, str(f)], capture_output=True, text=True, timeout=20)
        return r.returncode == 0 and "OK" in r.stdout


class _Down:
    """The local model unreachable (simulated outage, rung 2)."""
    async def chat(self, model, messages, **kw):
        raise ProviderError("503 local runtime down (simulated)", kind="http", status_code=503)


async def main(out: Path | None) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="harness-proof-"))
    src = sqlite3.connect(f"file:{LIVE / 'bcc.db'}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp / "bcc.db")
    src.backup(dst)
    src.close()
    dst.close()
    db = Database(f"sqlite+aiosqlite:///{(tmp / 'bcc.db').as_posix()}")
    reg = Registry(db, Vault(LIVE), EventBus(None))   # Vault reads the key file only
    msgs = [{"role": "user", "content": TASK}]
    receipt: dict = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "rungs": [], "paid_calls": 0}

    async def call(mid: int, adapter=None) -> tuple[str, int, int, float]:
        t0 = time.time()
        if adapter is None:
            adapter, model = await reg.adapter_for(mid)
        else:
            model = await reg.get_model(mid)
        if mid == PAID_CAP_MODEL:
            receipt["paid_calls"] += 1
        r = await asyncio.wait_for(adapter.chat(model["name"], msgs, temperature=0, max_tokens=900), timeout=240)
        return r.text or "", int(r.tokens_in or 0), int(r.tokens_out or 0), round(time.time() - t0, 1)

    # rung 1: local answers -> nothing else is called
    text, tin, tout, secs = await call(LOCAL_MODEL)
    receipt["rungs"].append({"rung": "1 local", "model": LOCAL_MODEL, "answered": bool(text.strip()),
                             "check_pass": passes(text), "tokens_in": tin, "tokens_out": tout, "secs": secs})

    # rung 2: local down -> the free pool, ordered by LIVE health (cooling models skipped), rotation on failure
    async with db.session() as s:
        import sqlalchemy as sa
        from bcc.db import models as models_t
        rows = (await s.execute(sa.select(models_t.c.id, models_t.c.health).where(
            models_t.c.id.in_(HARNESS_POOL)))).fetchall()
    health = {int(r[0]): mh.HealthRecord.from_dict(r[1]) for r in rows}
    skipped = [m for m in HARNESS_POOL if m in health and health[m].in_cooldown()]
    order = fp.pool_order([(m, health[m]) for m in HARNESS_POOL if m in health], exclude=[LOCAL_MODEL])
    tried_local = []
    try:
        await call(LOCAL_MODEL, adapter=_Down())
    except ProviderError as exc:
        tried_local.append(mh.classify_exception(exc)[0])
    answers: dict[int, tuple] = {}

    async def pool_call(mid: int):
        res = await call(mid)
        answers[mid] = res
        return res

    async def on_failure(mid: int, status: str, detail: str) -> None:
        await reg.record_model_health(mid, status, f"harness_proof: {detail}"[:200])   # lands in the temp copy

    rot = await fp.rotate(order, pool_call, on_failure, answered_ok=lambda r: bool(r[0].strip()))
    ans = answers.get(rot.model_id) if rot.model_id else None
    receipt["rungs"].append({
        "rung": "2 local down -> free pool", "local_status": tried_local, "pool_order": order,
        "skipped_cooling": skipped, "tried": rot.tried, "answered_by": rot.model_id,
        "check_pass": passes(ans[0]) if ans else False,
        "tokens_in": ans[1] if ans else 0, "tokens_out": ans[2] if ans else 0, "secs": ans[3] if ans else None})
    # rung 2b: a REAL failing free model forced to the front (Gemini 3.5 Flash, live-throttled 429 / kimi-k3 410):
    # rotation must classify it, record the cooldown and answer from the next model; the next order skips it.
    forced = [82] + [m for m in order if m != 82]
    answers.clear()
    rot2 = await fp.rotate(forced, pool_call, on_failure, answered_ok=lambda r: bool(r[0].strip()))
    async with db.session() as s:
        h2 = {int(r[0]): mh.HealthRecord.from_dict(r[1]) for r in (await s.execute(sa.select(
            models_t.c.id, models_t.c.health).where(models_t.c.id.in_([forced[0]])))).fetchall()}
    ans2 = answers.get(rot2.model_id) if rot2.model_id else None
    receipt["rungs"].append({
        "rung": "2b forced real-failing model first", "order": forced, "tried": rot2.tried,
        "answered_by": rot2.model_id, "check_pass": passes(ans2[0]) if ans2 else False,
        "failed_model_now_cooling": bool(h2.get(forced[0]) and h2[forced[0]].in_cooldown()),
        "next_order_skips_it": forced[0] not in fp.pool_order([(m, h2.get(m, health.get(m))) for m in forced
                                                                if (h2.get(m) or health.get(m))]),
        "tokens_in": ans2[1] if ans2 else 0, "tokens_out": ans2[2] if ans2 else 0, "secs": ans2[3] if ans2 else None})
    receipt["rungs"].append({"rung": "3 paid cap (GLM 7API)", "model": PAID_CAP_MODEL,
                             "called": receipt["paid_calls"] > 0,
                             "why": "not needed: a free rung answered" if (rot.model_id and rot2.model_id) else "pool exhausted"})
    tin = sum(r.get("tokens_in", 0) for r in receipt["rungs"])
    tout = sum(r.get("tokens_out", 0) for r in receipt["rungs"])
    receipt["spend_usd"] = 0.0 if receipt["paid_calls"] == 0 else None
    receipt["claude_equivalent_usd"] = {k: round(tin / 1e6 * p[0] + tout / 1e6 * p[1], 5)
                                        for k, p in CLAUDE_PRICE.items()}
    receipt["tokens"] = {"in": tin, "out": tout}
    await db.engine.dispose()
    if out:
        out.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    return receipt


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
