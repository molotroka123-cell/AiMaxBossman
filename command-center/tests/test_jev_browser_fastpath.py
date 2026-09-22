"""Jev Ultrafast browser fast path — offline rules, each with a negative control.

Pure tests run without a browser; the Chromium section uses the real
BrowserManager (the same runtime the existing browser agent uses) against
fixture pages on 127.0.0.1. Jev itself is a mocked HTTP server.
"""
from __future__ import annotations

import asyncio
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bcc.jev import browser_fastpath as fp
from bcc.jev import config as jev_config
from bcc.jev.client import JevClient, JevInvalidResponse
from bcc.v2.browser_control import BrowserManager, BrowserPolicy

from .browser_support import chromium_available, reason as browser_reason
from .jev_mock import FAKE_KEY, MockJev, choice


def _obs(items, features=None, **kw):
    base = {"url": "https://example.test/p", "title": "T", "text": "page text", "generation": 3,
            "interactive": items,
            "features": features if features is not None else {k: 0 for k in fp.UNSUPPORTED_FEATURES}}
    base.update(kw)
    return base


ITEMS = [
    {"ref": "e3-0", "tag": "input", "type": "text", "name": "q", "aria": "Search", "value": ""},
    {"ref": "e3-1", "tag": "button", "type": "button", "text": "Go"},
    {"ref": "e3-2", "tag": "select", "name": "country",
     "options": [{"value": "", "label": "-"}, {"value": "cz", "label": "Czechia"}]},
    {"ref": "e3-3", "tag": "input", "type": "password", "name": "pw", "secret": True},
    {"ref": "e3-4", "tag": "input", "type": "file", "name": "upload"},
    {"ref": "e3-5", "tag": "button", "text": "Disabled", "disabled": True},
    {"ref": "e3-6", "tag": "button", "text": "Оплатить заказ"},
    {"ref": "e3-7", "tag": "button", "type": "submit", "text": "Отправить"},
]


@pytest.fixture
def mock():
    server = MockJev()
    yield server
    server.close()


@pytest.fixture
def client(monkeypatch, mock, tmp_path):
    monkeypatch.setenv("BOSSMAN_JEV_ENABLED", "1")
    monkeypatch.setenv("BOSSMAN_JEV_BROWSER_ENABLED", "1")
    monkeypatch.setenv("BOSSMAN_JEV_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BOSSMAN_JEV_ENDPOINT", mock.url)
    monkeypatch.setenv("BOSSMAN_JEV_TIMEOUT_MS", "500")
    monkeypatch.setenv("BOSSMAN_JEV_KILL_FILE", str(tmp_path / "jev.disabled"))
    return JevClient(jev_config.load(), sleep=lambda _s: None)


def _respond(op, target=None):
    def build(req):
        answers = {}
        for name, q in req["questions"].items():
            ids = list(q["criteria"])
            pick = op if name == "operation" else (target if target in ids else ids[0])
            answers[name] = choice(ids, pick if pick in ids else ids[0])
            if name == "operation" and op not in ids:
                answers[name] = {"choice": op, "confidence": 0.9, "probabilities": {i: 0.0 for i in ids}}
        return {"model": "jev-mock", "answers": answers, "usage": {}}
    return build


# ------------------------------------------------------------------ action space

def test_action_space_is_operation_specific_and_observed_only():
    elements, targets = fp.action_space(_obs(ITEMS))
    refs = {e["ref"] for op in targets.values() for e in op.values()}
    assert "e3-3" not in {e["ref"] for e in targets.get("TYPE_TEXT", {}).values()}   # secret: never typed
    assert "e3-4" not in refs and "e3-5" not in refs                                 # file input, disabled
    assert set(targets["SELECT"]) == {"3:1", "3:2"}
    assert targets["SELECT"]["3:2"] == {"ref": "e3-2", "option": "cz"}
    assert "1" in targets["TYPE_TEXT"] and "2" not in targets["TYPE_TEXT"]        # button is not typeable
    assert all(":" not in t for t in targets["CLICK"])                              # CLICK never holds options


def test_click_cannot_consume_a_select_target(client, mock):
    mock.script = [("json", _respond("CLICK", "3:2"))]
    proposal = fp.propose(client, _obs(ITEMS), "choose Czechia", force=True)
    assert proposal.operation == "CLICK" and ":" not in proposal.target            # mock fell back to a CLICK id


def test_target_head_answer_outside_its_set_rejected(client, mock):
    def build(req):
        out = _respond("CLICK")(req)
        ids = list(req["questions"]["click_target"]["criteria"])
        out["answers"]["click_target"] = {"choice": "3:2", "confidence": 0.9,
                                          "probabilities": {i: (1.0 / len(ids)) for i in ids}}
        return out
    mock.script = [("json", build)]
    with pytest.raises(JevInvalidResponse):
        fp.propose(client, _obs(ITEMS), "goal", force=True)


def test_selector_or_js_as_target_rejected(client, mock):
    def build(req):
        out = _respond("CLICK")(req)
        ids = list(req["questions"]["click_target"]["criteria"])
        out["answers"]["click_target"] = {"choice": "#pay-button", "confidence": 1.0,
                                          "probabilities": {i: (1.0 / len(ids)) for i in ids}}
        return out
    mock.script = [("json", build)]
    with pytest.raises(JevInvalidResponse):
        fp.propose(client, _obs(ITEMS), "goal", force=True)


def test_valid_proposal_control(client, mock):
    mock.script = [("json", _respond("TYPE_TEXT", "1"))]
    p = fp.propose(client, _obs(ITEMS), "search", force=True)
    assert (p.operation, p.target, p.ref) == ("TYPE_TEXT", "1", "e3-0")
    assert len(mock.requests) == 1


def test_validate_proposal_rejects_unobserved_ref_control_passes():
    obs = _obs(ITEMS)
    good = fp.Proposal("CLICK", "2", "e3-1", None, 0.9, 3, fp.observation_fingerprint(obs), obs["url"])
    fp.validate_proposal(good, obs)                                                   # control
    forged = fp.Proposal("CLICK", "2", "e9-99", None, 0.9, 3, "", obs["url"])
    with pytest.raises(fp.Escalate) as exc:
        fp.validate_proposal(forged, obs)
    assert exc.value.reason == "target_not_observed"
    wrong_op = fp.Proposal("TYPE_TEXT", "2", "e3-1", None, 0.9, 3, "", obs["url"])   # button is not typeable
    with pytest.raises(fp.Escalate):
        fp.validate_proposal(wrong_op, obs)
    with pytest.raises(JevInvalidResponse):
        fp.validate_proposal(fp.Proposal("RUN_JS", None, None, None, 1, 3, "", ""), obs)


# ------------------------------------------------------------------ freshness

def test_stale_page_rejected_control_fresh_passes():
    obs = _obs([{"ref": "e3-0", "tag": "input", "type": "text", "name": "q", "value": "a"}])
    p = fp.Proposal("CLICK", "1", "e3-0", None, 0.9, 3, fp.observation_fingerprint(obs), obs["url"])
    same = {"url": obs["url"], "generation": 3, "refs": ["e3-0"], "items": {"e3-0": {"value": "a"}}}
    fp.check_fresh(p, same)                                                           # control
    for changed in ({**same, "generation": 4}, {**same, "url": obs["url"] + "?x"},
                    {**same, "items": {"e3-0": {"value": "b"}}}, {**same, "refs": []}):
        with pytest.raises(fp.StalePage):
            fp.check_fresh(p, changed)


# ------------------------------------------------------------------ unsupported → escalate

@pytest.mark.parametrize("feature", fp.UNSUPPORTED_FEATURES)
def test_unsupported_feature_escalates_without_model_call(client, mock, feature):
    features = {k: 0 for k in fp.UNSUPPORTED_FEATURES}
    features[feature] = 1
    with pytest.raises(fp.Escalate) as exc:
        fp.propose(client, _obs(ITEMS, features), "goal", force=True)
    assert exc.value.reason == "unsupported_page" and feature in exc.value.detail
    assert mock.requests == []


def test_missing_feature_data_escalates_control_clean_page_calls(client, mock):
    obs = _obs(ITEMS)
    obs.pop("features")
    with pytest.raises(fp.Escalate):
        fp.propose(client, obs, "goal", force=True)
    assert mock.requests == []
    fp.propose(client, _obs(ITEMS), "goal", force=True)                               # control
    assert len(mock.requests) == 1


# ------------------------------------------------------------------ approval boundary

def test_approval_required_actions_stop_control_plain_click_allowed():
    obs = _obs(ITEMS)
    policy = BrowserPolicy.from_dict(None)
    pay = fp.Proposal("CLICK", "5", "e3-6", None, 1, 3, "", obs["url"])
    with pytest.raises(fp.ApprovalRequired) as exc:
        fp.approval_gate(pay, obs, policy)
    assert exc.value.action == "purchase" and exc.value.reason == "policy_denied"
    send = fp.Proposal("CLICK", "6", "e3-7", None, 1, 3, "", obs["url"])
    with pytest.raises(fp.ApprovalRequired) as exc:
        fp.approval_gate(send, obs, policy)
    assert exc.value.action == "submit" and exc.value.reason == "approval_required"
    go = fp.Proposal("CLICK", "2", "e3-1", None, 1, 3, "", obs["url"])
    assert fp.approval_gate(go, obs, policy) == "click"                                # control


def test_owner_policy_ask_on_click_is_respected():
    obs = _obs(ITEMS)
    strict = BrowserPolicy.from_dict({"rules": {"click": "ask"}})
    go = fp.Proposal("CLICK", "2", "e3-1", None, 1, 3, "", obs["url"])
    with pytest.raises(fp.ApprovalRequired):
        fp.approval_gate(go, obs, strict)


# ------------------------------------------------------------------ DONE verification

def test_wrong_done_rejected_control_true_done_accepted():
    obs = _obs([], text="Корзина пуста", url="https://shop.test/cart")
    with pytest.raises(fp.DoneRejected):
        fp.verify_done(obs, {"text_contains": ["В корзине: 1"]})
    with pytest.raises(fp.DoneRejected):
        fp.verify_done(obs, {})                                                       # no criteria ≠ success
    ok = _obs([], text="В корзине: 1", url="https://shop.test/cart")
    assert fp.verify_done(ok, {"text_contains": ["В корзине: 1"], "url_contains": "/cart"})["verified"]


# ------------------------------------------------------------------ execution bookkeeping

class _FakeMgr:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def click(self, sid, **kw):
        self.calls.append(("click", kw))
        if self.fail:
            raise TimeoutError("navigation after click did not settle")
        return {}

    async def type_text(self, sid, **kw):
        self.calls.append(("type", kw))
        return {}

    async def select(self, sid, **kw):
        self.calls.append(("select", kw))
        return {}


async def test_mutation_never_blindly_retried_and_logged_before_observe():
    mgr, log = _FakeMgr(fail=True), fp.ExecutionLog()
    row = await fp.execute_once(mgr, 1, {"op": "CLICK", "ref": "e3-1"}, log)
    assert row["status"] == "uncertain" and len(mgr.calls) == 1
    assert log.entries == [row] and row["observed"] is False
    ok_mgr = _FakeMgr()
    row2 = await fp.execute_once(ok_mgr, 1, {"op": "CLICK", "ref": "e3-1"}, log)     # control
    assert row2["status"] == "executed"
    assert ok_mgr.calls[0][1]["approved"] is False and ok_mgr.calls[0][1]["actor"] == "agent"


async def test_type_without_validated_text_is_not_dispatched():
    mgr, log = _FakeMgr(), fp.ExecutionLog()
    with pytest.raises(fp.Escalate):
        await fp.execute_once(mgr, 1, {"op": "TYPE_TEXT", "ref": "e3-0"}, log)
    assert mgr.calls == [] and log.entries[0]["status"] == "not_dispatched"


async def test_phase2_execution_is_not_available():
    with pytest.raises(fp.Escalate) as exc:
        await fp.execute_step()
    assert exc.value.reason == "phase_not_enabled"
    assert jev_config.load_browser().may_execute is False


# ------------------------------------------------------------------ text helper

@pytest.mark.parametrize("raw", ['{"text": null}', '{"text": ""}', '{"text": "a", "extra": 1}',
                                 'Sure! {"text": "x"}', '{"text": 5}', json.dumps({"text": "x" * 2001})])
def test_text_helper_output_rejected(raw):
    with pytest.raises(JevInvalidResponse):
        fp.validate_text_output(raw)


async def test_gateway_text_helper_uses_supplied_gateway_control():
    seen = []

    async def chat(messages):
        seen.append(messages)
        return '{"text": "Prague"}'
    assert await fp.GatewayTextHelper(chat).generate("fly to Prague", "Where to?", "Flights") == "Prague"
    assert seen and seen[0][0]["role"] == "system"


# ------------------------------------------------------------------ shadow step

def test_shadow_step_disabled_makes_no_call(client, mock, monkeypatch):
    monkeypatch.delenv("BOSSMAN_JEV_BROWSER_ENABLED")
    rec = fp.shadow_step(client, _obs(ITEMS), "goal", {"op": "CLICK", "ref": "e3-1"})
    assert rec["fallback_reason"] == "disabled" and mock.requests == []


def test_shadow_step_records_agreement_and_never_executes(client, mock):
    mock.script = [("json", _respond("CLICK", "2"))]
    rec = fp.shadow_step(client, _obs(ITEMS), "goal", {"op": "CLICK", "ref": "e3-1", "option": None})
    assert rec["agreement"] == {"operation": True, "exact": True} and rec["executed_by_jev"] is False
    rec2 = fp.shadow_step(client, _obs(ITEMS), "goal", {"op": "TYPE_TEXT", "ref": "e3-0", "option": None})
    assert rec2["agreement"]["exact"] is False


def test_shadow_step_provider_failure_is_fallback(client, mock):
    mock.script = [("status", 500)]
    rec = fp.shadow_step(client, _obs(ITEMS), "goal", None)
    assert rec["fallback_reason"] == "http_5xx" and rec["proposal"] is None


# ------------------------------------------------------------------ real Chromium, real BrowserManager

pytestmark_browser = pytest.mark.skipif(not chromium_available(), reason=browser_reason())

PAGES = {
    "clean.html": "<!doctype html><meta charset=utf-8><input name=q aria-label=Query>"
                  "<button type=button onclick=\"document.body.dataset.x=1\">Go</button>"
                  "<select name=c><option value=a>A</option><option value=b>B</option></select>",
    "frame.html": "<!doctype html><meta charset=utf-8><button>Go</button>"
                  "<iframe srcdoc='<button>in</button>' width=300 height=200></iframe>",
    "canvas.html": "<!doctype html><meta charset=utf-8><button>Go</button><canvas width=300 height=150></canvas>",
    "upload.html": "<!doctype html><meta charset=utf-8><input type=file name=f><button>Go</button>",
}


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("BCC_BROWSER_ALLOW_PRIVATE", "1")
    root = tmp_path / "site"
    root.mkdir()
    for name, html in PAGES.items():
        (root / name).write_text(html, encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
async def mgr(tmp_path):
    from bcc.features.browser import _patch_executable
    manager = BrowserManager(tmp_path / "browser")
    _patch_executable(manager)
    await manager.start(1, BrowserPolicy.from_dict(None), headless=True)
    yield manager
    await manager.close()


@pytestmark_browser
async def test_live_observation_indexes_only_observed_elements(mgr, site):
    await mgr.navigate(1, f"{site}/clean.html", actor="agent", approved=True)
    obs = await mgr.jev_observe(1, actor="agent", approved=True)
    assert fp.unsupported_reasons(obs) == []
    _, targets = fp.action_space(obs)
    observed = {i["ref"] for i in obs["interactive"]}
    assert {e["ref"] for op in targets.values() for e in op.values()} <= observed
    assert {e["option"] for e in targets["SELECT"].values()} == {"a", "b"}


@pytestmark_browser
@pytest.mark.parametrize("page,feature", [("frame.html", "iframes"), ("canvas.html", "canvas"),
                                          ("upload.html", "file_inputs")])
async def test_live_unsupported_pages_escalate(mgr, site, page, feature):
    await mgr.navigate(1, f"{site}/{page}", actor="agent", approved=True)
    obs = await mgr.jev_observe(1, actor="agent", approved=True)
    assert feature in fp.unsupported_reasons(obs)


@pytestmark_browser
async def test_live_stale_after_existing_agent_types(mgr, site):
    await mgr.navigate(1, f"{site}/clean.html", actor="agent", approved=True)
    obs = await mgr.jev_observe(1, actor="agent", approved=True)
    ref = next(i["ref"] for i in obs["interactive"] if i.get("name") == "q")
    p = fp.Proposal("CLICK", None, ref, None, 1, obs["generation"], fp.observation_fingerprint(obs), obs["url"])
    fp.check_fresh(p, await mgr.jev_current(1, actor="agent", approved=True))       # control: unchanged page
    await mgr.type_text(1, ref=ref, text="changed", actor="agent", approved=True)
    await asyncio.sleep(0.05)
    with pytest.raises(fp.StalePage):
        fp.check_fresh(p, await mgr.jev_current(1, actor="agent", approved=True))
