"""Тесты Надстройки Bossman 2.0 (28.10.2026):
- компактность pc_agent_policy.py (<= 300 строк),
- усиленный детектор обфускации PowerShell (& $var, InvokeScript, [scriptblock]::Create, -f),
- скрининг путей Windows через canonicalize_windows_path_safe(..., reject_dangerous=True),
- реальные ноды ComfyUI_IPAdapter_plus в character_lock (IPAdapterUnifiedLoaderFaceID + IPAdapterFaceID),
- резолв OBSIDIAN_VAULT из settings_kv['memory.vault'] в plugins.py,
- эндпоинты /api/v20/status, /api/v20/memory/audit, /api/v20/memory/sync, /api/v20/security/self-test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.art_v2.photo_engine import DynamicWorkflowCompiler
from bcc.features import plugins as P
from bcc.features.bossman_20 import (
    CANONICAL_BOSSMAN_20_NOTES,
    http_security_self_test,
    inspect_obsidian_vault,
)
from bcc.features.pc_agent_policy_guards import (
    canonicalize_windows_path_safe,
    validate_powershell_ast_or_tokens,
)
from bcc.security.win_paths import PathRefused


def test_pc_agent_policy_under_300_lines():
    policy_file = Path(__file__).resolve().parents[1] / "bcc" / "features" / "pc_agent_policy.py"
    lines = policy_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 300, f"pc_agent_policy.py has {len(lines)} lines (> 300)"


@pytest.mark.parametrize(
    "cmd",
    [
        "$c='Remove-Item'; & $c secret.txt",
        ". $runner arg1",
        "& (Get-Command *emove-Item) a.txt",
        "& { Remove-Item a.txt }",
        "$ExecutionContext.InvokeCommand.InvokeScript('rm a')",
        "[scriptblock]::Create('rm a').Invoke()",
        "(Get-Item a.txt).Delete()",
        "(\"{0}{1}\" -f 'Remove-','Item') a.txt",
    ],
)
def test_powershell_token_gate_blocks_indirection_and_dynamic_invoke(cmd: str):
    ok, reason = validate_powershell_ast_or_tokens(cmd)
    assert ok is False, f"Expected {cmd!r} to be blocked, got ok=True"
    assert reason != "ok"


@pytest.mark.parametrize(
    "bad_path",
    [
        r"C:\temp\file.txt:secret_stream",
        r"C:\temp\NUL",
        r"C:\temp\audit.jsonl.",
    ],
)
def test_canonicalize_windows_path_safe_reject_dangerous_mode(bad_path: str):
    with pytest.raises(PathRefused):
        canonicalize_windows_path_safe(bad_path, reject_dangerous=True)


def test_photo_engine_character_lock_uses_real_ipadapter_plus_nodes():
    wf = DynamicWorkflowCompiler().compile_character_lock("ref.png", "portrait in studio")
    class_types = {n["class_type"] for n in wf["nodes"].values()}
    assert "IPAdapterFaceIDPlusV2" not in class_types
    assert "IPAdapterUnifiedLoaderFaceID" in class_types
    assert "IPAdapterFaceID" in class_types


async def test_obsidian_plugin_falls_back_to_memory_vault_setting(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    vault_dir = tmp_path / "obs_vault"
    vault_dir.mkdir()
    note = vault_dir / "BOSSMAN Memory" / "hello.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Привет из Bossman 2.0", encoding="utf-8")

    async def fake_load_config(_svc):
        return {"root": str(vault_dir), "write_folder": "BOSSMAN Memory"}

    monkeypatch.setattr("bcc.features.tools_memory.load_config", fake_load_config)
    fake_svc = type("FakeSvc", (), {"db": object()})()

    resolved = await P.resolve_cred("OBSIDIAN_VAULT", fake_svc)
    assert resolved == str(vault_dir)

    ctx = type("C", (), {"svc": fake_svc, "task": {}, "run_id": 1, "agent": {}, "workspace": "", "call_id": "c1", "step": 0})()
    read_res = await P._h_obsidian_read({"path": "BOSSMAN Memory/hello.md"}, ctx)
    assert not read_res.error
    assert "Привет из Bossman 2.0" in read_res.content


async def test_bossman_20_security_self_test_all_5_pass():
    res = await http_security_self_test()
    assert res["ok"] is True
    assert res["passed"] == 5
    assert res["total"] == 5


def test_inspect_obsidian_vault_detects_junk_and_canonical_notes(tmp_path: Path):
    wf = tmp_path / "BOSSMAN Memory"
    wf.mkdir(parents=True)
    (wf / "2026-09-21-1822-memory.md").write_text("# memory\n\nmemory\n", encoding="utf-8")
    r1 = inspect_obsidian_vault(tmp_path)
    assert r1["synthetic_junk_count"] == 1
    assert r1["freshness_status"] == "NEEDS_SYNC"

    (wf / "2026-09-21-1822-memory.md").unlink()
    for fname, content in CANONICAL_BOSSMAN_20_NOTES.items():
        (wf / fname).write_text(content, encoding="utf-8")
    r2 = inspect_obsidian_vault(tmp_path)
    assert r2["synthetic_junk_count"] == 0
    assert r2["canonical_v20_count"] == len(CANONICAL_BOSSMAN_20_NOTES)
    assert r2["freshness_status"] == "UP_TO_DATE"
