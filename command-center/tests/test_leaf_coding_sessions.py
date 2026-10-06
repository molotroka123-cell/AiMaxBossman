"""authored_by_lane agcloud: behavior tests for bcc.features.coding_sessions (HTTP layer, real manager)."""
from __future__ import annotations

from bcc.coding_session import CodingSessionError
from bcc.features import coding_sessions


def test_feature_registered_with_full_chain():
    assert coding_sessions.FEATURE.name == "coding_sessions"
    paths = {r.path for r in coding_sessions.router.routes}
    assert {"/coding-sessions", "/coding-sessions/{session_id}/diff", "/coding-sessions/{session_id}/merge",
            "/coding-sessions/{session_id}/merge_preview", "/coding-sessions/{session_id}/discard"} <= paths


def test_error_mapping_is_honest():
    assert coding_sessions._err(CodingSessionError("unknown session x")).status_code == 404
    assert coding_sessions._err(CodingSessionError("session already active")).status_code == 409
    assert coding_sessions._err(CodingSessionError("bad ref")).status_code == 400


async def _listed(env):
    r = await env.client.get("/api/coding-sessions")
    assert r.status_code == 200
    return r.json()


async def test_list_empty_and_unknown_session_is_404(env):
    assert not await _listed(env)
    assert (await env.client.get("/api/coding-sessions/nope")).status_code == 404


async def test_create_rejects_missing_and_out_of_roots_repo(env, tmp_path):
    r = await env.client.post("/api/coding-sessions", json={"session_id": "s1", "source_repo": "  "})
    assert r.status_code == 400
    outside = tmp_path / "somewhere_not_allowed"
    outside.mkdir()
    r = await env.client.post("/api/coding-sessions", json={"session_id": "s1", "source_repo": str(outside)})
    assert r.status_code == 403                                  # confinement to allowed roots
    r = await env.client.post("/api/coding-sessions",
                              json={"session_id": "s1", "source_repo": str(tmp_path / "nonexistent")})
    assert r.status_code == 400
    assert not await _listed(env)                                # nothing was created by rejected requests


async def test_merge_and_discard_unknown_session_never_succeed(env):
    assert (await env.client.post("/api/coding-sessions/ghost/merge", json={})).status_code == 404
    assert (await env.client.post("/api/coding-sessions/ghost/discard")).status_code == 404
    assert (await env.client.post("/api/coding-sessions/ghost/merge_preview", json={})).status_code == 404
