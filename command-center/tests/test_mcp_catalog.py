"""Каталог MCP: топ-10 ставится через ту же таблицу и тот же allowlist, что и ручной ввод."""
from __future__ import annotations

import shutil

import pytest

from bcc.features.mcp_catalog import BY_ID, CATALOG
from bcc.v2.mcp_hub import command_policy_refusal

needs_runners = pytest.mark.skipif(
    not (shutil.which("npx") and shutil.which("uvx")),
    reason="npx и uvx нужны для проверки allowlist по реальным бинарникам")


def test_twelve_unique_pinned_entries():
    assert len(CATALOG) == 12 and len(BY_ID) == 12
    for e in CATALOG:
        assert ("==" in e.package) if e.runner == "uvx" else (e.package.rsplit("@", 1)[-1][:1].isdigit()), e.id
        assert e.risk in {"read", "network", "write"}


def test_argv_contains_path_only_for_param_servers():
    assert BY_ID["time"].argv() == ["uvx", "mcp-server-time==2026.10.10"]
    assert BY_ID["git"].argv("D:\\repo")[-2:] == ["--repository", "D:\\repo"]
    assert BY_ID["filesystem"].argv("D:\\work")[-1] == "D:\\work"


@needs_runners
def test_every_entry_passes_command_allowlist():
    for e in CATALOG:
        path = "D:\\work" if e.param else ""
        assert command_policy_refusal(e.argv(path)) == "", e.id


async def test_list_and_install_roundtrip(env):
    listed = (await env.client.get("/api/mcp/catalog")).json()
    assert [s["id"] for s in listed["servers"]] == [e.id for e in CATALOG]
    assert not any(s["installed"] for s in listed["servers"])

    first = await env.client.post("/api/mcp/catalog/time/install")
    if first.status_code == 403:
        pytest.skip("uvx отсутствует в allowlist этой машины")
    assert first.status_code == 200, first.text
    assert first.json()["name"] == "catalog-time"

    again = await env.client.post("/api/mcp/catalog/time/install")
    assert again.status_code == 409

    after = (await env.client.get("/api/mcp/catalog")).json()["servers"]
    assert {s["id"]: s["installed"] for s in after}["time"] is True
    servers = (await env.client.get("/api/mcp/servers")).json()
    assert servers[0]["command"] == ["uvx", "mcp-server-time==2026.10.10"]


async def test_path_servers_refuse_missing_and_too_wide_paths(env, tmp_path):
    assert (await env.client.post("/api/mcp/catalog/filesystem/install")).status_code == 422
    root = tmp_path.anchor
    wide = await env.client.post("/api/mcp/catalog/filesystem/install", json={"path": root})
    assert wide.status_code == 422
    relative = await env.client.post("/api/mcp/catalog/filesystem/install", json={"path": "docs"})
    assert relative.status_code == 422
    assert (await env.client.post("/api/mcp/catalog/nope/install")).status_code == 404
    assert (await env.client.get("/api/mcp/servers")).json() == []


async def test_git_requires_a_repository_root(env, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    bad = await env.client.post("/api/mcp/catalog/git/install", json={"path": str(plain)})
    assert bad.status_code == 422 and ".git" in bad.text
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    ok = await env.client.post("/api/mcp/catalog/git/install", json={"path": str(repo)})
    if ok.status_code == 403:
        pytest.skip("uvx отсутствует в allowlist этой машины")
    assert ok.status_code == 200, ok.text
