"""Architecture P0 step 2: the /m/op/ UI reaches the PC operator through ONE path. Every route op.js calls exists in the
mobile proxy, and the agent id it sends comes only from GET /operator (Bossman's pc_operator), never from a list of
agents, qwen_operator or pc_agent."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "bossmobile"
OPJS = (ROOT / "ui" / "op" / "op.js").read_text(encoding="utf-8")
APIPY = (ROOT / "api.py").read_text(encoding="utf-8")

CALLED = {(m.group(1), re.sub(r"\$\{[^}]*\}", "{x}", m.group(2))) for m in
          re.finditer(r"api\('(GET|POST|PUT|PATCH|DELETE)',\s*[`']([^`']*)[`']", OPJS)}
ROUTES = {(m.group(1).upper(), re.sub(r"\{[^}]*\}", "{x}", m.group(2))) for m in
          re.finditer(r'@app\.(get|post|put|patch|delete)\("/m/api(/[^"]*)"', APIPY)}


def test_every_route_the_operator_ui_calls_is_proxied():
    assert CALLED, "op.js api() calls were not found (regex drift)"
    missing = sorted(c for c in CALLED if c not in ROUTES)
    assert not missing, missing


def test_agent_id_comes_only_from_the_operator_endpoint():
    m = re.search(r"const opAgent = \(\) => \(([^;]*)\);", OPJS)
    assert m and "S.op.agent" in m.group(1)
    sends = re.findall(r"agent_id:\s*([^,}\n]+)", OPJS)
    assert "const agentId = opAgent();" in OPJS
    assert sends and all(s.strip() in ("opAgent()", "agentId") for s in sends), sends


def test_operator_ui_never_touches_other_operator_implementations():
    for bad in ("/agents", "qwen", "pc-agent", "pc_agent", "/work", "/agent/router", "/agent/models"):
        assert not any(bad in p for _m, p in CALLED), bad
        assert f"'{bad}" not in OPJS and f"`{bad}" not in OPJS, bad
    # the only /agent/* use is the session transport (create-less: patch, messages, compact)
    agent_paths = sorted(p for _m, p in CALLED if p.startswith("/agent/"))
    assert agent_paths == ["/agent/sessions/{x}", "/agent/sessions/{x}/compact", "/agent/sessions/{x}/messages"]
