from __future__ import annotations

import base64

import sqlalchemy as sa

from bcc.features.images import process_one
from bcc.v2.images_tables import image_assets as assets_t, image_jobs as jobs_t


async def test_images_feature_routes_exist_and_require_auth(env):
    unauth = await env.client.get("/api/images/assets", headers={"X-BCC-Token": "wrong"})
    assert unauth.status_code in (401, 403)

    ok = await env.client.get("/api/images/assets")
    assert ok.status_code == 200
    assert ok.json() == {"items": [], "total": 0}


async def test_mock_generation_job_persists_asset(env):
    create = await env.client.post("/api/images/jobs", json={
        "prompt": "cyberpunk Prague at night",
        "model_alias": "mock-image",
        "aspect_ratio": "16:9",
        "width": 1280,
        "height": 720,
        "steps": 20,
        "count": 2,
        "tags": ["concept"],
    })
    assert create.status_code == 200
    job = create.json()
    assert job["status"] == "queued"

    processed = await process_one(env.svc)
    assert processed == job["id"]

    detail = await env.client.get(f"/api/images/jobs/{job['id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"

    assets = await env.client.get("/api/images/assets")
    body = assets.json()
    assert body["total"] == 2
    assert all(x["model_alias"] == "mock-image" for x in body["items"])
    assert all(x["file_url"].startswith("/api/images/assets/") for x in body["items"])

    asset_id = body["items"][0]["id"]
    file_res = await env.client.get(f"/api/images/assets/{asset_id}/file")
    assert file_res.status_code == 200
    assert file_res.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in file_res.content


async def test_collection_favorite_and_filter(env):
    collection = await env.client.post("/api/images/collections", json={"name": "Marketing"})
    assert collection.status_code == 200
    cid = collection.json()["id"]

    job = (await env.client.post("/api/images/jobs", json={
        "prompt": "campaign hero image",
        "model_alias": "mock-image",
        "collection_id": cid,
        "count": 1,
    })).json()
    await process_one(env.svc)

    assets = (await env.client.get(f"/api/images/assets?collection_id={cid}")).json()
    assert assets["total"] == 1
    aid = assets["items"][0]["id"]

    patched = await env.client.patch(f"/api/images/assets/{aid}", json={
        "favorite": True,
        "tags": ["hero", "campaign"],
    })
    assert patched.status_code == 200
    assert patched.json()["favorite"] is True
    assert patched.json()["tags"] == ["hero", "campaign"]

    fav = (await env.client.get("/api/images/assets?favorite=true")).json()
    assert fav["total"] == 1


async def test_base64_import_is_protected_and_persistent(env):
    raw = b"\x89PNG\r\n\x1a\n" + b"fakepng"
    res = await env.client.post("/api/images/assets/import", json={
        "filename": "sample.png",
        "data_base64": base64.b64encode(raw).decode(),
        "title": "Imported sample",
        "tags": ["imported"],
    })
    assert res.status_code == 200
    asset = res.json()
    assert asset["model_alias"] == "import"
    assert asset["title"] == "Imported sample"

    file_res = await env.client.get(asset["file_url"])
    assert file_res.status_code == 200
    assert file_res.content == raw


async def test_real_unwired_model_fails_honestly(env):
    job = (await env.client.post("/api/images/jobs", json={
        "prompt": "real provider check",
        "model_alias": "some-real-image-model",
    })).json()

    await process_one(env.svc)
    detail = (await env.client.get(f"/api/images/jobs/{job['id']}")).json()
    assert detail["status"] == "failed"
    assert "ещё не подключён" in detail["error"]


async def test_image_models_always_include_mock(env):
    models = await env.client.get("/api/images/models")
    assert models.status_code == 200
    aliases = [x["alias"] for x in models.json()]
    assert "mock-image" in aliases
