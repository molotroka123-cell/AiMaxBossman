"""Baseline of the call voice contour with the REAL engines (Whisper STT, local LLM, Piper/... TTS), no Telegram.

    python tools/voice_latency_baseline.py [--scenario basic] [--out docs/audits/2026-10-24-voice/baseline.json]

Runs bcc.telegram_calls.call.selftest with real=True on the loopback line: a synthetic caller speaks, our production
path (resampling, VAD, endpointing, barge-in, playout) answers. Evidence level «loopback»: it measures OUR path's
latency (end of caller speech -> first reply audio) and is never reported as a real Telegram call. Nobody is called.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "command-center"))
# BCC_DATA_DIR trap: without it Settings() points at the repo's empty command-center/data, not the live data dir
os.environ.setdefault("BCC_DATA_DIR", str(Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter"))


async def run(scenario: str, llm: str = "") -> dict:
    import dataclasses
    from bcc.telegram_calls.call.selftest import PEER, run_selftest
    from bcc.telegram_calls.settings import load_settings
    # the real engines scope the brain's history to a peer: the self-test's synthetic interlocutor, never a contact
    settings = dataclasses.replace(load_settings(), peer_user_id=PEER.user_id, **({"llm_model": llm} if llm else {}))
    return await run_selftest(settings, scenario, real=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", default="basic")
    ap.add_argument("--out", default="docs/audits/2026-10-24-voice/baseline.json")
    ap.add_argument("--llm", default="", help="CallSettings.llm_model override for this run only (the config file is not changed)")
    a = ap.parse_args(argv)
    t0 = time.time()
    res = asyncio.run(run(a.scenario, a.llm))
    res["llm_override"] = a.llm or None
    res.update(at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), seconds=round(time.time() - t0, 1),
               source_sha=subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True,
                                         cwd=HERE).stdout.strip())
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8", newline="\n")
    print(json.dumps(res, ensure_ascii=False, default=str)[:3000])
    return 0 if res.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
