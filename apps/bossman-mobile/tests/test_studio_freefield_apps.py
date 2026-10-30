"""Tests for Freefield-style one-folder ``app.json`` Studio Apps in Bossman Mobile."""
from __future__ import annotations

import json

from bossmobile.studio_apps_loader import compose_scene_prompt, enrich_apps, load_raw_apps


def test_load_raw_apps_every_folder_has_valid_app_json():
    apps = load_raw_apps()
    assert len(apps) >= 5
    ids = [a["id"] for a in apps]
    assert "cinema-studio-video" in ids
    assert "product-spin-360" in ids
    assert "hand-reveal-macro" in ids
    assert "cinema-film-still" in ids
    assert "qwen-multi-ref-edit" in ids
    for app in apps:
        assert app["open_weights"] is True
        assert app["folder"] == f"studio_apps/{app['id']}/app.json"
        ex = app["lab_example"]
        assert len(ex["scene"]) > 20
        assert ex["peak_vram_gb"] > 0
        assert ex["run_time_s"] > 0


def test_enrich_apps_honest_estimate_vs_measured():
    raw = load_raw_apps()
    enriched_est = enrich_apps(raw, photo_catalog=[], video_catalog=[], history={})
    for a in enriched_est:
        assert a["lab_example"]["measurement_kind"] == "estimate"
        assert "оценка" in a["lab_example"]["measurement_label"]

    enriched_meas = enrich_apps(
        raw,
        photo_catalog=[{"id": "z-image-turbo", "label": "Z-Image Turbo", "available": True,
                        "timing": {"kind": "measured", "label": "по замеру 5 генераций"}}],
        video_catalog=[{"id": "wan2.2-ti2v-5b", "label": "Wan 2.2", "available": True, "ready": True,
                        "timing": {"kind": "measured", "label": "по замеру 4 генераций"}}],
        history={},
    )
    cinema = next(a for a in enriched_meas if a["id"] == "cinema-studio-video")
    assert cinema["lab_example"]["measurement_kind"] == "measured"
    assert cinema["lab_example"]["measurement_label"] == "по замеру 4 генераций"


def test_compose_scene_prompt_adds_camera_move_and_tempo():
    apps = load_raw_apps()
    cinema = next(a for a in apps if a["id"] == "cinema-studio-video")
    out = compose_scene_prompt(
        "A detective in a trench coat under a streetlamp",
        camera_move="dolly_in",
        tempo="single_shot",
        app_spec=cinema,
    )
    assert "dolly-in camera movement" in out
    assert "single-take shot" in out


def test_api_studio_apps_and_video_job_with_camera_move(authed):
    r = authed.c.get("/m/api/studio/apps")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["layout"] == "one-folder-app-json"
    assert body["open_weights_only"] is True
    assert body["total"] >= 5

    r_vid = authed.c.get("/m/api/studio/apps?kind=video")
    assert r_vid.status_code == 200
    assert all(a["kind"] == "video" for a in r_vid.json()["apps"])

    r_one = authed.c.get("/m/api/studio/apps/cinema-studio-video")
    assert r_one.status_code == 200
    one = r_one.json()
    assert one["headline"] == "THE SAME FILM LOOK, NOW MOVING"
    assert len(one["camera_moves"]) >= 4
    assert len(one["tempos"]) >= 3

    r_404 = authed.c.get("/m/api/studio/apps/unknown-app")
    assert r_404.status_code == 404

    # Submit a video job using Cinema Studio Video's camera_move & tempo
    r_job = authed.post(
        "/m/api/video/jobs",
        data={
            "model": "wan2.2-ti2v-5b",
            "prompt": one["lab_example"]["scene"],
            "negative": one["lab_example"]["negative"],
            "duration": "2",
            "resolution": "480x320",
            "studio_app": "cinema-studio-video",
            "camera_move": "dolly_in",
            "tempo": "single_shot",
            "seed": "20261030",
        },
    )
    assert r_job.status_code == 202, r_job.text
    sent_calls = [c for c in authed.fake.calls if c["path"] == "/api/direct-gen/jobs" and c["method"] == "POST"]
    assert sent_calls, "Expected POST /api/direct-gen/jobs to be sent to Bossman"
    sent_json = json.loads(sent_calls[-1]["body"])
    assert "dolly-in camera movement" in sent_json["prompt"]
    assert "single-take shot" in sent_json["prompt"]
