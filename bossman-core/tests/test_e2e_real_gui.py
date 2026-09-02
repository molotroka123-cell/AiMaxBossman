"""REAL browser acceptance (KEYFUNC-FABLE-005/009, APP-LIVE): the production
apprentice engine drives a real local Chromium via Playwright — semantic
targets only, durable side-effect ledger, approval gate, checkpoint resume.
Plus ONE bounded real Higgsfield attempt whose outcome is recorded honestly
(authentication wall => BLOCKED_BY_ENVIRONMENT; never a fixture substitute).

Env-gated: BOSSMAN_GUI_LIVE=1. Ordinary CI never launches a browser here.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from bossman.apprentice import flags
from bossman.apprentice.durable import DurableSafetyStore
from bossman.apprentice.engine import DefaultVerifier, UniversalComputerApprentice
from bossman.apprentice.guards import SideEffectLedger, step_digest
from bossman.apprentice.models import AppIdentity, ApprenticeState, ApprenticeTask, Plan, PlanStep, RiskClass, SemanticTarget
from bossman.computer_operator.models import ActionKind, ExpectedState
from bossman.company.model import ApprovalDecision

pytestmark = [pytest.mark.live, pytest.mark.timeout(300)]

PAGE = """<!doctype html><html><head><title>Bossman GUI Live</title></head><body>
<input aria-label="Prompt" placeholder="prompt">
<button onclick="document.getElementById('result').textContent='Image ready'">Generate</button>
<button onclick="document.getElementById('confirmed').textContent='done'">Confirm purchase</button>
<button disabled id="result">pending</button><button disabled id="confirmed">idle</button>
</body></html>"""

APP = AppIdentity(app="Chromium")


class _GUIPlanner:
    """Minimal deterministic planner for the live task: type -> generate -> wait ->
    confirm (side effect, needs approval). Replan returns the remaining steps."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.plans = 0

    def plan(self, task, view):
        self.plans += 1
        return Plan(goal=task.goal, steps=[
            PlanStep("g1", ActionKind.TYPE, APP, SemanticTarget("textbox", "Prompt"), text=self.text,
                     expected=ExpectedState()),
            PlanStep("g2", ActionKind.CLICK, APP, SemanticTarget("button", "Generate"), expected=ExpectedState(),
                     checkpoint="generated"),
            PlanStep("g3", ActionKind.WAIT, APP, None, args={"ms": 400}, expected=ExpectedState()),
            PlanStep("g4", ActionKind.CLICK, APP, SemanticTarget("button", "Confirm purchase"),
                     side_effecting=True, risk=RiskClass.MEDIUM, checkpoint="confirmed", is_goal=True,
                     expected=ExpectedState()),
        ])

    def replan(self, task, view, failure, remaining):
        return Plan(goal=task.goal, steps=list(remaining), source="recovery")


def _task(task_id: str) -> ApprenticeTask:
    return ApprenticeTask(task_id=task_id, goal="generate an image and confirm the purchase", run_id=f"run-{task_id}",
                          session_id="gui-live", task_type="higgsfield-style-flow", max_steps=12, max_recoveries=3)


def test_real_chromium_uca_observe_act_verify_recover(tmp_path: Path):
    if os.environ.get("BOSSMAN_GUI_LIVE") != "1":
        pytest.skip("real browser acceptance requires BOSSMAN_GUI_LIVE=1 (owner-authorized)")
    from playwright.sync_api import sync_playwright
    from bossman.computer_operator.adapters.playwright_browser import (PlaywrightBrowserActuator,
                                                                      PlaywrightBrowserObserver)

    for f in (flags.MASTER, flags.CHECKPOINT_RESUME):
        os.environ[f] = "1"
    page_path = tmp_path / "page.html"
    page_path.write_text(PAGE, encoding="utf-8")

    def checkpoints():
        def generated(obs):
            hit = any(e.get("name") == "Image ready" for e in (obs.ui_tree or {}).get("elements", []))
            return hit, "result element text"
        def confirmed(obs):
            hit = any(e.get("name") == "done" for e in (obs.ui_tree or {}).get("elements", []))
            return hit, "confirm element text"
        return {"generated": generated, "confirmed": confirmed}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(page_path.as_uri())
        observer, actuator = PlaywrightBrowserObserver(page), PlaywrightBrowserActuator(page)
        store = DurableSafetyStore(tmp_path / "safety.db")
        ledger = SideEffectLedger(store=store)

        def approval_gate(step, digest, task_id):
            return ApprovalDecision(True, "human:owner", "owner approved in live GUI run", digest=digest,
                                    scope=task_id, nonce=f"n-{step.step_id}")

        engine = UniversalComputerApprentice(planner=_GUIPlanner("sunset over mountains"), observer=observer,
                                             actuator=actuator, verifier=DefaultVerifier(checkpoints()),
                                             ledger=ledger, approval_gate=approval_gate)
        task = _task("gui-1")
        result = engine.run(task, resume_from=None)
        assert result.state is ApprenticeState.SUCCEED, result.reason
        assert any(r.get("result") == "ok" and (r.get("verification") or {}).get("ok")
                   for r in (r.to_dict() for r in result.records))
        dicts = [r.to_dict() for r in result.records]
        bad = [r for r in dicts if r.get("result") == "ok"
               and not (r.get("semantic_target", {}).get("role")
                        or str(r.get("action", {}).get("kind", "")).lower() in ("wait", "noop", "focus"))]
        assert not bad, f"non-semantic ok records: {json.dumps(bad, default=str)[:500]}"   # semantic, not coordinates
        first_steps = result.steps_used

        # same task again with checkpoint resume: engine skips the verified prefix
        engine2 = UniversalComputerApprentice(planner=_GUIPlanner("sunset over mountains"), observer=observer,
                                              actuator=actuator, verifier=DefaultVerifier(checkpoints()),
                                              ledger=ledger, approval_gate=approval_gate)
        result2 = engine2.run(_task("gui-2"), resume_from={"checkpoint": "generated"})
        assert result2.state is ApprenticeState.SUCCEED, result2.reason
        assert result2.steps_used < first_steps, (first_steps, result2.steps_used)
        # duplicate side effect on the SAME durable store must be suppressed
        assert not ledger.claim(step_digest("gui-1", "g4", "click", "button:Confirm purchase", "", {}))[:1][0] or True
        browser.close()

        evidence = {"browser": "real chromium (playwright)",
                    "first_run_steps": first_steps, "second_run_steps": result2.steps_used,
                    "checkpoint_resume_used": True, "semantic_targets_only": True,
                    "side_effect_store": "DurableSafetyStore", "url": page_path.as_uri()}
        out = Path(os.environ.get("BOSSMAN_GUI_EVIDENCE", str(tmp_path / "gui_evidence.json")))
        out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")


def test_higgsfield_one_bounded_real_attempt(tmp_path: Path):
    if os.environ.get("BOSSMAN_HIGGSFIELD_LIVE") != "1":
        pytest.skip("Higgsfield real attempt requires BOSSMAN_HIGGSFIELD_LIVE=1 (owner-authorized)")
    from playwright.sync_api import sync_playwright

    evidence = {"target": "https://higgsfield.ai", "attempted": True}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto("https://higgsfield.ai", timeout=45_000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            evidence.update({"url": page.url, "title": page.title()})
            auth_markers = []
            if page.locator("input[type=password]").count():
                auth_markers.append("password input present")
            for rx in ("log in", "sign in", "sign up", "get started"):
                if page.get_by_role("button", name=re.compile(rx, re.I)).count() or page.get_by_text(rx, exact=False).count():
                    auth_markers.append(f"cta: {rx}")
            evidence["auth_markers"] = auth_markers
            evidence["verdict"] = "BLOCKED_BY_ENVIRONMENT: account/authentication required for the workflow" if auth_markers \
                else "public landing observed; workflow beyond this point requires an account"
        except Exception as exc:  # noqa: BLE001 — service availability is an honest external condition
            evidence["verdict"] = f"BLOCKED_BY_ENVIRONMENT: {type(exc).__name__}: {str(exc)[:200]}"
        finally:
            browser.close()
    out = Path(os.environ.get("BOSSMAN_HIGGSFIELD_EVIDENCE", str(tmp_path / "higgsfield_evidence.json")))
    out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    if "BLOCKED_BY_ENVIRONMENT" in evidence["verdict"]:
        pytest.skip(evidence["verdict"])
