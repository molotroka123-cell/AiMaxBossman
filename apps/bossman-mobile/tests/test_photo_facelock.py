"""Mobile proxy: @-references + FaceLock reach Bossman exactly as sent, and are refused in Russian when they cannot work."""
import base64
import json

import pytest

import conftest

IMG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
EDIT = {"id": "qwen-image-2.1-uc-edit", "label": "Qwen Edit", "kind": "photo", "modes": ["T2I", "I2I"], "available": True,
        "reason": "", "requires_image": False, "accepts_image": True, "max_refs": 3, "default_steps": 20, "max_steps": 50,
        "default_resolution": "512x512", "min_side": 256, "max_side": 1536, "side_multiple": 16}
T2I = {**EDIT, "id": "z-image-turbo", "label": "Z", "modes": ["T2I"], "accepts_image": False, "max_refs": 0}


@pytest.fixture
def edit(monkeypatch):
    monkeypatch.setattr(conftest, "DG_MODELS", {"models": [EDIT, T2I]})


def sent(authed):
    return json.loads([c for c in authed.fake.calls if c["path"] == "/api/direct-gen/jobs" and c["method"] == "POST"][-1]["body"])


def test_two_references_names_and_facelock_are_passed_through(authed, edit):
    r = authed.post("/m/api/photo/jobs", json={"model": EDIT["id"], "prompt": "лицо с @лицо, одежда с @2", "images_b64": [IMG, IMG],
                                               "ref_names": ["лицо", ""], "facelock": True})
    assert r.status_code == 202, r.text
    body = sent(authed)
    assert body["facelock"] is True and body["images_b64"] == [IMG, IMG] and body["ref_names"] == ["лицо", ""]
    assert "image_b64" not in body


def test_facelock_off_by_default_is_not_sent(authed, edit):
    r = authed.post("/m/api/photo/jobs", json={"model": EDIT["id"], "prompt": "портрет @1", "image_b64": IMG})
    assert r.status_code == 202
    assert "facelock" not in sent(authed) and sent(authed)["image_b64"] == IMG


def test_facelock_without_reference_or_on_text_model_is_refused(authed, edit):
    r = authed.post("/m/api/photo/jobs", json={"model": EDIT["id"], "prompt": "пейзаж", "facelock": True})
    assert r.status_code == 422 and "FaceLock" in json.dumps(r.json(), ensure_ascii=False)
    r = authed.post("/m/api/photo/jobs", json={"model": T2I["id"], "prompt": "пейзаж", "facelock": True})
    assert r.status_code == 422


def test_unknown_reference_tag_is_refused_before_anything_leaves(authed, edit):
    r = authed.post("/m/api/photo/jobs", json={"model": EDIT["id"], "prompt": "лицо с @3", "images_b64": [IMG, IMG]})
    assert r.status_code == 422
    assert not [c for c in authed.fake.calls if c["path"] == "/api/direct-gen/jobs" and c["method"] == "POST"]
    r = authed.post("/m/api/photo/jobs", json={"model": EDIT["id"], "prompt": "x", "images_b64": [IMG] * 4})
    assert r.status_code == 422


def test_job_view_carries_facelock_status_and_unlocked_flag(authed, edit, monkeypatch):
    job = {**conftest.JOB, "facelock": {"requested": True, "status": "applied", "reason": ""},
           "result_unlocked": {"file": "image_unlocked.png", "bytes": 3, "mime": "image/png"}}
    monkeypatch.setattr(conftest, "JOB", job)
    got = authed.c.get(f"/m/api/photo/jobs/{'a' * 32}").json()
    assert got["facelock"]["status"] == "applied" and got["result_unlocked"]["file"] == "image_unlocked.png"
    assert "provenance" not in got
    assert authed.c.get(f"/m/api/photo/jobs/{'a' * 32}/file?variant=bogus").status_code == 422
