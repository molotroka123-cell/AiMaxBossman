"""Live evidence for the OpenRouter leaves (cap-22, mod-openrouter) at $0.

Runs the REAL bcc app (temp data dir, vault, db) from THIS worktree and drives the real OpenRouter feature:
  POST /api/openrouter/connect  -> live GET /key + GET /models + catalog sync
  GET  .../catalog               -> real catalog rows; picks a ':free' model with price 0/0 ONLY
  POST .../pin  + /models/{id}/probe -> live chat probe against that FREE model
Paid models are never called: the probe refuses unless remote_id endswith ':free' and price_in == price_out == 0.
Key: env OPENROUTER_API_KEY or the existing secrets-file lookup (tools/worker_client.openrouter_key); never printed.
Output: evidence/out/_live_openrouter.txt (+ evidence/out/_live_openrouter.summary.json). Exit 0 only when all live checks pass.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVID = ROOT / "docs/architecture/bossman-tree-20261005/evidence"
OUTFILE = EVID / "out" / "_live_openrouter.txt"
SUMMARY = EVID / "out" / "_live_openrouter.summary.json"
LOG: list[str] = []
KEY = ""


def say(msg: str) -> None:
    text = str(msg)
    if KEY:
        text = text.replace(KEY, "[KEY]")
    LOG.append(text)
    print(text, flush=True)


def find_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    base = os.environ.get("LOCALAPPDATA_REAL") or os.environ.get("LOCALAPPDATA", "")
    f = Path(base) / "Bossman" / "secrets" / "openrouter-test.env"
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


async def main() -> int:
    global KEY
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    KEY = find_key()
    if not KEY:
        say("NO_KEY: no OpenRouter key found; live leaf stays KEEP")
        return 2
    tmp = tempfile.mkdtemp(prefix="agcloud_live_")
    # real owner data dir is never used: everything below lives in tmp
    for k in list(os.environ):
        if any(t in k.upper() for t in ("KEY", "TOKEN", "SECRET")):
            os.environ.pop(k, None)
    os.environ.update(LOCALAPPDATA=tmp, APPDATA=tmp, BCC_DATA_DIR=tmp, BOSSMAN_DATA_DIR=tmp, PYTHONUTF8="1")
    sys.path[:0] = [str(ROOT / "command-center"), str(ROOT / "bossman-core"), str(ROOT)]
    import bcc.features.openrouter as feat
    assert str(Path(feat.__file__).resolve()).lower().startswith(str(ROOT).lower()), feat.__file__
    say(f"module: {feat.__name__} file inside worktree: OK")
    from tests.conftest import client_for, make_settings, start_app
    app, svc = await start_app(make_settings(Path(tmp)))
    ok = False
    try:
        c = client_for(app, svc)
        r = await c.post("/api/openrouter/connect", json={"api_key": KEY})
        say(f"connect: HTTP {r.status_code}")
        body = r.json() if r.status_code == 200 else {"err": r.text[:300]}
        say(f"connect body: ok={body.get('ok')} created={body.get('created')} models={body.get('models')} "
            f"catalog_error={body.get('catalog_error')}")
        if r.status_code != 200 or not body.get("ok") or not body.get("models"):
            return 1
        pid = body["provider_id"]
        r = await c.get(f"/api/openrouter/{pid}/catalog", params={"q": ":free", "limit": 200})
        items = [i for i in r.json()["items"] if str(i["remote_id"]).endswith(":free")]
        say(f"catalog: HTTP {r.status_code} free_items={len(items)}")
        # price re-read through the product's own parser, no guessing
        from bcc.v2.openrouter_ext import catalog_price_values
        zero = [i for i in items if catalog_price_values(i) == {"price_in": 0.0, "price_out": 0.0}]
        say(f"catalog: zero-priced :free models={len(zero)}")
        if not zero:
            return 1
        pref = ("nvidia/nemotron-3-ultra-550b-a55b:free", "inclusionai/ling-3.0-flash-fin:free")
        zero.sort(key=lambda i: (0 if i["remote_id"] in pref else 1, i["remote_id"]))
        verified_chat = None
        for cand in zero[:6]:
            rid = cand["remote_id"]
            assert rid.endswith(":free")
            pin = (await c.post(f"/api/openrouter/{pid}/pin", json={"remote_id": rid, "alias": "agcloud-live"})).json()
            say(f"pin: {rid} -> model_id={pin.get('model_id')}")
            pr = await c.post(f"/api/openrouter/models/{pin['model_id']}/probe")
            say(f"probe: HTTP {pr.status_code}")
            if pr.status_code != 200:
                say(pr.text[:300])
                continue
            probes = pr.json()["probes"]
            for p in probes:
                say(f"  probe cap={p['capability']} advertised={p['advertised']} verified={p['verified']} "
                    f"skipped={p['skipped']} detail={str(p['detail'])[:120]}")
            chat = next((p for p in probes if p["capability"] == "chat"), None)
            if chat and chat["verified"]:
                verified_chat = rid
                break
            # candidate rate-limited/unavailable: free the alias and try the next free model
            async with svc.db.session() as s:
                import sqlalchemy as sa
                from bcc.db import models as models_t
                await s.execute(sa.delete(models_t).where(models_t.c.id == pin["model_id"]))
                await s.commit()
        say(f"RESULT verified_chat_model={verified_chat} cost=0 (price_in=price_out=0, :free only)")
        ok = verified_chat is not None
    finally:
        try:
            await svc.stop()
        except Exception as exc:  # noqa: BLE001
            say(f"stop: {type(exc).__name__}")
        text = "\n".join(LOG) + "\n"
        OUTFILE.parent.mkdir(parents=True, exist_ok=True)
        OUTFILE.write_text(text, encoding="utf-8")
        SUMMARY.write_text(json.dumps({"started_at": started,
                                       "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                       "passed": ok, "model": locals().get("verified_chat")},
                                      indent=1), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
