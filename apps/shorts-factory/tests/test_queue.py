"""Queue + approval gate: youtube.upload is reachable only for owner-approved items; default privacy private."""
import sqlite3

from conftest import Env, FakeYouTube


def ready_item(env, videos, ai=False):
    src = env.register(videos["land"], ai=ai)
    env.approve_rights(src["id"])
    j = env.wait(env.req("POST", "/jobs/process", "editor", json={"source_id": src["id"], "k": 1}).json()["id"])
    assert j["status"] == "completed", j
    it = env.store.shorts()[0]
    assert it["state"] == "ready" and it["validation"]["ok"]
    return it


def test_publish_refused_unless_approved_and_upload_never_called(env, videos):
    it = ready_item(env, videos)
    r = env.req("POST", f"/queue/{it['id']}/publish", "owner", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_APPROVED"
    assert env.yt.uploads == []
    assert env.req("POST", f"/queue/{it['id']}/approve", "editor", json={}).status_code == 403
    assert env.req("POST", f"/queue/{it['id']}/publish", "editor", json={}).status_code == 403
    assert env.yt.uploads == [] and env.store.get_short(it["id"])["state"] == "ready"


def test_approve_then_publish_uses_private_and_disclosure(env, videos):
    it = ready_item(env, videos, ai=True)
    r = env.req("POST", f"/queue/{it['id']}/approve", "owner", json={"note": "да"})
    assert r.status_code == 200 and r.json()["state"] == "approved_by_owner"
    r = env.req("POST", f"/queue/{it['id']}/publish", "owner", json={})
    assert r.status_code == 200, r.text
    assert len(env.yt.uploads) == 1
    up = env.yt.uploads[0]
    assert up["privacy"] == "private" and up["contains_synthetic_media"] is True and up["path"] == it["file"]
    assert len(up["title"]) <= 100 and "shorts" in up["tags"]
    assert env.store.get_short(it["id"])["state"] == "published"
    assert env.req("POST", f"/queue/{it['id']}/publish", "owner", json={}).status_code == 409     # no double upload
    assert len(env.yt.uploads) == 1


def test_slot_scheduling_is_private_with_publish_at(env, videos):
    it = ready_item(env, videos)
    env.req("POST", f"/queue/{it['id']}/approve", "owner", json={"privacy": "public"})
    r = env.req("POST", f"/queue/{it['id']}/publish", "owner", json={"use_slot": True})
    assert r.status_code == 200, r.text
    up = env.yt.uploads[0]
    assert up["publish_at"] and up["publish_at"].endswith("Z") and up["privacy"] == "private"
    assert env.store.get_short(it["id"])["state"] == "scheduled"


def test_not_connected_keeps_item_approved(tmp_path, videos):
    env = Env(tmp_path, youtube=FakeYouTube(connected=False))
    it = ready_item(env, videos)
    env.req("POST", f"/queue/{it['id']}/approve", "owner", json={})
    r = env.req("POST", f"/queue/{it['id']}/publish", "owner", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_CONNECTED"
    assert env.store.get_short(it["id"])["state"] == "approved_by_owner"


def test_dry_run_does_not_change_state(env, videos):
    it = ready_item(env, videos)
    env.req("POST", f"/queue/{it['id']}/approve", "owner", json={})
    r = env.req("POST", f"/queue/{it['id']}/publish", "owner", json={"dry_run": True})
    assert r.status_code == 200 and r.json()["status"] == "DRY_RUN"
    assert env.yt.uploads == [] and env.store.get_short(it["id"])["state"] == "approved_by_owner"


def test_reject_is_final_and_metadata_edit_after_approval_needs_new_yes(env, videos):
    it = ready_item(env, videos)
    env.req("POST", f"/queue/{it['id']}/approve", "owner", json={})
    r = env.req("PATCH", f"/queue/{it['id']}/metadata", "editor", json={"title": "Changed"})
    assert r.status_code == 200 and r.json()["state"] == "ready"
    assert env.req("POST", f"/queue/{it['id']}/publish", "owner", json={}).status_code == 409
    assert env.req("POST", f"/queue/{it['id']}/reject", "owner", json={"note": "нет"}).status_code == 200
    assert env.req("POST", f"/queue/{it['id']}/approve", "owner", json={}).status_code == 409
    assert env.yt.uploads == []


def test_revoked_rights_block_approval(env, videos):
    it = ready_item(env, videos)
    env.req("POST", f"/sources/{it['source_id']}/rights", "owner", json={"approve": False, "confirm": "", "note": "отзываю"})
    r = env.req("POST", f"/queue/{it['id']}/approve", "owner", json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "RIGHTS_NOT_APPROVED"


def test_audit_chain_verifies_and_detects_tampering(env, videos):
    ready_item(env, videos)
    assert env.req("GET", "/audit/verify", "owner").json()["ok"] is True
    assert env.req("GET", "/audit/verify", "editor").status_code == 403
    db = sqlite3.connect(env.store.path)
    db.execute("UPDATE audit SET payload='{}' WHERE seq=1")
    db.commit()
    db.close()
    assert env.req("GET", "/audit/verify", "owner").json()["ok"] is False


def test_fake_engagement_actions_are_forbidden(env):
    for name in ("buy-views", "sub4sub", "auto-comment", "reupload-third-party"):
        r = env.req("POST", f"/actions/{name}", "owner")
        assert r.status_code == 403 and r.json()["detail"]["code"] == "FORBIDDEN_BY_POLICY"


def test_video_preview_needs_token(env, videos):
    it = ready_item(env, videos)
    assert env.c.get(f"/api/v1/queue/{it['id']}/video").status_code == 401
    r = env.c.get(f"/api/v1/queue/{it['id']}/video?t=tv")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and len(r.content) > 10000
    assert "file" not in env.req("GET", f"/queue/{it['id']}", "viewer").json()     # no local paths in the API
