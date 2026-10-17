"""Rights gate: nothing is cut or queued without the owner's rights approval (negative + positive controls)."""
import shutil

import pytest

from shortsfactory import pipeline, rights


def _files(env):
    d = env.app.state.data_dir / "shorts"
    return list(d.rglob("*.mp4")) if d.exists() else []


def test_unapproved_source_is_refused_and_nothing_is_cut(env, videos):
    src = env.register(videos["land"])
    assert src["approved_by_owner"] is False and src["rights_approval"] is None
    r = env.req("POST", "/jobs/process", "editor", json={"source_id": src["id"], "k": 1})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "RIGHTS_NOT_APPROVED"
    assert _files(env) == [] and env.store.shorts() == []
    with pytest.raises(rights.RightsError):
        pipeline.process_source(env.store, src["id"], env.app.state.data_dir, k=1, transcriber=lambda *a, **k: 1 / 0)
    with pytest.raises(rights.RightsError):
        pipeline.enqueue(env.store, src["id"], {"file": "x"}, "ready")
    assert env.store.shorts() == []
    ev = [e["event"] for e in env.store.audit_events()]
    assert "rights.refused" in ev


def test_positive_control_after_owner_approval_the_same_source_is_cut(env, videos):
    src = env.register(videos["land"])
    env.approve_rights(src["id"])
    r = env.req("POST", "/jobs/process", "editor", json={"source_id": src["id"], "k": 1})
    assert r.status_code == 200, r.text
    j = env.wait(r.json()["id"])
    assert j["status"] == "completed", j
    assert len(env.store.shorts("ready")) == 1 and len(_files(env)) == 1


def test_only_owner_can_approve_rights_and_must_confirm(env, videos):
    src = env.register(videos["land"])
    body = {"approve": True, "confirm": "ДА", "note": "моя запись"}
    assert env.req("POST", f"/sources/{src['id']}/rights", "editor", json=body).status_code == 403
    assert env.req("POST", f"/sources/{src['id']}/rights", "viewer", json=body).status_code == 403
    r = env.req("POST", f"/sources/{src['id']}/rights", "owner", json={**body, "confirm": "ok"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "CONFIRM_REQUIRED"
    assert env.store.get_source(src["id"])["approved_by_owner"] is False


def test_approval_flag_cannot_be_patched_and_changing_path_voids_it(env, videos):
    src = env.register(videos["land"])
    r = env.req("PATCH", f"/sources/{src['id']}", "editor", json={"changes": {"approved_by_owner": True}})
    assert r.status_code == 403
    env.approve_rights(src["id"])
    r = env.req("PATCH", f"/sources/{src['id']}", "editor", json={"changes": {"path": str(videos["small"])}})
    assert r.status_code == 200 and r.json()["source"]["approved_by_owner"] is False
    with pytest.raises(rights.RightsError):
        rights.check(env.store.get_source(src["id"]))


def test_file_swapped_after_approval_is_refused(env, videos, tmp_path):
    p = tmp_path / "mine.mp4"
    shutil.copy(videos["land"], p)
    src = env.register(p)
    env.approve_rights(src["id"])
    rights.check(env.store.get_source(src["id"]))            # ok before
    shutil.copy(videos["small"], p)                           # same path, different content
    with pytest.raises(rights.RightsError) as e:
        rights.check(env.store.get_source(src["id"]))
    assert e.value.code == "SOURCE_CHANGED"


def test_licensed_source_needs_licence_reference(env, videos):
    r = env.req("POST", "/sources", "editor", json={"title": "film", "kind": "licensed", "path": str(videos["land"]),
                                                    "rights_note": "разрешение правообладателя"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "LICENCE_REQUIRED"
    ok = env.req("POST", "/sources", "editor", json={"title": "film", "kind": "licensed", "path": str(videos["land"]),
                                                     "rights_note": "разрешение правообладателя", "licence_url": "https://example.org/licence"})
    assert ok.status_code == 200 and ok.json()["source"]["approved_by_owner"] is False


def test_approval_cannot_be_set_at_registration(env, videos):
    r = env.req("POST", "/sources", "editor", json={"title": "xx", "kind": "own", "path": str(videos["land"]), "rights_note": "моё",
                                                    "approved_by_owner": True})
    assert r.status_code == 200 and r.json()["source"]["approved_by_owner"] is False
