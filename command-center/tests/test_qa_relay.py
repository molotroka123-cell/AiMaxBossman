"""B2 — the cloud QA bridge, and everything it must refuse.

Cloud QA could not test the local system: `127.0.0.1` and `bossman.local` are
blocked by the browser tool's SSRF policy, so tasks 29 and 30 honestly reported
"a public URL is needed" and stopped. The fix is not a softer SSRF policy — it
is a bridge where the cloud side never reaches in, only asks for a named
allowlisted capability that the local side runs itself.

The master doc names the cases that must be covered: auth failure, replay,
expired task, forbidden action, oversized evidence, secret redaction, and
disconnect/reconnect. Each has a test below, and most of this file is refusals,
because a relay is defined by what it will not do.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc import qa_relay as relay
from bcc.features import qa_relay as feature


SECRET = "test-bridge-secret-value"


@pytest.fixture
def bridge():
    return relay.Bridge()


@pytest.fixture(autouse=True)
def fresh_process_bridge():
    """The feature holds one bridge per process; tests must not inherit each
    other's nonces or rate-limit budget."""
    feature.BRIDGE = relay.Bridge()
    yield
    feature.BRIDGE = relay.Bridge()


async def run(bridge, svc, job, *, secret=SECRET, enabled=True, now=None):
    return await bridge.handle(svc, job, secret=secret, enabled=enabled, now=now)


# ---------------------------------------------------------------- signing

def test_a_tampered_job_does_not_verify():
    job = relay.build_job(SECRET, "apps.smoke")
    assert relay.verify(SECRET, job) is True
    for field, value in [("capability", "health.smoke"), ("params", {"x": 1}),
                         ("expires_at", job["expires_at"] + 10_000),
                         ("nonce", "other"), ("job_id", "deadbeef")]:
        assert relay.verify(SECRET, {**job, field: value}) is False, field


def test_a_job_signed_with_another_secret_does_not_verify():
    job = relay.build_job("someone-elses-secret", "apps.smoke")
    assert relay.verify(SECRET, job) is False


def test_an_unsigned_job_never_verifies():
    job = relay.build_job(SECRET, "apps.smoke")
    assert relay.verify(SECRET, {**job, "sig": ""}) is False
    assert relay.verify("", job) is False           # no secret configured


def test_ttl_is_clamped_at_construction():
    job = relay.build_job(SECRET, "apps.smoke", ttl_seconds=10 ** 6)
    assert job["expires_at"] - job["issued_at"] <= relay.MAX_TTL_SECONDS


# ---------------------------------------------------------------- refusals

async def test_a_valid_job_is_refused_while_the_bridge_is_off(env, bridge):
    """The owner's kill switch beats a perfect signature. Checked before the
    signature so switching off is not a race against a signed request."""
    job = relay.build_job(SECRET, "apps.smoke")
    out = await run(bridge, env.svc, job, enabled=False)
    assert out.status == relay.REJECTED_DISABLED and out.evidence == {}


async def test_a_bad_signature_runs_nothing(env, bridge):
    job = relay.build_job("wrong-secret", "apps.smoke")
    out = await run(bridge, env.svc, job)
    assert out.status == relay.REJECTED_SIGNATURE and out.evidence == {}


async def test_no_configured_secret_refuses_everything(env, bridge):
    job = relay.build_job(SECRET, "apps.smoke")
    out = await run(bridge, env.svc, job, secret="")
    assert out.status == relay.REJECTED_SIGNATURE


async def test_a_replayed_job_is_refused_even_with_a_perfect_signature(env, bridge):
    job = relay.build_job(SECRET, "apps.smoke")
    first = await run(bridge, env.svc, job)
    second = await run(bridge, env.svc, job)
    assert first.status == relay.OK
    assert second.status == relay.REJECTED_REPLAY and second.evidence == {}


async def test_an_expired_job_is_refused(env, bridge):
    now = relay._now()
    job = relay.build_job(SECRET, "apps.smoke", ttl_seconds=60, issued_at=now - 3600)
    out = await run(bridge, env.svc, job, now=now)
    assert out.status == relay.REJECTED_EXPIRED


async def test_a_long_lived_job_is_refused_even_before_it_expires(env, bridge):
    """A signed job that lives for a day is a credential, not a request. The
    signature covers `expires_at`, so this cannot be fixed by re-signing."""
    now = relay._now()
    job = dict(job_id="a" * 16, nonce="n", capability="apps.smoke", params={},
               issued_at=now, expires_at=now + 10 * relay.MAX_TTL_SECONDS)
    job["sig"] = relay.sign(SECRET, job)
    out = await run(bridge, env.svc, job, now=now)
    assert out.status == relay.REJECTED_EXPIRED


@pytest.mark.parametrize("capability", [
    "terminal.run", "shell", "apps.start", "filesystem.read", "",
    "apps.smoke ", "APPS.SMOKE", "../apps.smoke", "video_studio.delete",
])
async def test_only_registered_capabilities_can_be_named(env, bridge, capability):
    """The cloud side chooses FROM a menu; it never writes one. A capability
    that is not in the registry cannot be reached however it is spelled."""
    job = relay.build_job(SECRET, capability)
    out = await run(bridge, env.svc, job)
    assert out.status == relay.REJECTED_CAPABILITY


async def test_undeclared_parameters_are_a_refusal_not_an_ignored_field(env, bridge):
    """An ignored extra is how a path or a command gets smuggled in later. The
    param schema is closed."""
    job = relay.build_job(SECRET, "apps.smoke", {"path": "/etc/passwd"})
    out = await run(bridge, env.svc, job)
    assert out.status == relay.REJECTED_PARAMS and "path" in out.detail


async def test_params_must_be_an_object(env, bridge):
    job = relay.build_job(SECRET, "apps.smoke")
    job["params"] = ["/etc/passwd"]
    job["sig"] = relay.sign(SECRET, job)
    out = await run(bridge, env.svc, job)
    assert out.status == relay.REJECTED_PARAMS


async def test_a_non_object_job_is_refused_without_crashing(env, bridge):
    for junk in [None, "job", 7, [1, 2], True]:
        out = await run(bridge, env.svc, junk)
        assert out.status == relay.REJECTED_SIGNATURE


async def test_the_rate_limit_bounds_what_the_cloud_side_can_ask_for(env, bridge):
    now = relay._now()
    statuses = []
    for _ in range(relay.RATE_LIMIT_JOBS + 3):
        job = relay.build_job(SECRET, "health.smoke", issued_at=now)
        statuses.append((await run(bridge, env.svc, job, now=now)).status)
    assert statuses.count(relay.OK) == relay.RATE_LIMIT_JOBS
    assert statuses[-1] == relay.REJECTED_RATE


async def test_the_rate_limit_window_recovers(env, bridge):
    """"Disconnect/reconnect": a bridge that throttled an hour ago must serve
    the next session."""
    now = relay._now()
    for _ in range(relay.RATE_LIMIT_JOBS):
        await run(bridge, env.svc, relay.build_job(SECRET, "health.smoke", issued_at=now), now=now)
    blocked = await run(bridge, env.svc, relay.build_job(SECRET, "health.smoke", issued_at=now), now=now)
    assert blocked.status == relay.REJECTED_RATE

    later = now + relay.RATE_LIMIT_WINDOW_SECONDS + 1
    ok = await run(bridge, env.svc, relay.build_job(SECRET, "health.smoke", issued_at=later),
                   now=later)
    assert ok.status == relay.OK


# --------------------------------------------------------- evidence hygiene

def test_secrets_are_redacted_from_evidence():
    out = relay.sanitize({"api_key": "sk-or-v1-abcdef0123456789",
                          "nested": {"token": "ghp_secretvalue123456"},
                          "safe": "ok"})
    blob = json.dumps(out)
    assert "sk-or-v1-abcdef0123456789" not in blob
    assert "ghp_secretvalue123456" not in blob
    assert out["safe"] == "ok"


@pytest.mark.parametrize("text,forbidden", [
    ("/home/timur/projects/bossman/db.sqlite", "timur"),
    (r"C:\Users\timur\AppData\token.txt", "timur"),
    ("/etc/shadow", "shadow"),
    ("/var/lib/private/keys", "private"),
])
def test_private_paths_are_scrubbed_from_evidence(text, forbidden):
    """Evidence is a verdict, not a filesystem map. An absolute path leaks the
    owner's username and layout to whoever reads the QA report."""
    assert forbidden not in relay.scrub_text(text)


def test_scrubbing_leaves_ordinary_text_alone():
    """Negative control: over-scrubbing makes evidence useless, which is its own
    failure — QA that cannot read the answer learns nothing."""
    for text in ["video_studio ok", "6 templates", "project count: 1",
                 "HTTP 200", "capability APPS_ACTION verified"]:
        assert relay.scrub_text(text) == text


async def test_oversized_evidence_is_truncated_not_sent(env, bridge):
    cap = relay.Capability("test.huge", "returns too much",
                           lambda svc, params: _huge())
    bridge.registry = relay.Registry()
    bridge.registry.register(cap)
    out = await run(bridge, env.svc, relay.build_job(SECRET, "test.huge"))
    assert out.status == relay.OK and out.truncated is True
    assert out.evidence["truncated"] is True
    assert len(json.dumps(out.evidence)) < relay.MAX_EVIDENCE_BYTES


async def _huge():
    return {"blob": "x" * (relay.MAX_EVIDENCE_BYTES * 2)}


async def test_a_handler_returning_unstructured_data_is_a_failure(env, bridge):
    """Structured evidence only: a handler that returns a wall of text has no
    way to be sanitized field by field."""
    bridge.registry = relay.Registry()
    bridge.registry.register(relay.Capability(
        "test.text", "returns a string", lambda svc, params: _text()))
    out = await run(bridge, env.svc, relay.build_job(SECRET, "test.text"))
    assert out.status == relay.FAILED


async def _text():
    return "just a string"


async def test_a_slow_handler_is_bounded(env, bridge, monkeypatch):
    monkeypatch.setattr(relay, "MAX_ACTION_SECONDS", 0.05)
    bridge.registry = relay.Registry()
    bridge.registry.register(relay.Capability(
        "test.slow", "never returns", lambda svc, params: asyncio.sleep(5)))
    out = await run(bridge, env.svc, relay.build_job(SECRET, "test.slow"))
    assert out.status == relay.TIMED_OUT


async def test_a_failing_handler_reports_a_type_not_a_traceback(env, bridge):
    """An exception message can carry a path or a query. The cloud side gets the
    exception type; the details stay in the local logs."""
    bridge.registry = relay.Registry()

    async def boom(svc, params):
        raise RuntimeError("/home/timur/secret/db.sqlite is locked")

    bridge.registry.register(relay.Capability("test.boom", "raises", boom))
    out = await run(bridge, env.svc, relay.build_job(SECRET, "test.boom"))
    assert out.status == relay.FAILED
    assert out.detail == "RuntimeError" and "timur" not in json.dumps(out.to_dict())


# ------------------------------------------------------------------- audit

async def test_every_accepted_and_refused_job_is_audited(env, bridge):
    await run(bridge, env.svc, relay.build_job(SECRET, "apps.smoke"))
    await run(bridge, env.svc, relay.build_job("wrong", "apps.smoke"))
    await run(bridge, env.svc, relay.build_job(SECRET, "not.a.capability"))
    statuses = [row["status"] for row in bridge.audit]
    assert statuses == [relay.OK, relay.REJECTED_SIGNATURE, relay.REJECTED_CAPABILITY]


# ------------------------------------------------- the three QA capabilities

@pytest.mark.parametrize("capability,key", [
    ("apps.smoke", "app_count"),
    ("health.smoke", "database"),
    # The three systems cloud QA could not reach at all.
    ("video_studio.smoke", "project_count"),
    ("web_designer.smoke", "template_count"),
])
async def test_the_qa_capabilities_return_structured_evidence(env, bridge, capability, key):
    out = await run(bridge, env.svc, relay.build_job(SECRET, capability))
    assert out.status == relay.OK, out.detail
    assert key in out.evidence


async def test_apps_smoke_reports_the_control_policy_cloud_qa_could_not_see(env, bridge):
    """This is the evidence the cloud run needed and could not get: whether Apps
    control is on, and why."""
    out = await run(bridge, env.svc, relay.build_job(SECRET, "apps.smoke"))
    assert out.evidence["control_enabled"] is False
    assert out.evidence["control_source"] in ("default", "environment", "deployment_lock")


async def test_no_capability_can_read_a_file_or_run_a_command():
    """Structural: the allowlist is the security boundary, so assert its shape
    rather than trusting that nobody adds a path parameter later."""
    for cap in relay.REGISTRY.catalog():
        assert not cap["required"], f"{cap['name']} требует параметров от облака"
        for name in cap["params"]:
            assert name not in ("path", "command", "cwd", "file", "url", "argv")


# -------------------------------------------------------------- the feature

async def test_the_bridge_is_off_and_unconfigured_by_default(env):
    body = (await env.client.get("/api/qa-relay")).json()
    assert body["enabled"] is False and body["configured"] is False
    assert set(body["capabilities"][0]) == {"name", "description", "params", "required"}


async def test_enabling_without_a_secret_is_refused(env):
    res = await env.client.post("/api/qa-relay/enable")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "QA_RELAY_NO_SECRET"


async def test_the_secret_is_shown_once_and_never_again(env):
    secret = (await env.client.post("/api/qa-relay/secret")).json()["secret"]
    assert len(secret) >= 32
    state = (await env.client.get("/api/qa-relay")).json()
    assert secret not in json.dumps(state)
    assert state["configured"] is True


async def test_the_full_owner_flow_then_a_real_job(env):
    secret = (await env.client.post("/api/qa-relay/secret")).json()["secret"]
    assert (await env.client.post("/api/qa-relay/enable")).json()["enabled"] is True

    job = relay.build_job(secret, "apps.smoke")
    body = (await env.client.post("/api/qa-relay/job", json=job)).json()
    assert body["status"] == relay.OK and "app_count" in body["evidence"]

    # The kill switch takes effect on the very next job.
    await env.client.post("/api/qa-relay/disable")
    again = (await env.client.post("/api/qa-relay/job",
                                   json=relay.build_job(secret, "apps.smoke"))).json()
    assert again["status"] == relay.REJECTED_DISABLED


async def test_rotating_the_secret_revokes_the_old_cloud_side(env):
    old = (await env.client.post("/api/qa-relay/secret")).json()["secret"]
    await env.client.post("/api/qa-relay/enable")
    stale = relay.build_job(old, "apps.smoke")
    await env.client.post("/api/qa-relay/secret")               # rotate
    body = (await env.client.post("/api/qa-relay/job", json=stale)).json()
    assert body["status"] == relay.REJECTED_SIGNATURE


async def test_the_audit_endpoint_shows_refusals_too(env):
    secret = (await env.client.post("/api/qa-relay/secret")).json()["secret"]
    await env.client.post("/api/qa-relay/enable")
    await env.client.post("/api/qa-relay/job", json=relay.build_job(secret, "apps.smoke"))
    await env.client.post("/api/qa-relay/job", json=relay.build_job("bad", "apps.smoke"))
    entries = (await env.client.get("/api/qa-relay/audit")).json()["entries"]
    assert {e["status"] for e in entries} == {relay.OK, relay.REJECTED_SIGNATURE}
