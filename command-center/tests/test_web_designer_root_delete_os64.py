"""OS-64 release gate: destructive document-root deletion is never a generic edit."""
from __future__ import annotations


async def test_root_delete_requires_high_severity_ack_and_verified_recovery(env):
    created = (await env.client.post(
        "/api/web-designer/projects",
        json={"name": "OS64 root", "template": "blank"},
    )).json()
    pid = created["meta"]["id"]
    version = created["meta"]["version"]
    original = created["code"]

    denied = await env.client.post(
        f"/api/web-designer/projects/{pid}/edit",
        json={"op": "delete", "path": "html > body", "tag": "body",
              "base_version": version},
    )
    assert denied.status_code == 409
    assert "ENTIRE SITE/DOCUMENT CONTENT" in denied.json()["error"]["message"]
    unchanged = (await env.client.get(f"/api/web-designer/projects/{pid}")).json()
    assert unchanged["code"] == original
    assert unchanged["meta"]["version"] == version

    approved = await env.client.post(
        f"/api/web-designer/projects/{pid}/edit",
        json={"op": "delete", "path": "html > body", "tag": "body",
              "base_version": version, "destructive_root_ack": True},
    )
    assert approved.status_code == 200, approved.text
    after = (await env.client.get(f"/api/web-designer/projects/{pid}")).json()
    assert "<body" not in after["code"].lower()
    assert after["meta"]["version"] == version + 1

    # The exact same approval payload is stale after the first mutation and
    # cannot authorize a second destructive edit.
    replay = await env.client.post(
        f"/api/web-designer/projects/{pid}/edit",
        json={"op": "delete", "path": "html > body", "tag": "body",
              "base_version": version, "destructive_root_ack": True},
    )
    assert replay.status_code == 409

    restored = await env.client.post(
        f"/api/web-designer/projects/{pid}/versions/{version}/restore")
    assert restored.status_code == 200, restored.text
    final = (await env.client.get(f"/api/web-designer/projects/{pid}")).json()
    assert final["code"] == original


async def test_html_root_uses_same_destructive_gate(env):
    created = (await env.client.post(
        "/api/web-designer/projects",
        json={"name": "OS64 html", "template": "blank"},
    )).json()
    pid = created["meta"]["id"]
    version = created["meta"]["version"]
    denied = await env.client.post(
        f"/api/web-designer/projects/{pid}/edit",
        json={"op": "delete", "path": "html", "tag": "html",
              "base_version": version},
    )
    assert denied.status_code == 409
    assert "ENTIRE SITE/DOCUMENT CONTENT" in denied.json()["error"]["message"]
