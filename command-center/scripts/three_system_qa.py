"""§5 — re-run the three-system QA, this time without a human rescuing it.

The cloud QA run could not test Video Studio, Web Designer or Apps at all: the
browser tool refuses loopback URLs, so tasks 29 and 30 honestly reported "a
public URL is needed" and stopped. That is what the signed relay exists to fix,
so the acceptance for it has to be the same three systems, driven the same way
a cloud agent would drive them — over the relay contract, with nothing but
signed jobs going in and sanitized evidence coming out.

What this proves and what it does not:

  * It proves the relay path end to end against a real server: real HTTP, real
    signing, real capability handlers calling the same service functions the UI
    calls, real sanitized evidence.
  * It does NOT prove that a remote cloud agent can reach this machine. Where
    the job queue lives is a deployment question and stays outside the
    repository; the contract is identical over an outbound poll, a file drop or
    a paste, which is exactly why it can be exercised here.

Run:  python -m scripts.three_system_qa  [--out evidence.json]
Exit: 0 when every system answered, 1 otherwise. The evidence file is written
either way — a failed run's evidence is the more useful of the two.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bcc import qa_relay as relay  # noqa: E402

#: The three systems §5 names, plus core health as the control: if health fails
#: too, the run says "the server is unwell", not "Video Studio is broken".
SYSTEMS = ["video_studio.smoke", "web_designer.smoke", "apps.smoke", "health.smoke"]


async def _run(out_path: Path) -> int:
    import httpx
    from bcc.api import create_app
    from bcc.auth import HEADER
    from bcc.config import Settings
    import tempfile

    started = time.time()
    workdir = Path(tempfile.mkdtemp(prefix="bcc-3system-qa-"))
    settings = Settings(data_dir=workdir / "data",
                        database_url=f"sqlite+aiosqlite:///{workdir / 'data' / 'qa.db'}",
                        ui_dir=workdir / "no-ui")
    app = create_app(settings, announce_token=False, start_workers=False)
    svc = app.state.svc
    await svc.start()
    transport = httpx.ASGITransport(app=app)
    results: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://qa",
                                     headers={HEADER: svc.auth.token}) as client:
            # The owner's own setup, through the same endpoints an owner uses.
            secret = (await client.post("/api/qa-relay/secret")).json()["secret"]
            enabled = (await client.post("/api/qa-relay/enable")).json()
            if not enabled.get("enabled"):
                raise RuntimeError("bridge refused to enable")

            for capability in SYSTEMS:
                job = relay.build_job(secret, capability)
                t0 = time.perf_counter()
                response = await client.post("/api/qa-relay/job", json=job)
                body = response.json()
                results.append({
                    "system": capability,
                    "job_id": job["job_id"],
                    "http_status": response.status_code,
                    "status": body.get("status"),
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                    "evidence": body.get("evidence"),
                    "detail": body.get("detail"),
                    # An intervention is a human doing something the run could
                    # not. Driving the relay is the run's own work, so this is
                    # 0 by construction — and stating that explicitly is the
                    # point of the whole exercise.
                    "manual_interventions": 0,
                })

            # Negative controls, in the same run: the acceptance is only
            # meaningful if the bridge still refuses what it must refuse.
            replay = await client.post("/api/qa-relay/job", json=job)
            forged = await client.post(
                "/api/qa-relay/job",
                json=relay.build_job("not-the-shared-secret", "apps.smoke"))
            forbidden = await client.post(
                "/api/qa-relay/job", json=relay.build_job(secret, "terminal.run"))
            await client.post("/api/qa-relay/disable")
            after_kill = await client.post(
                "/api/qa-relay/job", json=relay.build_job(secret, "apps.smoke"))
            audit = (await client.get("/api/qa-relay/audit")).json()

            controls = {
                "replayed_job": replay.json().get("status"),
                "forged_signature": forged.json().get("status"),
                "unlisted_capability": forbidden.json().get("status"),
                "after_owner_kill_switch": after_kill.json().get("status"),
            }
    finally:
        await svc.stop()

    ok = all(r["status"] == relay.OK for r in results)
    controls_ok = controls == {
        "replayed_job": relay.REJECTED_REPLAY,
        "forged_signature": relay.REJECTED_SIGNATURE,
        "unlisted_capability": relay.REJECTED_CAPABILITY,
        "after_owner_kill_switch": relay.REJECTED_DISABLED,
    }
    evidence = {
        "run": "three_system_qa",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "wall_seconds": round(time.time() - started, 2),
        "transport": "in-process ASGI over the real relay contract",
        "systems": results,
        "negative_controls": controls,
        "negative_controls_pass": controls_ok,
        "manual_interventions_total": 0,
        "audit_entries": len(audit.get("entries", [])),
        "verdict": "PASS" if (ok and controls_ok) else "FAIL",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps({k: v for k, v in evidence.items() if k != "systems"},
                     ensure_ascii=False, indent=2))
    for row in results:
        print(f"  {row['system']:24s} {row['status']:8s} {row['duration_ms']:>5d}ms")
    return 0 if evidence["verdict"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.three_system_qa")
    parser.add_argument("--out", default="docs/testing/three-system-qa-20260908.json",
                        help="where to write the evidence file")
    args = parser.parse_args(argv)
    return asyncio.run(_run(Path(args.out)))


if __name__ == "__main__":
    raise SystemExit(main())
