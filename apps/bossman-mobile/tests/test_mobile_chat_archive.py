"""Long-press chat actions through the phone gateway (owner 22.10): encrypted archive, restore, delete forever.

Synthetic fake Bossman only (tests/conftest.py). The gateway is a proxy: session cookie + X-BM-CSRF on every
mutating call, id validation before anything reaches Bossman, upstream errors passed through, typed confirmation
for delete forever, and no chat text in anything it answers.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "bossmobile" / "ui"
MANIFEST = Path(__file__).resolve().parents[1] / "app.manifest.yaml"


def _calls(env, method=None, prefix="/api/chat/"):
    return [c for c in env.fake.calls if c["path"].startswith(prefix) and (method is None or c["method"] == method)
            and c["path"] != "/api/chat/options"]


def test_all_routes_need_a_session(env):
    for method, path in (("post", "/m/api/threads/T1/archive-encrypted"), ("get", "/m/api/archive"),
                         ("post", "/m/api/archive/T1/restore"), ("delete", "/m/api/archive/T1"),
                         ("post", "/m/api/threads/T1/delete-forever")):
        kw = {"json": {"confirm": "T1"}} if path.endswith("delete-forever") else {}
        assert getattr(env.c, method)(path, **kw).status_code in (401, 403), path
    assert not _calls(env)


def test_mutating_routes_need_csrf(authed):
    wrong = {"X-BM-CSRF": "wrong"}
    assert authed.c.post("/m/api/threads/T1/archive-encrypted", headers=wrong).status_code == 403
    assert authed.c.post("/m/api/archive/T1/restore", headers=wrong).status_code == 403
    assert authed.c.delete("/m/api/archive/T1", headers=wrong).status_code == 403
    assert authed.c.post("/m/api/threads/T1/delete-forever", headers=wrong, json={"confirm": "T1"}).status_code == 403
    assert authed.c.post("/m/api/threads/T1/archive-encrypted").status_code == 403, "no header at all"
    assert not [c for c in _calls(authed) if c["method"] != "GET"], "refused requests never reach Bossman"


def test_archive_encrypted_restore_list_delete_pass_through(authed):
    r = authed.post("/m/api/threads/T1/archive-encrypted")
    assert r.status_code == 200 and r.json()["encrypted"] is True
    r = authed.c.get("/m/api/archive")
    assert r.status_code == 200 and r.json()["items"][0] == {
        "id": "T1", "title": "Секретный чат", "archived_at": "2026-10-22T10:00:00", "size": 99}
    assert authed.post("/m/api/archive/T1/restore").json() == {"ok": True, "id": "T1", "restored": True}
    assert authed.c.delete("/m/api/archive/T1", headers={"X-BM-CSRF": authed.csrf}).json() == {"ok": True, "deleted": True}
    seen = [(c["method"], c["path"]) for c in _calls(authed)]
    assert seen == [("POST", "/api/chat/threads/T1/archive-encrypted"), ("GET", "/api/chat/archive"),
                    ("POST", "/api/chat/archive/T1/restore"), ("DELETE", "/api/chat/archive/T1")]


def test_upstream_errors_reach_the_phone_unchanged(authed):
    r = authed.post("/m/api/threads/T9/archive-encrypted")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "CHAT_ARCHIVE_KEY_UNAVAILABLE"
    r = authed.post("/m/api/archive/T8/restore")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CHAT_THREAD_EXISTS"


def test_bad_ids_never_reach_bossman(authed):
    for bad in ("..%2Fx", "a b", "x" * 65, "a.b"):
        assert authed.post(f"/m/api/threads/{bad}/archive-encrypted").status_code in (404, 422), bad
        assert authed.post(f"/m/api/archive/{bad}/restore").status_code in (404, 422), bad
        assert authed.c.delete(f"/m/api/archive/{bad}", headers={"X-BM-CSRF": authed.csrf}).status_code in (404, 405, 422), bad
        assert authed.post(f"/m/api/threads/{bad}/delete-forever", json={"confirm": "123456"}).status_code in (404, 422), bad
    assert not _calls(authed)


def test_delete_forever_requires_the_typed_short_id(authed):
    # T1 is shorter than 6: the whole id is the short id
    for wrong in ("", "t1", "T2", "ok", "да"):
        r = authed.post("/m/api/threads/T1/delete-forever", json={"confirm": wrong})
        assert r.status_code == 422, wrong
    assert authed.post("/m/api/threads/T1/delete-forever", json={}).status_code == 422
    assert not _calls(authed, "DELETE"), "nothing was deleted without the typed id"
    # the real id format: last 6 characters
    ok = authed.post("/m/api/threads/T1/delete-forever", json={"confirm": " T1 "})
    assert ok.status_code == 200 and ok.json()["purged"] is True
    sent = _calls(authed, "DELETE")[0]
    assert sent["path"] == "/api/chat/threads/T1"
    assert sent["query"] == {"purge": "1", "confirm": "T1"}
    assert sent["headers"]["x-bossman-surface"] == "mobile"


def test_short_id_is_the_last_six_characters(authed):
    tid = "20261022-101010-abc123"
    assert authed.post(f"/m/api/threads/{tid}/delete-forever", json={"confirm": "abc12"}).status_code == 422
    assert authed.post(f"/m/api/threads/{tid}/delete-forever", json={"confirm": "101010"}).status_code == 422
    assert not _calls(authed, "DELETE")
    authed.post(f"/m/api/threads/{tid}/delete-forever", json={"confirm": "abc123"})   # fake has no such thread: 404 passthrough
    sent = _calls(authed, "DELETE")[0]
    assert sent["path"] == f"/api/chat/threads/{tid}" and sent["query"]["confirm"] == tid


def test_no_chat_text_or_secrets_in_gateway_answers(authed):
    texts = [authed.post("/m/api/threads/T1/archive-encrypted").text, authed.c.get("/m/api/archive").text,
             authed.post("/m/api/threads/T9/archive-encrypted").text]
    for t in texts:
        assert "BOSSMAN-OWNER-TOKEN" not in t and "UPSTREAM-SESSION" not in t


def test_manifest_lists_the_new_calls_and_sw_bumped():
    text = MANIFEST.read_text(encoding="utf-8")
    for call in ("/m/api/threads/{id}/archive-encrypted", "/m/api/archive", "/m/api/archive/{id}/restore",
                 "/m/api/threads/{id}/delete-forever"):
        assert call in text
    sw = (UI / "sw.js").read_text(encoding="utf-8")
    assert int(re.search(r"bossman-shell-v(\d+)", sw).group(1)) >= 19
