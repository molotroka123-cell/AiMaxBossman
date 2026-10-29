"""Check that Bossman's dialog cost matches what Outrun actually debited (owner 29.10: «следи что бы цена диалога
считалась правильно»).

    python tools/outrun_cost_check.py --agent 51 [--turns 2]

Reads the Outrun balance (GET /v1/account, key from the Bossman vault, never printed) before and after a short
synthetic dialog sent through the real chat path to the agent, sums Bossman's per-run cost (tokens x model prices,
cached tokens at the cache price) and prints both numbers and the difference. Billing can lag: the balance is
re-read for up to 60 s.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import prove_chat_attachments as p  # noqa: E402

PROVIDER_ID = 17


def _key() -> str:
    data = Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter")
    sys.path.insert(0, str(ROOT / "command-center"))
    from bcc.secrets import Vault
    db = sqlite3.connect(f"file:{data / 'bcc.db'}?mode=ro", uri=True)
    try:
        enc = db.execute("select api_key_enc from providers where id=?", (PROVIDER_ID,)).fetchone()[0]
    finally:
        db.close()
    return Vault(data).decrypt(enc) or ""


def balance(key: str) -> float | None:
    req = urllib.request.Request("https://api.outrun.to/v1/account", headers={"Authorization": f"Bearer {key}",
                                                                              "User-Agent": "Bossman/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    for k in ("balance", "credit", "credits", "balance_usd", "available"):
        v = d.get(k) if isinstance(d, dict) else None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict):
            for kk in ("usd", "amount", "available", "total"):
                if isinstance(v.get(kk), (int, float)):
                    return float(v[kk])
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agent", type=int, default=51)
    ap.add_argument("--turns", type=int, default=2)
    a = ap.parse_args(argv)
    key = _key()
    before = balance(key)
    th = p.api("POST", "/api/chat/threads", {"title": "TEST outrun cost check"})
    tid = th.get("id") or (th.get("thread") or {}).get("id")
    asks = ["Назови три способа проверить, что веб-приложение собралось без ошибок. Коротко.",
            "Теперь то же самое, но для мобильного приложения. Коротко."][:a.turns]
    tasks = []
    for q in asks:
        sent = p.api("POST", f"/api/chat/threads/{tid}/send", {"text": q, "agent_id": a.agent}, timeout=600)
        task_id = sent.get("task_id") or (sent.get("task") or {}).get("id") or (sent.get("turn") or {}).get("task_id")
        t0, t = time.time(), {}
        while time.time() - t0 < 600:
            t = p.api("GET", f"/api/tasks/{task_id}", timeout=30)
            if t["task"]["status"] in ("completed", "failed", "stopped", "blocked"):
                break
            time.sleep(3)
        tasks.append(t)
    runs = [r for t in tasks for r in t["runs"]]
    bossman = round(sum(float(r.get("cost_usd") or 0) for r in runs), 6)
    after, t0 = before, time.time()
    while time.time() - t0 < 60:
        after = balance(key)
        if before is not None and after is not None and after < before:
            break
        time.sleep(10)
    debited = round(before - after, 6) if before is not None and after is not None else None
    try:
        p.api("PATCH", f"/api/chat/threads/{tid}", {"archived": True})
    except Exception:
        pass
    out = {"agent": a.agent, "turns": len(tasks), "statuses": [t["task"]["status"] for t in tasks],
           "tokens_in": sum(int(r.get("tokens_in") or 0) for r in runs), "tokens_out": sum(int(r.get("tokens_out") or 0) for r in runs),
           "bossman_cost_usd": bossman, "outrun_debited_usd": debited, "balance_before": before, "balance_after": after,
           "difference_usd": None if debited is None else round(debited - bossman, 6)}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if debited is not None and abs(debited - bossman) <= max(0.0005, 0.15 * max(debited, bossman)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
