"""pc.studio (harness 03.11 + P0 audit 05.11): Roblox Studio driver — busy guard, strict confirmation, marker proof, policy tiers."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from bcc.features import studio_pc as sp


def _ok_ps(calls: list[str], script: str) -> str:
    calls.append(script)
    for tok in sp.EXPECTED_TOKENS.values():
        if tok in script:
            return f"front=True\n{tok}"
    return "front=True"


def _studio(tmp_path, procs, owned=None, ps_fn=None):
    calls = []
    runner = ps_fn if ps_fn is not None else (lambda script: _ok_ps(calls, script))
    s = sp.StudioPC(state_file=tmp_path / "owned.json", ps=runner, lister=lambda: list(procs))
    if owned is not None:
        s._set_owned(owned)
    return s, calls


def _make_png(width: int, height: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    # Use non-trivial RGB pattern so compressed size > 100 bytes for normal dimensions
    raw = b"".join(b"\x00" + bytes(((x * 37 + y * 13 + c * 59) % 256 for x in range(width) for c in range(3)))
                   for y in range(height))
    idat = zlib.compress(raw)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


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


def test_busy_guard_refuses_headless_foreign_studio_process(tmp_path):
    """Live machine case 05.11: foreign pid 47928 with hwnd=0 still triggers BUSY GUARD and blocks open/play."""
    s, calls = _studio(tmp_path, [{"pid": 47928, "hwnd": 0, "title": ""}])
    st = s.status()
    assert st["busy"] and "47928" in st["busy_reason"]
    with pytest.raises(PermissionError):
        s.play()
    assert calls == []


def test_owned_studio_gets_input(tmp_path):
    s, calls = _studio(tmp_path, [{"pid": 42, "hwnd": 99, "title": "a.rbxlx - Roblox Studio"}], owned=42)
    assert not s.status()["busy"]
    res = s.play()
    assert res["ok"] and res["status"] == "PASS"
    assert "{F5}" in calls[-1] and "Front 99" in calls[-1]


def test_p0_empty_output_returns_unknown_not_ok(tmp_path):
    """P0 bug reproduction & fix (05.11): previously `'front=False' not in out` returned ok=True on empty stdout."""
    s, _ = _studio(tmp_path, [{"pid": 42, "hwnd": 99, "title": "a.rbxlx - Roblox Studio"}], owned=42,
                   ps_fn=lambda _script: "")
    for fn in (s.front, s.play, s.stop):
        res = fn()
        assert res["ok"] is False and res["status"] == "UNKNOWN"
        assert "empty" in res.get("reason", "")


def test_p0_partial_execution_without_action_token_fails(tmp_path):
    """If Front succeeded ('front=True') but PowerShell stopped before sending F5, play() must NOT report ok=True."""
    s, _ = _studio(tmp_path, [{"pid": 42, "hwnd": 99, "title": "a.rbxlx - Roblox Studio"}], owned=42,
                   ps_fn=lambda _script: "front=True")
    res = s.play()
    assert res["ok"] is False and res["status"] == "UNKNOWN"
    assert "missing confirmation token" in res.get("reason", "")


def test_p0_powershell_error_and_focus_failure_return_fail(tmp_path):
    s_err, _ = _studio(tmp_path, [{"pid": 42, "hwnd": 99, "title": "a.rbxlx - Roblox Studio"}], owned=42,
                       ps_fn=lambda _script: "PS_ERROR: SendKeys failed")
    r1 = s_err.play()
    assert r1["ok"] is False and r1["status"] == "FAIL" and "PS_ERROR" in r1["reason"]

    s_fg, _ = _studio(tmp_path, [{"pid": 42, "hwnd": 99, "title": "a.rbxlx - Roblox Studio"}], owned=42,
                      ps_fn=lambda _script: "front=False")
    r2 = s_fg.front()
    assert r2["ok"] is False and r2["status"] == "FAIL" and "front=False" in r2["reason"]


def test_p0_window_switch_mid_operation_raises_and_records_fail(tmp_path):
    """If the user switches to Telegram mid-run_lua, AssertFg aborts with FOREGROUND_GUARD and records ok=False."""
    s, _ = _studio(
        tmp_path,
        [{"pid": 42, "hwnd": 99, "title": "a.rbxlx - Roblox Studio"}],
        owned=42,
        ps_fn=lambda _script: "front=True\nFOREGROUND_GUARD: window title 'Telegram' is a chat app in front",
    )
    with pytest.raises(PermissionError, match="FOREGROUND_GUARD"):
        s.run_lua("print(1)", out_file=str(tmp_path / "out.txt"))
    assert s.receipts[-1]["ok"] is False and s.receipts[-1]["status"] == "FAIL"


def test_p0_partial_run_lua_fails_when_copy_output_unconfirmed(tmp_path):
    """Even if an old out_file exists on disk, run_lua must fail if copy_output returns empty stdout."""
    out_file = tmp_path / "out.txt"
    step = {"n": 0}

    def ps_fn(script: str) -> str:
        step["n"] += 1
        if step["n"] == 1:
            return "front=True\nSTUDIO_ACTION_OK:run_lua"
        return ""  # copy_output failed silently

    s, _ = _studio(tmp_path, [{"pid": 42, "hwnd": 99, "title": "a.rbxlx - Roblox Studio"}], owned=42, ps_fn=ps_fn)
    res = s.run_lua("print(1)", out_file=str(out_file))
    assert res["ok"] is False and res["status"] == "UNKNOWN"


def test_p0_screenshot_rejects_1x1_stub_and_accepts_real_dimensions(tmp_path):
    stub_path = tmp_path / "stub_1x1.png"
    stub_path.write_bytes(_make_png(1, 1))
    s_stub, _ = _studio(
        tmp_path,
        [],
        ps_fn=lambda _script: "saved\nSTUDIO_ACTION_OK:screenshot",
    )
    r_stub = s_stub.screenshot(str(stub_path))
    assert r_stub["ok"] is False and r_stub["status"] == "FAIL"
    assert r_stub["image"]["is_stub_1x1"] is True and "1x1 stub" in r_stub["reason"]

    real_path = tmp_path / "real_64x64.png"
    real_path.write_bytes(_make_png(64, 64))
    r_real = s_stub.screenshot(str(real_path))
    assert r_real["ok"] is True and r_real["status"] == "PASS"
    assert r_real["image"]["width"] == 64 and r_real["image"]["height"] == 64


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


def test_front_does_not_minimize_previous_foreground_window():
    """Audit 05.11: Front($h) must never call ShowWindow($fg, 6) (SW_MINIMIZE) on the owner's active window."""
    assert "ShowWindow($fg, 6)" not in sp._PS_INPUT
    assert "ShowWindow($t, 9)" in sp._PS_INPUT and "SetForegroundWindow($t)" in sp._PS_INPUT


def test_studio_tool_registered_in_registry():
    import asyncio
    from bcc.tools import REGISTRY
    asyncio.run(sp.setup(None))
    spec = REGISTRY.get("pc.studio")
    assert spec is not None and spec.permission == "computer.control" and spec.default_effect == "ask"
