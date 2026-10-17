"""Face-consent gate and 'never show the same sentence twice' captions rule (negative + positive controls)."""
from shortsfactory import media


def _ready(env, path, **src_extra):
    src = env.register(path, **src_extra)
    env.approve_rights(src["id"])
    j = env.wait(env.req("POST", "/jobs/process", "editor", json={"source_id": src["id"], "k": 1}).json()["id"])
    assert j["status"] == "completed", j.get("error")
    return src, env.store.shorts()[-1]


def test_people_in_frame_without_consent_blocks_approval_and_publication(env, videos):
    src, it = _ready(env, videos["land"], people_in_frame=True)
    q = env.req("GET", f"/queue/{it['id']}", "viewer").json()
    assert q["blocked_consent"] is True and q["blocked"] == "consent"
    r = env.req("POST", f"/queue/{it['id']}/approve", "owner", json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "CONSENT_REQUIRED"
    assert env.store.get_short(it["id"])["state"] == "ready" and env.yt.uploads == []
    # editor cannot clear the flag or fake the consent
    assert env.req("PATCH", f"/sources/{src['id']}", "editor", json={"changes": {"people_in_frame": False}}).status_code == 403
    assert env.req("PATCH", f"/sources/{src['id']}", "editor", json={"changes": {"consent_recorded": True}}).status_code == 403
    assert env.req("POST", f"/sources/{src['id']}/consent", "editor", json={"consent": True, "confirm": "ДА", "persons": ["A"], "note": "ok ok"}).status_code == 403
    bad = env.req("POST", f"/sources/{src['id']}/consent", "owner", json={"consent": True, "confirm": "ДА", "persons": [], "note": "без имён"})
    assert bad.status_code == 422
    # positive control: owner records consent -> approval and upload work
    ok = env.req("POST", f"/sources/{src['id']}/consent", "owner", json={"consent": True, "confirm": "ДА", "persons": ["Ведущий"], "note": "письменное согласие"})
    assert ok.status_code == 200 and ok.json()["source"]["consent_recorded"] is True
    assert env.req("POST", f"/queue/{it['id']}/approve", "owner", json={}).status_code == 200
    # consent revoked after approval -> publication refused
    env.req("POST", f"/sources/{src['id']}/consent", "owner", json={"consent": False, "note": "отозвано"})
    r = env.req("POST", f"/queue/{it['id']}/publish", "owner", json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "CONSENT_REQUIRED" and env.yt.uploads == []


def test_no_people_flag_means_not_blocked(env, videos):
    _, it = _ready(env, videos["land"])
    assert env.req("GET", f"/queue/{it['id']}", "viewer").json()["blocked_consent"] is False
    assert env.req("POST", f"/queue/{it['id']}/approve", "owner", json={}).status_code == 200


def test_detected_face_switches_people_flag_on(env, videos, monkeypatch):
    monkeypatch.setattr(media, "face_center_x", lambda *a, **k: {"x": 0.4, "method": "fake-detector", "faces": 3})
    src, it = _ready(env, videos["land"])
    s = env.store.get_source(src["id"])
    assert s["people_in_frame"] is True and s["people_detected"]["method"] == "fake-detector"
    assert env.req("POST", f"/queue/{it['id']}/approve", "owner", json={}).status_code == 403


def test_source_with_on_screen_text_gets_no_second_caption_layer(env, videos):
    _, with_caps = _ready(env, videos["land"])
    assert with_caps["captions"]["burned"] is True
    _, no_caps = _ready(env, videos["vert"], has_burned_captions=True)
    assert no_caps["captions"]["burned"] is False and "повтор" in no_caps["captions"]["reason"]
    assert not (env.app.state.data_dir / "shorts" / no_caps["id"] / "subs.ass").exists()
    assert no_caps["validation"]["ok"]


def test_rerender_supersedes_unapproved_item_with_current_settings(env, videos):
    src, it = _ready(env, videos["land"])
    assert it["captions"]["burned"] is True
    env.req("PATCH", f"/sources/{src['id']}", "editor", json={"changes": {"has_burned_captions": True}})
    j = env.wait(env.req("POST", f"/queue/{it['id']}/rerender", "editor").json()["id"])
    assert j["status"] == "completed", j.get("error")
    new = j["result"]
    assert new["captions"]["burned"] is False and new["replaces"] == it["id"] and new["segment"] == it["segment"]
    old = env.store.get_short(it["id"])
    assert old["state"] == "rejected" and old["superseded_by"] == new["id"]
    assert env.store.get_source(src["id"])["approved_by_owner"] is True      # caption flag does not void rights
