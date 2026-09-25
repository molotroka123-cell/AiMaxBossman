#!/usr/bin/env python3
"""Configure the Bossman 1.5 economy swarm through the running Bossman API.

No OpenRouter request is made directly by this script. It asks Bossman's
canonical OpenRouter catalog to sync, pins the exact model ids, then creates or
updates five Bossman agents. The API key remains in Bossman's vault.

Free workers are accepted only when the live catalog reports zero input/output
price. GLM 5.3 Flash is the only paid model in this profile and gets a small,
explicit agent budget.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))

from bcc.terminal_cli.api_client import BossmanError, Client, discover  # noqa: E402
from bcc.features.economy_swarm import GLM_MODEL, LING_MODEL, NEMOTRON_MODEL  # noqa: E402


MODEL_SPECS = (
    (NEMOTRON_MODEL, "nemotron-ultra-free", True),
    (LING_MODEL, "ling-flash-fin-free", True),
    (GLM_MODEL, "glm53-finalizer", False),
)

READ_ONLY = ["fs.read", "fs.search", "fs.list", "search_journal", "log"]
CODER = ["fs.read", "fs.write", "fs.edit", "fs.search", "fs.list", "run", "tests", "git",
         "search_journal", "log"]

AGENTS = (
    {
        "name": "YT-Nemotron-Transcript",
        "role": "public-video transcript evidence worker",
        "model_alias": "nemotron-ultra-free",
        "tools": [],
        "max_steps": 8,
        "max_tokens": 16000,
        "system_prompt": (
            "You are Nemotron worker 1 of 3 inside Bossman. Work only from the bounded PUBLIC YouTube "
            "episode bundle supplied by Bossman. Extract teacher claims, triggers, invalidations and uncertainty. "
            "Do not invent market numbers, do not promote lessons, do not trade, and return typed evidence that "
            "another verifier can audit."
        ),
    },
    {
        "name": "YT-Nemotron-Chart",
        "role": "public-video chart/order-flow evidence worker",
        "model_alias": "nemotron-ultra-free",
        "tools": [],
        "max_steps": 8,
        "max_tokens": 16000,
        "system_prompt": (
            "You are Nemotron worker 2 of 3 inside Bossman. Inspect only structured observations/crops already "
            "extracted locally from public K1m6a videos. Check Price/CVD/OI/levels/timeframe consistency, call out "
            "UNKNOWN rather than guessing, and emit evidence-backed candidate observations. Never promote a lesson "
            "or authorize a trade."
        ),
    },
    {
        "name": "YT-Nemotron-Strategy",
        "role": "public-video strategy hypothesis worker",
        "model_alias": "nemotron-ultra-free",
        "tools": [],
        "max_steps": 8,
        "max_tokens": 16000,
        "system_prompt": (
            "You are Nemotron worker 3 of 3 inside Bossman. Synthesize candidate strategy rules from the public "
            "teacher transcript plus deterministic market observations and future in-video outcomes. Separate "
            "observation, teacher opinion, hypothesis, trigger and invalidation. Everything remains UNVERIFIED "
            "until Ling/hidden tests/independent evidence approve it. No live trading."
        ),
    },
    {
        "name": "Ling-Fin-Verifier",
        "role": "free verifier, tester and bounded repair coder",
        "model_alias": "ling-flash-fin-free",
        "tools": CODER,
        "max_steps": 30,
        "max_tokens": 32000,
        "system_prompt": (
            "You are the free verification/coding lane. Reproduce before fixing, run tests, inspect actual files, "
            "prefer minimal patches, and reject false DONE. Verify Nemotron candidate lessons against evidence and "
            "code against hidden/neighbor/negative tests. A teacher/model claim is never enough to promote memory. "
            "Do not spend money, trade, publish, or bypass Bossman approvals."
        ),
    },
    {
        "name": "GLM53-Finalizer",
        "role": "paid finalizer used only after free verifier failure",
        "model_alias": "glm53-finalizer",
        "tools": CODER,
        "max_steps": 20,
        "max_tokens": 24000,
        "system_prompt": (
            "You are the paid FINALIZER, not the first-line coder. You are invoked only after Nemotron and Ling "
            "evidence has been exhausted and Bossman's economy gate explicitly allows one paid escalation. Read "
            "the failing evidence, make the smallest defensible patch, run verification, and stop. Do not broaden "
            "scope, auto-recharge, trade, publish, or bypass approvals."
        ),
    },
)


def _money(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _exact_catalog(client: Client, provider_id: int, remote_id: str) -> dict:
    page = client.get(f"/api/openrouter/{provider_id}/catalog",
                      params={"q": remote_id, "limit": 100, "include_stale": False})
    rows = page.get("items") if isinstance(page, dict) else []
    row = next((x for x in rows or [] if x.get("remote_id") == remote_id), None)
    if row is None:
        raise RuntimeError(f"OpenRouter live catalog does not contain exact model id: {remote_id}")
    return row


def _pin(client: Client, provider_id: int, remote_id: str, alias: str, free_only: bool) -> int:
    row = _exact_catalog(client, provider_id, remote_id)
    pin = _money(row.get("price_in"))
    pout = _money(row.get("price_out"))
    if free_only and (not remote_id.endswith(":free") or pin not in (0.0,) or pout not in (0.0,)):
        raise RuntimeError(
            f"refusing supposed free worker {remote_id}: live catalog price_in={pin} price_out={pout}")
    if not free_only and (pin is None or pout is None):
        raise RuntimeError(f"refusing paid finalizer with unknown live pricing: {remote_id}")
    result = client.post(f"/api/openrouter/{provider_id}/pin",
                         {"remote_id": remote_id, "alias": alias})
    return int(result["model_id"])


def _upsert_agent(client: Client, model_ids: dict[str, int], spec: dict, glm_budget: float) -> dict:
    agents = client.get("/api/agents") or []
    old = next((a for a in agents if a.get("name") == spec["name"]), None)
    body = {
        "name": spec["name"],
        "role": spec["role"],
        "system_prompt": spec["system_prompt"],
        "model_id": model_ids[spec["model_alias"]],
        "fallback_model_id": None,
        "tools": spec["tools"],
        "max_steps": spec["max_steps"],
        "max_tokens": spec["max_tokens"],
        "budget_usd": glm_budget if spec["model_alias"] == "glm53-finalizer" else 0.0,
        "permissions": {},
        "enabled": True,
    }
    if old is None:
        return client.post("/api/agents", body)
    patch = dict(body)
    patch.pop("name")
    return client.patch(f"/api/agents/{int(old['id'])}", patch)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--data-dir")
    ap.add_argument("--glm-budget-usd", type=float, default=0.25)
    ap.add_argument("--skip-probes", action="store_true")
    ap.add_argument("--out", default="")
    ns = ap.parse_args(argv)
    if not 0 <= ns.glm_budget_usd <= 25:
        ap.error("--glm-budget-usd must be in [0,25]")

    try:
        target = discover(ns.url, ns.data_dir)
        with Client(target, timeout=120) as client:
            provider = client.get("/api/openrouter/provider")
            if not provider.get("connected") or not provider.get("has_key"):
                raise RuntimeError("Bossman canonical OpenRouter provider/key is not connected")
            pid = int(provider["provider_id"])
            client.post(f"/api/openrouter/{pid}/sync?force=true", {})
            model_ids: dict[str, int] = {}
            catalog: dict[str, dict] = {}
            for remote_id, alias, free_only in MODEL_SPECS:
                catalog[remote_id] = _exact_catalog(client, pid, remote_id)
                model_ids[alias] = _pin(client, pid, remote_id, alias, free_only)
                if not ns.skip_probes:
                    client.post(f"/api/openrouter/models/{model_ids[alias]}/probe", {})
            created = [_upsert_agent(client, model_ids, spec, ns.glm_budget_usd) for spec in AGENTS]
            economy = client.get("/api/economy/status")
    except (BossmanError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    report = {
        "ok": True,
        "bossman": target.url,
        "provider_id": pid,
        "models": model_ids,
        "catalog": {k: {"price_in": v.get("price_in"), "price_out": v.get("price_out"),
                         "context_window": v.get("context_window"),
                         "advertised_caps": v.get("advertised_caps")} for k, v in catalog.items()},
        "agents": [{"id": a.get("id"), "name": a.get("name"), "model_id": a.get("model_id"),
                    "budget_usd": a.get("budget_usd")} for a in created],
        "glm_budget_usd": ns.glm_budget_usd,
        "economy": economy,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if ns.out:
        pathlib.Path(ns.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
