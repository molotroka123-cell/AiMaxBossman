"""Audit 2026-09-28: Windows Smart App Control blocks OpenTimelineIO's native module.

The import then fails with "DLL load failed ... Application Control policy". Every OTIO
surface must say so plainly — never a raw ImportError, never "not installed".
"""
import sys
from types import SimpleNamespace
import pytest

SAC = "DLL load failed while importing _opentime: An Application Control policy has blocked this file."


class Blocked:
    def find_spec(self, name, path=None, target=None):
        if name == "opentimelineio" or name.startswith("opentimelineio."):
            raise ImportError(SAC)
        return None


@pytest.fixture
def sac_blocked(monkeypatch):
    for name in [n for n in sys.modules if n == "opentimelineio" or n.startswith("opentimelineio.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [Blocked(), *sys.meta_path])


def test_reason_names_the_windows_block_and_the_missing_package_differently():
    from bcc.video_studio.interchange import unavailable_reason
    assert "Application Control" in unavailable_reason(ImportError(SAC))
    assert "not installed" in unavailable_reason(ModuleNotFoundError("No module named 'opentimelineio'"))


async def _project(env):
    r = await env.client.post("/api/video-studio/projects", json={"operation_id": "otio-blocked"})
    assert r.status_code == 200, r.text
    return r.json()["project_id"]


async def test_agent_tool_turns_the_block_into_a_clear_error(env, sac_blocked):
    from bcc.video_studio.tools import _handler
    pid = await _project(env)
    ctx = SimpleNamespace(svc=env.svc, task={"id": 1, "meta": {"video_project_id": pid}})
    with pytest.raises(ValueError, match="Application Control"):
        await _handler("project.interchange", {"project_id": pid, "format": "otio"}, ctx)


async def test_routes_and_capabilities_report_the_block(env, sac_blocked):
    pid = await _project(env)
    r = await env.client.get(f"/api/video-studio/projects/{pid}/otio")
    assert r.status_code == 409 and "Application Control" in r.text
    caps = (await env.client.get("/api/video-studio/capabilities")).json()
    assert caps["interchange"]["available"] is False
    assert "Application Control" in caps["interchange"]["reason"]
