"""«Удалить навсегда»: /m/api/{photo|video}/jobs/{id}/delete forwards ONE DELETE to Bossman, needs session + CSRF."""
from __future__ import annotations

import json
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "bossmobile" / "ui"


def test_delete_routes_forward_one_job_and_need_csrf(authed):
    for kind in ("photo", "video"):
        r = authed.post(f"/m/api/{kind}/jobs/{'a' * 32}/delete")
        assert r.status_code == 200 and r.json() == {"ok": True, "deleted": True}
    calls = [c for c in authed.fake.calls if c["method"] == "DELETE"]
    assert len(calls) == 2 and all(c["path"] == f"/api/direct-gen/jobs/{'a' * 32}" for c in calls)
    assert authed.c.post(f"/m/api/photo/jobs/{'a' * 32}/delete", headers={"X-BM-CSRF": "wrong"}).status_code == 403
    assert authed.c.get(f"/m/api/photo/jobs/{'a' * 32}/delete").status_code in (404, 405)
    assert len([c for c in authed.fake.calls if c["method"] == "DELETE"]) == 2, "refused requests never reach Bossman"


def test_delete_needs_a_session(env):
    assert env.c.post(f"/m/api/photo/jobs/{'a' * 32}/delete").status_code in (401, 403)
    assert not [c for c in env.fake.calls if c["method"] == "DELETE"]


def test_delete_error_text_reaches_the_phone_and_bad_ids_are_refused(authed):
    r = authed.post(f"/m/api/video/jobs/{'e' * 32}/delete")
    assert r.status_code == 409 and "остановите" in json.dumps(r.json(), ensure_ascii=False)
    for bad in ("..%2Fx", "x", "A" * 32):
        assert authed.post(f"/m/api/photo/jobs/{bad}/delete").status_code == 404
    assert len([c for c in authed.fake.calls if c["method"] == "DELETE"]) == 1


def test_ui_has_delete_buttons_and_sw_cache_bumped():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert 'id="viewer-delete"' in html and 'id="vviewer-delete"' in html
    assert html.index('id="viewer-pult"') < html.index('id="viewer-delete"')
    assert html.index('id="vviewer-pult"') < html.index('id="vviewer-delete"')
    sw = (UI / "sw.js").read_text(encoding="utf-8")
    assert "bossman-shell-v9" in sw
