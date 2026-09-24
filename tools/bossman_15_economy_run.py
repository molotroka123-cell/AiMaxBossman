#!/usr/bin/env python3
"""Bossman 1.5 owner run: free-first YouTube learning + coding/test workers.

All OpenRouter inference goes through bcc.economy_orchestrator and therefore
through Bossman's provider/governance layer. This runner has no trading client
and never places an order.

The exact owner YouTube/channel URL is never guessed. Supply it with --channel
or BOSSMAN_K1MBA_YOUTUBE_URL. The requested public training window is
2026-08-14..2026-08-27.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))

from bcc.economy_orchestrator import EconomyOrchestrator, TrainingRound, model_policy  # noqa: E402
from distill_recorder import Recorder  # noqa: E402

WINDOW_START = "20260814"
WINDOW_END = "20260827"


def run(args: list[str], *, timeout: int = 3600,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False, env=env)


def discover_videos(channel: str) -> list[dict[str, Any]]:
    """Public yt-dlp metadata only; no DRM/auth/paywall bypass."""
    ytdlp = os.environ.get("BOSSMAN_YTDLP", "yt-dlp")
    proc = run([
        ytdlp, "--flat-playlist", "--dump-json",
        "--dateafter", WINDOW_START, "--datebefore", WINDOW_END, channel,
    ], timeout=900)
    if proc.returncode != 0:
        raise RuntimeError("yt-dlp discovery failed: " + (proc.stderr or proc.stdout)[-1000:])
    out = []
    for line in proc.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        vid = str(row.get("id") or "").strip()
        url = row.get("webpage_url") or row.get("url")
        if vid and url:
            out.append({
                "id": vid, "url": str(url), "timestamp": row.get("timestamp"),
                "upload_date": row.get("upload_date"), "title": row.get("title"),
            })
    return out


def ingest(url: str, inbox: pathlib.Path) -> pathlib.Path:
    inbox.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    proc = run([sys.executable, str(ROOT / "tools" / "youtube_trader_ingest_auto.py"), url,
                "--output-root", str(inbox)], timeout=7200, env=env)
    if proc.returncode != 0:
        raise RuntimeError("youtube ingest failed: " + (proc.stderr or proc.stdout)[-1200:])
    manifests = sorted(inbox.rglob("manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not manifests:
        raise RuntimeError("youtube ingest produced no manifest")
    return manifests[0].parent


def evidence_packet(video_dir: pathlib.Path) -> dict[str, Any]:
    manifest = json.loads((video_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    cases = video_dir / "candidate_cases.jsonl"
    if cases.is_file():
        for line in cases.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return {"manifest": manifest, "candidate_cases": rows[:200], "case_count": len(rows)}


async def main_async(ns) -> int:
    channel = ns.channel or os.environ.get("BOSSMAN_K1MBA_YOUTUBE_URL", "").strip()
    if not channel:
        raise RuntimeError(
            "exact owner YouTube URL missing: set --channel or BOSSMAN_K1MBA_YOUTUBE_URL; do not guess it"
        )
    out = pathlib.Path(ns.out)
    out.mkdir(parents=True, exist_ok=True)
    inbox = out / "youtube-inbox"
    orch = EconomyOrchestrator()
    recorder = Recorder("bossman-15-youtube-economy", directory=out / "distill")

    videos = discover_videos(channel)
    report: dict[str, Any] = {
        "schema": "bossman.15.owner-economy-run.v1",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "channel": channel,
        "window": ["2026-08-14", "2026-08-27"],
        "policy": model_policy(),
        "videos_discovered": len(videos),
        "video_runs": [],
        "coding": None,
        "finalizer": None,
    }

    for video in videos:
        try:
            vdir = ingest(video["url"], inbox)
            learned = await orch.learn_video(
                TrainingRound(video["url"], video["id"], evidence_packet(vdir))
            )
            learned["title"] = video.get("title")
            for worker in learned.get("workers", []):
                recorder.record(
                    model=worker["model"],
                    task_class="youtube_training:" + video["id"] + ":" + worker["role"],
                    messages=[],
                    response_text=worker.get("text", ""),
                    verdict="UNVERIFIED",
                    verifier="pending independent outcome/holdout verification",
                    curator_note="Teacher material is quarantined; not canonical strategy memory.",
                    extra={"source_url": video["url"], "packet_sha256": worker.get("packet_sha256"),
                           "learning_status": "UNVERIFIED"},
                )
            report["video_runs"].append(learned)
        except Exception as exc:
            report["video_runs"].append({
                "video_id": video.get("id"), "url": video.get("url"),
                "status": "BLOCKED", "error": f"{type(exc).__name__}: {exc}"[:500],
            })

    code_task = {
        "goal": "Review Bossman 1.5 YouTube-learning/economy paths and identify smallest code/test fixes",
        "video_status": [{"video_id": x.get("video_id"), "status": x.get("status")}
                         for x in report["video_runs"]],
        "requirements": ["free-first", "tests decide", "no live trading", "no raw teacher promotion"],
    }
    report["coding"] = await orch.code_review(code_task)
    recorder.record(
        model=report["coding"].get("model", ""),
        task_class="v1.5_free_code_review",
        messages=[],
        response_text=report["coding"].get("text", ""),
        verdict="UNVERIFIED",
        verifier="Codex/Aster + executable tests required",
        curator_note="Free Ling review; model DONE is never a PASS.",
    )

    unresolved = [x for x in report["video_runs"] if x.get("status") in ("BLOCKED", "FAIL")]
    bundle = {
        "status": "NEEDS_REVIEW" if unresolved else "FREE_PATH_COMPLETE",
        "blockers": [x.get("error") for x in unresolved[:20]],
        "ling_review": report["coding"].get("text", "")[:6000],
    }
    report["finalizer"] = await orch.finalize(bundle, allow_paid=ns.allow_glm)
    report["cost"] = {
        "spent_usd": round(orch.gateway.ledger.spent_usd, 8),
        "rows": orch.gateway.ledger.rows,
    }
    report["jev"] = orch.jev.records
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["weights_changed"] = False
    report["live_trading"] = False
    report["distill"] = {"records": recorder.count, "path": str(recorder.path),
                          "gate_state": "RAW_CANDIDATE"}

    path = out / "bossman-1.5-owner-run.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="")
    ap.add_argument(
        "--out",
        default=str(pathlib.Path(os.environ.get("LOCALAPPDATA", ".")) /
                    "Bossman" / "owner-run" / "v1.5-economy"),
    )
    ap.add_argument(
        "--allow-glm", action="store_true",
        help="permit a budgeted paid GLM finalizer only when Jev selects it",
    )
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
