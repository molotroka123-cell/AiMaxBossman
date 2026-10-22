#!/usr/bin/env python3
"""Bossman North Star 2x Performance Benchmark & A/B Harness (`CP-PERF-00..06`).

Measures the end-to-end Agent Orchestrator critical path:
    Mobile Request -> Session Auth/Load -> Model Router Catalog -> Skill Match ->
    Preflight -> Task Create -> Engine Enqueue -> Tool/Skill Verification -> Event Stream

Also supports running controlled local Vulkan MTP A/B sweeps via `llama-cli.exe`
when `--with-vulkan-mtp` is passed and no live direct-gen media job is active.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))


def _git_sha(path: Path) -> str:
    r = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip()


async def run_orchestrator_suite(iterations: int = 15) -> dict[str, Any]:
    """Run deterministic paired workload lanes against Command Center `/api/agent/*`."""
    import httpx
    import sqlalchemy as sa
    from bcc.api import create_app
    from bcc.auth import HEADER
    from bcc.config import Settings
    from bcc.db import agents as agents_t, models as models_t, providers as providers_t
    from bcc.providers import ChatResult, Health
    from bcc.v2.skill_library import SkillLibrary

    class _FastFakeAdapter:
        async def chat(self, model: str, messages: list[dict], **kw: Any) -> ChatResult:
            return ChatResult(text="готово", tokens_in=12, tokens_out=6, model=model)

        async def health(self) -> Health:
            return Health(status="ok", latency_ms=1)

        async def list_models(self) -> list[str]:
            return ["gemma4-31b-uncensored:latest"]

    with tempfile.TemporaryDirectory(prefix="bossman_perf2x_", ignore_cleanup_errors=True) as tmp_str:
        tmp = Path(tmp_str)
        data_dir = tmp / "data"
        ws_skills = tmp / "ws" / ".agents" / "skills"
        ws_skills.mkdir(parents=True, exist_ok=True)
        src_skill = ROOT / ".agents" / "skills" / "north-star-tree-hygiene" / "SKILL.md"
        if src_skill.is_file():
            dst = ws_skills / "north-star-tree-hygiene"
            dst.mkdir(parents=True, exist_ok=True)
            (dst / "SKILL.md").write_bytes(src_skill.read_bytes())

        settings = Settings(
            data_dir=data_dir,
            database_url=f"sqlite+aiosqlite:///{data_dir / 'bcc.db'}",
            ui_dir=tmp / "no-ui",
        )
        app = create_app(settings, announce_token=False, adapter_factory=lambda *a, **k: _FastFakeAdapter())
        svc = app.state.svc
        await svc.start()
        try:
            svc.skills = SkillLibrary([ws_skills], ws_skills)
            headers = {HEADER: svc.auth.token}
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
                prov = (await client.post("/api/providers", json={
                    "name": "Local Ollama",
                    "kind": "openai_compat",
                    "base_url": "http://127.0.0.1:8080/v1",
                })).json()
                mod = (await client.post("/api/models", json={
                    "provider_id": prov["id"],
                    "name": "gemma4-31b-uncensored:latest",
                    "alias": "gemma4-31b-uncensored:latest",
                    "caps": {"chat": True, "tools": True, "code": True},
                })).json()
                await client.post("/api/agents", json={
                    "name": "Gemma 4 uncensored",
                    "system_prompt": "Ты — Bossman OpenCode Agent.",
                    "model_id": mod["id"],
                    "tools": ["terminal.run", "code.search", "code.expand", "code.status", "memory.search", "computer.observe"],
                    "permissions": {"read": "auto", "write": "ask", "exec": "ask", "computer_read": "auto", "computer_act": "ask"},
                    "max_steps": 8,
                })

                # Seed 20 sessions to exercise session list + turn history
                session_ids: list[str] = []
                for i in range(20):
                    r = await client.post("/api/agent/sessions", json={
                        "title": f"Perf Session {i}",
                        "mode": "AGENT",
                    })
                    assert r.status_code == 201, r.text
                    session_ids.append(r.json()["id"])

                sid = session_ids[0]

                # Lane 1: End-to-End Agent Message Dispatch (Mobile -> Session -> Catalog -> Route -> Skill -> Task)
                e2e_msg_ms: list[float] = []
                for i in range(iterations):
                    t0 = time.perf_counter()
                    res = await client.post(f"/api/agent/sessions/{sid}/messages", json={
                        "text": f"Проверь статус git репозитория и список файлов (шаг {i})",
                        "mode": "AGENT",
                    })
                    dt = (time.perf_counter() - t0) * 1000.0
                    assert res.status_code == 200, res.text
                    e2e_msg_ms.append(dt)

                # Lane 2: Deterministic Verified Skill Fast-Path vs Full Turn
                skill_msg_ms: list[float] = []
                for i in range(iterations):
                    t0 = time.perf_counter()
                    res = await client.post(f"/api/agent/sessions/{sid}/messages", json={
                        "text": f"Проверь гигиену 927 листьев capability tree и сверь SHA tree-registry.json с HEAD ({i})",
                        "mode": "AGENT",
                    })
                    dt = (time.perf_counter() - t0) * 1000.0
                    assert res.status_code == 200, res.text
                    skill_msg_ms.append(dt)

                # Lane 3: Session List with 20 populated sessions (`GET /api/agent/sessions`)
                list_sess_ms: list[float] = []
                for _ in range(iterations):
                    t0 = time.perf_counter()
                    res = await client.get("/api/agent/sessions")
                    dt = (time.perf_counter() - t0) * 1000.0
                    assert res.status_code == 200, res.text
                    list_sess_ms.append(dt)

                # Lane 4: Model Router Catalog + Route Selection (`GET /api/agent/models` + `POST /api/agent/router/select`)
                router_ms: list[float] = []
                for _ in range(iterations):
                    t0 = time.perf_counter()
                    r1 = await client.get("/api/agent/models")
                    r2 = await client.post("/api/agent/router/select", json={"mode": "CODE", "allow_cloud": False})
                    dt = (time.perf_counter() - t0) * 1000.0
                    assert r1.status_code == 200 and r2.status_code == 200
                    router_ms.append(dt)

                # Lane 5: Tool Registry Schema Payload (`GET /api/agent/tools`)
                tools_ms: list[float] = []
                tools_bytes = 0
                filtered_bytes = 0
                for _ in range(iterations):
                    t0 = time.perf_counter()
                    r_all = await client.get("/api/agent/tools")
                    dt = (time.perf_counter() - t0) * 1000.0
                    assert r_all.status_code == 200
                    tools_bytes = len(r_all.content)
                    tools_ms.append(dt)
                r_filt = await client.get("/api/agent/tools", params={"mode": "CODE", "compact": "1"})
                if r_filt.status_code == 200:
                    filtered_bytes = len(r_filt.content)

                # Collect trace summary if available
                trace_top = []
                r_tr = await client.get("/api/agent/perf/top-spans")
                if r_tr.status_code == 200:
                    trace_top = r_tr.json().get("top_spans", [])
        finally:
            await svc.stop()

    def _stats(samples: list[float]) -> dict[str, float]:
        s = sorted(samples)
        p95_idx = min(len(s) - 1, int(len(s) * 0.95))
        return {
            "count": len(s),
            "median_ms": round(statistics.median(s), 3),
            "mean_ms": round(statistics.mean(s), 3),
            "p95_ms": round(s[p95_idx], 3),
            "min_ms": round(s[0], 3),
            "max_ms": round(s[-1], 3),
        }

    combined_baseline = [
        e2e_msg_ms[i] + skill_msg_ms[i] + list_sess_ms[i] + router_ms[i]
        for i in range(iterations)
    ]

    return {
        "lanes": {
            "e2e_agent_message_ms": _stats(e2e_msg_ms),
            "skill_routed_message_ms": _stats(skill_msg_ms),
            "list_20_sessions_ms": _stats(list_sess_ms),
            "router_catalog_and_select_ms": _stats(router_ms),
            "tools_schema_ms": _stats(tools_ms),
            "combined_critical_path_ms": _stats(combined_baseline),
        },
        "payload_sizes": {
            "full_tools_bytes": tools_bytes,
            "filtered_compact_tools_bytes": filtered_bytes or tools_bytes,
        },
        "top_spans": trace_top,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--iterations", type=int, default=15)
    ap.add_argument("--compare-baseline", type=Path, default=None)
    args = ap.parse_args()

    suite = asyncio.run(run_orchestrator_suite(iterations=args.iterations))
    installed_wt = Path(r"C:\Users\asd\Bossman\wt-checkpoint-20261015")
    doc: dict[str, Any] = {
        "schema": "bossman.perf2x.run/1",
        "label": args.label,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repo_head_sha": _git_sha(ROOT),
        "installed_head_sha": _git_sha(installed_wt) if installed_wt.is_dir() else None,
        "hardware": {
            "os": platform.platform(),
            "cpu": platform.processor(),
            "python": sys.version.split()[0],
            "apu": "AMD Ryzen AI Max+ 395 w/ Radeon 8060S (128 GB UMA)",
        },
        **suite,
    }

    if args.compare_baseline and args.compare_baseline.is_file():
        base = json.loads(args.compare_baseline.read_text(encoding="utf-8"))
        speedups: dict[str, Any] = {}
        for lane, cur_stat in doc["lanes"].items():
            base_stat = (base.get("lanes") or {}).get(lane) or {}
            b_med = float(base_stat.get("median_ms") or 0.0)
            c_med = float(cur_stat.get("median_ms") or 0.0)
            ratio = round(b_med / c_med, 3) if c_med > 0 else 0.0
            speedups[lane] = {
                "baseline_median_ms": b_med,
                "candidate_median_ms": c_med,
                "speedup_x": ratio,
            }
        doc["comparison_vs_baseline"] = {
            "baseline_file": str(args.compare_baseline.name),
            "baseline_sha": base.get("repo_head_sha"),
            "speedups": speedups,
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(doc.get("comparison_vs_baseline") or doc["lanes"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
