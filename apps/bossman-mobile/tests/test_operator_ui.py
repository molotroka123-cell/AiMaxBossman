"""The phone UI «OpenCode on the phone» (owner 11.11): /m/ lands on the one-chat PC-operator UI at /m/op/, the classic
pult stays at /m/index.html, and /m/api/operator* proxies Bossman's /api/operator (with a fallback for an older
Bossman that has no such route)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest

import conftest
from conftest import REAL_CSRF, SID, Env

UI = Path(__file__).resolve().parents[1] / "bossmobile" / "ui"
OP = UI / "op"

OPERATOR = {"agent": {"id": 1, "name": "Qwen Оператор (компьютер)"},
            "model": {"id": 11, "alias": "local-qwen", "name": "qwen:latest", "context_window": 32768, "health": "ok",
                      "loaded": False},
            "candidates": [{"id": 11, "alias": "local-qwen", "name": "qwen:latest"},
                           {"id": 14, "alias": "qwen-big", "name": "qwen3:32b"}],
            "total_control": False, "ready": True, "reason": None,
            "internal_secret_path": "C:/never/shown"}


@pytest.fixture
def op_env(tmp_path, monkeypatch):
    """An Env whose fake Bossman knows the new /api/operator routes (a backend of 11.11 or later)."""
    original = conftest.FakeBossman.handler
    state = {"op": json.loads(json.dumps(OPERATOR)), "warm": {"ok": True, "loaded": True, "load_ms": 4200, "error": None,
                                                             "extra": "x"}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path.startswith("/api/operator"):
            body = request.content.decode("utf-8", "replace") if request.content else ""
            self.calls.append({"method": method, "path": path, "query": dict(request.url.params), "body": body,
                               "headers": dict(request.headers)})
            if f"bcc_session={SID}" not in request.headers.get("cookie", ""):
                return httpx.Response(401, json={"error": {"message": "нужна аутентификация"}})
            if method != "GET" and request.headers.get("x-bcc-csrf") != REAL_CSRF:
                return httpx.Response(403, json={"error": {"message": "csrf", "code": "csrf"}})
            if path == "/api/operator" and method == "GET":
                return httpx.Response(200, json=state["op"])
            if path == "/api/operator" and method == "PUT":
                b = json.loads(body or "{}")
                if b.get("model_id") == 999:
                    return httpx.Response(404, json={"error": {"message": "нет такой модели", "code": "OPERATOR_MODEL_UNKNOWN"}})
                if "model_id" in b:
                    m = next(c for c in state["op"]["candidates"] if c["id"] == b["model_id"])
                    state["op"]["model"] = {**m, "context_window": 8192, "health": "ok", "loaded": False}
                if "total_control" in b:
                    state["op"]["total_control"] = bool(b["total_control"])
                return httpx.Response(200, json=state["op"])
            if path == "/api/operator/warm" and method == "POST":
                return httpx.Response(200, json=state["warm"])
        return original(self, request)

    monkeypatch.setattr(conftest.FakeBossman, "handler", handler)
    e = Env(tmp_path)
    e.state = state
    return e


def _op_calls(e):
    return [c for c in e.fake.calls if c["path"].startswith("/api/operator")]


# ------------------------------------------------------------------ landing + static shell
@pytest.mark.parametrize("path", ["/m/", "/m"])
def test_root_redirects_to_operator_ui(env, path):
    r = env.c.get(path, follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/m/op/"
    assert r.headers["cache-control"] == "no-store"


def test_pairing_link_lands_on_operator_ui(env):
    # the #pair= fragment is never sent to the server; the browser re-attaches it after the redirect
    r = env.c.get("/m/", follow_redirects=True)
    assert r.status_code == 200 and str(r.url).endswith("/m/op/")
    assert 'src="op.js"' in r.text


def test_operator_ui_served_with_strict_csp_and_no_cache(env):
    r = env.c.get("/m/op/")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp and "style-src 'self'" in csp
    assert r.headers["cache-control"] == "no-cache"
    html = r.text
    assert "Bossman" in html and "apple-mobile-web-app-capable" in html
    # no inline script, no <style>, no style= attribute, no on*= handler
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I):
        assert "src=" in m.group(1) and not m.group(2).strip(), m.group(0)
    assert not re.search(r"<style\b", html, re.I)
    assert not re.search(r"\sstyle\s*=", html, re.I)
    assert not re.search(r"\son[a-z]+\s*=", html, re.I)
    for asset, ctype in (("op.js", "javascript"), ("md.js", "javascript"), ("op.css", "text/css")):
        a = env.c.get(f"/m/op/{asset}")
        assert a.status_code == 200 and ctype in a.headers["content-type"], asset
        assert a.headers["cache-control"] == "no-cache", asset
    # the bundled font and icons resolve from /m/op/ (relative ../ paths)
    assert env.c.get("/m/fonts/InterVariable.woff2").status_code == 200
    assert "../fonts/InterVariable.woff2" in (OP / "op.css").read_text(encoding="utf-8")


def test_operator_ui_screen_has_only_the_agreed_controls():
    html = (OP / "index.html").read_text(encoding="utf-8")
    for el_id in ("btn-stop", "status-text", "btn-history", "input", "btn-photo", "btn-video", "btn-send", "log"):
        assert f'id="{el_id}"' in html, el_id
    # revised spec 11.11: NO model / provider / mode picker on the screen
    low = html.lower()
    for banned in ("<select", "провайдер", "provider", "выбор модели", "model-picker", "data-mode"):
        assert banned not in low, banned


def test_operator_js_never_injects_html():
    for f in OP.glob("*.js"):
        src = f.read_text(encoding="utf-8")
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert bad not in src, f"{f.name}: {bad}"


def test_classic_pult_still_served(env):
    r = env.c.get("/m/index.html")
    assert r.status_code == 200 and 'src="app.js"' in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert env.c.get("/m/app.js").status_code == 200
    assert env.c.get("/m/sw.js").headers["cache-control"] == "no-store"


# ------------------------------------------------------------------ /m/api/operator proxy
def test_operator_routes_require_session_and_csrf(op_env):
    assert op_env.c.get("/m/api/operator").status_code == 401
    op_env.login()
    assert op_env.c.put("/m/api/operator", json={"total_control": True}).status_code == 403
    assert op_env.c.post("/m/api/operator/warm").status_code == 403
    bad = {"X-BM-CSRF": "wrong"}
    assert op_env.c.put("/m/api/operator", json={"total_control": True}, headers=bad).status_code == 403
    assert op_env.c.post("/m/api/operator/warm", headers=bad).status_code == 403
    assert not [c for c in _op_calls(op_env) if c["method"] != "GET"], "a request without CSRF reached Bossman"


def test_operator_get_proxied_and_filtered(op_env):
    op_env.login()
    r = op_env.c.get("/m/api/operator")
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"agent", "model", "candidates", "total_control", "ready", "reason"}
    assert j["agent"] == {"id": 1, "name": "Qwen Оператор (компьютер)"}
    assert j["model"]["alias"] == "local-qwen" and j["model"]["loaded"] is False
    assert len(j["candidates"]) == 2 and j["total_control"] is False and j["ready"] is True
    assert r.headers["cache-control"] == "no-store"
    assert _op_calls(op_env)[-1]["method"] == "GET" and _op_calls(op_env)[-1]["path"] == "/api/operator"


def test_operator_put_forwards_only_known_fields(op_env):
    op_env.login()
    r = op_env.c.put("/m/api/operator", json={"model_id": 14}, headers={"X-BM-CSRF": op_env.csrf})
    assert r.status_code == 200, r.text
    assert r.json()["model"]["alias"] == "qwen-big"
    sent = _op_calls(op_env)[-1]
    assert sent["method"] == "PUT" and json.loads(sent["body"]) == {"model_id": 14}
    r = op_env.c.put("/m/api/operator", json={"total_control": True}, headers={"X-BM-CSRF": op_env.csrf})
    assert r.status_code == 200 and r.json()["total_control"] is True
    assert json.loads(_op_calls(op_env)[-1]["body"]) == {"total_control": True}
    # empty and unknown fields are refused by the phone, never forwarded
    n = len(_op_calls(op_env))
    assert op_env.c.put("/m/api/operator", json={}, headers={"X-BM-CSRF": op_env.csrf}).status_code == 422
    assert op_env.c.put("/m/api/operator", json={"model_id": 14, "provider": "x"},
                        headers={"X-BM-CSRF": op_env.csrf}).status_code == 422
    assert len(_op_calls(op_env)) == n


def test_operator_coded_404_is_a_real_answer_not_a_fallback(op_env):
    op_env.login()
    r = op_env.c.put("/m/api/operator", json={"model_id": 999}, headers={"X-BM-CSRF": op_env.csrf})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "OPERATOR_MODEL_UNKNOWN"


def test_operator_warm_proxied_and_normalised(op_env):
    op_env.login()
    r = op_env.post("/m/api/operator/warm")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "loaded": True, "load_ms": 4200, "error": None}
    assert _op_calls(op_env)[-1]["method"] == "POST" and _op_calls(op_env)[-1]["path"] == "/api/operator/warm"
    op_env.state["warm"] = {"ok": False, "loaded": False, "load_ms": "x", "error": "не хватает памяти"}
    assert op_env.post("/m/api/operator/warm").json() == {"ok": False, "loaded": False, "load_ms": 0,
                                                          "error": "не хватает памяти"}


def test_operator_fallback_on_old_bossman(authed):
    # the conftest Bossman has no /api/operator (404 without a code): the phone builds the same shape itself
    r = authed.c.get("/m/api/operator")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["agent"]["id"] == 1                      # the local agent with computer.observe = the PC operator
    assert j["model"]["alias"] == "local-qwen"
    assert j["candidates"] == [] and j["total_control"] is False and j["ready"] is True
    for k in ("agent", "model", "candidates", "total_control", "ready", "reason"):
        assert k in j
    # no model swap / warm-up on an old Bossman: an honest coded error the UI understands
    for resp in (authed.c.put("/m/api/operator", json={"model_id": 11}, headers={"X-BM-CSRF": authed.csrf}),
                 authed.post("/m/api/operator/warm")):
        assert resp.status_code == 501 and resp.json()["detail"]["code"] == "BM_OPERATOR_UNSUPPORTED"
