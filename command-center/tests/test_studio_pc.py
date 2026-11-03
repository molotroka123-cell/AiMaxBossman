"""pc.studio (harness 03.11): Roblox Studio driver — busy guard, pure script building, marker proof, policy tiers."""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.features import studio_pc as sp


def _studio(tmp_path, procs, owned=None):
    calls = []
    s = sp.StudioPC(state_file=tmp_path / "owned.json", ps=lambda script: calls.append(script) or "front=True",
                    lister=lambda: list(procs))
    if owned is not None:
        s._set_owned(owned)
    return s, calls


def test_busy_guard_refuses_input_on_a_studio_it_did_not_launch(tmp_path):
    """Another session's play-test must never get our keys: a foreign Studio pid blocks every acting action."""
    s, calls = _studio(tmp_path, [{"pid": 7764, "hwnd": 11, "title": "x.rbxlx - Roblox Studio"}])
    st = s.status()
    assert st["busy"] and "7764" in st["busy_reason"]
    for act in (s.play, s.stop, s.front, s.close):
        with pytest.raises(PermissionError):
            act()
    with pytest.raises(PermissionError):
        s.open(str(tmp_path / "a.rbxlx"))
    assert calls == [], "no input script may be sent while Studio is busy"


def test_owned_studio_gets_input(tmp_path):
    s, calls = _studio(tmp_path, [{"pid": 42, "hwnd": 99, "title": "a.rbxlx - Roblox Studio"}], owned=42)
    assert not s.status()["busy"]
    assert s.play()["ok"]
    assert "{F5}" in calls[-1] and "Front 99" in calls[-1]


def test_no_studio_means_nothing_to_drive(tmp_path):
    s, _ = _studio(tmp_path, [])
    with pytest.raises(RuntimeError):
        s.play()


def test_lua_is_single_quoted_not_interpolated():
    script = sp.build_script("run_lua", hwnd=1, lua="print('a') $env:SECRET `whoami`")
    assert "Set-Clipboard -Value 'print(''a'') $env:SECRET `whoami`'" in script


def test_marker_proof():
    m = sp.make_marker()
    lua = sp.marker_lua(m, 'print("x")')
    assert lua.startswith(f'print("{m}:begin")') and lua.endswith(f'print("{m}:end")')
    assert sp.output_has_marker(f"12:00 {m}:begin\n12:00 x\n12:00 {m}:end", m)
    assert not sp.output_has_marker(f"{m}:begin\nerror", m)


def test_place_validation(tmp_path):
    with pytest.raises(ValueError):
        sp.validate_place(str(tmp_path / "evil.exe"))
    with pytest.raises(FileNotFoundError):
        sp.validate_place(str(tmp_path / "missing.rbxlx"))
    p = tmp_path / "ok.rbxlx"
    p.write_text("<roblox/>", encoding="utf-8")
    assert sp.validate_place(str(p)) == p


def test_policy_tiers(tmp_path):
    from bcc.features import pc_agent

    from .test_pc_agent_master_p0_p5 import _DummySvc
    svc = _DummySvc(tmp_path)
    rt = svc._pc_agent_runtime
    tier = lambda a: pc_agent._classify_pc_step_with_autonomy(rt, svc, "pc.studio", {"action": a}).tier  # noqa: E731
    assert tier("status") == "T0" and tier("screenshot") == "T0"
    assert tier("play") == "T1" and tier("run_lua") == "T1"
    assert tier("format_disk") == "T3"
