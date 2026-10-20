"""Global STOP is visible in the phone app (owner 20.10: STOP stayed on for hours after a test and nobody saw it).

GET /m/api/stop reads <bcc data>/computer/STOP; «Продолжить» goes through Bossman's own POST /api/computer/resume,
the mobile process never deletes the file itself.
"""
from __future__ import annotations

import os
import time
from datetime import datetime


def _set_stop(env, minutes_ago: float):
    f = env.app.state.settings.stop_file
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("owner\n0\n", encoding="utf-8")
    t = time.time() - minutes_ago * 60
    os.utime(f, (t, t))
    return f, t


def test_stop_needs_login(env):
    assert env.c.get("/m/api/stop").status_code == 401
    assert env.c.post("/m/api/stop/resume").status_code == 401


def test_stop_inactive(authed):
    r = authed.c.get("/m/api/stop")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json() == {"active": False, "since": None, "age_min": None}


def test_stop_active_reports_mtime_and_age(authed):
    _, t = _set_stop(authed, 135)
    body = authed.c.get("/m/api/stop").json()
    assert body["active"] is True and body["age_min"] in (134, 135)
    assert abs(datetime.fromisoformat(body["since"]).timestamp() - t) < 1.0
    assert body["since"].endswith("+00:00")


def test_resume_needs_csrf_and_goes_through_bossman(authed):
    f, _ = _set_stop(authed, 5)
    authed.fake.resume_clears = False          # prove the mobile process itself does NOT delete the file
    assert authed.c.post("/m/api/stop/resume").status_code == 403
    assert f.exists()
    r = authed.post("/m/api/stop/resume")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "BM_STOP_STILL_ACTIVE"
    assert f.exists(), "mobile must never remove the STOP file on its own"
    resumes = [c for c in authed.fake.calls if c["path"] == "/api/computer/resume"]
    assert len(resumes) == 1 and resumes[0]["method"] == "POST"


def test_resume_clears_via_bossman(authed):
    f, _ = _set_stop(authed, 90)
    authed.fake.resume_clears = True
    r = authed.post("/m/api/stop/resume")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "active": False, "since": None, "age_min": None, "generation": 3}
    assert not f.exists()
    assert authed.c.get("/m/api/stop").json()["active"] is False
