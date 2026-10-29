"""Tests for North Star Long Horizon Claude ($1.00 budget enforcer, 15 branches, and CriticalErrorReflex)."""
from __future__ import annotations

from bcc.features.north_star_horizon import CriticalErrorReflex, FIFTEEN_BRANCHES, RUN_ID, RUN_NAME


def test_fifteen_branches_match_capability_tree_roots():
    assert len(FIFTEEN_BRANCHES) == 15
    node_ids = [b["node_id"] for b in FIFTEEN_BRANCHES]
    assert node_ids == [
        "jeff", "ux", "apps", "models", "cloud",
        "computer", "media", "memory", "agents", "skills",
        "plugins", "ops", "oss", "commerce", "devices",
    ]


def test_reflex_diagnoses_known_critical_errors(tmp_path):
    reflex = CriticalErrorReflex(data_dir=tmp_path)
    assert reflex.stats()["catalog_entries"] >= 16

    res1 = reflex.diagnose("Модель вернула пустой или неполный ответ (content: \"\")")
    assert res1["matched"] is True
    assert any(m["id"] == "LLM-REASONING-OFF" for m in res1["matches"])
    assert res1["latency_ms"] < 50.0

    res2 = reflex.diagnose("[API Error: 400 request (66032 tokens) exceeds the available context size (65536 tokens)]")
    assert res2["matched"] is True
    assert any(m["id"] == "LLM-CTX-OVERFLOW" for m in res2["matches"])


def test_reflex_records_and_scrubs_new_critical_lesson(tmp_path):
    reflex = CriticalErrorReflex(data_dir=tmp_path)
    saved = reflex.record_lesson({
        "id": "NS-LH-CLAUDE-COMFY-SYN-TIMEOUT",
        "class": "network-loopback",
        "title": "TCP SYN retransmission delay 2050 ms when local ComfyUI (:8188) is offline",
        "severity": "HIGH",
        "status": "FIXED",
        "component": "bcc/oss/comfyui.py",
        "symptom": "GET /api/direct-gen/status takes >2000ms for client ivan.petrov@example.org",
        "symptom_regex": [r"direct-gen/status.*2\d{3}\s*ms", r"ComfyUI.*connect timeout"],
        "keywords": ["comfyui", "direct-gen", "timeout", "2050ms", "8188"],
        "detection": "Measure GET /api/direct-gen/status latency when 127.0.0.1:8188 is closed.",
        "root_cause": "Windows loopback TCP SYN retransmission waits ~2.05s when connect timeout is 2.0s.",
        "fix": "Use httpx.Timeout(2.0, connect=0.25) and asyncio.gather in DirectGenService.status.",
        "prevention": "Always set connect<=0.25s on optional loopback sidecars.",
    })
    assert "ivan.petrov@example.org" not in saved["symptom"]
    assert "[EMAIL_" in saved["symptom"]

    diag = reflex.diagnose("direct-gen/status took 2276 ms due to ComfyUI connect timeout")
    assert diag["matched"] is True
    assert any(m["id"] == "NS-LH-CLAUDE-COMFY-SYN-TIMEOUT" for m in diag["matches"])


async def test_north_star_api_routes(env):
    r = await env.client.get("/api/north-star/status")
    assert r.status_code == 200
    body = r.json()
    assert body["run_name"] == RUN_NAME
    assert body["run_id"] == RUN_ID
    assert body["budget"]["cap_usd"] == 1.0
    assert body["budget"]["within_budget"] is True
    assert body["branches_total"] == 15

    r2 = await env.client.post("/api/north-star/reflex/diagnose", json={
        "text": "request (66032 tokens) exceeds the available context size (65536 tokens)"
    })
    assert r2.status_code == 200
    assert r2.json()["matched"] is True
