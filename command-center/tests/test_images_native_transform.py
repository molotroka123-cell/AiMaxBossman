"""Native Image Studio edit path must change real pixels and persist a derived asset."""
from __future__ import annotations

import base64
import io

from PIL import Image


def png_bytes():
    image = Image.new("RGB", (3, 2))
    px = image.load()
    px[0, 0] = (255, 0, 0)
    px[1, 0] = (0, 255, 0)
    px[2, 0] = (0, 0, 255)
    px[0, 1] = (20, 40, 60)
    px[1, 1] = (80, 100, 120)
    px[2, 1] = (140, 160, 180)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


async def test_import_transform_reopen_and_verify_pixels(env):
    original = png_bytes()
    imported = await env.client.post("/api/images/assets/import", json={
        "filename": "owner.png",
        "data_base64": base64.b64encode(original).decode(),
        "title": "owner fixture",
    })
    assert imported.status_code == 200, imported.text
    src = imported.json()

    edited = await env.client.post(
        f"/api/images/assets/{src['id']}/transform",
        json={"operation": "grayscale"},
    )
    assert edited.status_code == 200, edited.text
    derived = edited.json()
    assert derived["id"] != src["id"]
    assert derived["model_alias"] == "image-editor"
    assert derived["meta"]["source_asset_id"] == src["id"]
    assert derived["meta"]["operation"] == "grayscale"

    payload = await env.client.get(f"/api/images/assets/{derived['id']}/file")
    assert payload.status_code == 200
    assert payload.content != original
    img = Image.open(io.BytesIO(payload.content)).convert("RGB")
    for r, g, b in img.getdata():
        assert r == g == b

    reopened = await env.client.get(f"/api/images/assets/{derived['id']}")
    assert reopened.status_code == 200
    assert reopened.json()["meta"]["sha256_16"] == derived["meta"]["sha256_16"]


async def test_rotate_and_resize_are_real_editor_operations(env):
    imported = await env.client.post("/api/images/assets/import", json={
        "filename": "owner.png",
        "data_base64": base64.b64encode(png_bytes()).decode(),
    })
    src = imported.json()
    rotated = await env.client.post(
        f"/api/images/assets/{src['id']}/transform", json={"operation": "rotate90"})
    assert rotated.status_code == 200
    assert (rotated.json()["width"], rotated.json()["height"]) == (2, 3)

    resized = await env.client.post(
        f"/api/images/assets/{src['id']}/transform",
        json={"operation": "resize", "width": 32, "height": 16},
    )
    assert resized.status_code == 200
    assert (resized.json()["width"], resized.json()["height"]) == (32, 16)


async def test_resize_requires_both_dimensions(env):
    imported = await env.client.post("/api/images/assets/import", json={
        "filename": "owner.png",
        "data_base64": base64.b64encode(png_bytes()).decode(),
    })
    response = await env.client.post(
        f"/api/images/assets/{imported.json()['id']}/transform",
        json={"operation": "resize", "width": 32},
    )
    assert response.status_code == 422
