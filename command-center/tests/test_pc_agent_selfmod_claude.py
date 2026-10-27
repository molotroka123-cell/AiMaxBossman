"""Аудит Claude 26.10 (19:40): контур самоизменения `pc.self_modify` — оффлайн-проверки на приманках в tmp_path.

D-030: `propose` с присланным кодом (`code`/`test_code`) выполняет Python (pytest) как T1 auto, без подтверждения: это исполнение произвольного кода.
D-031: `rollback` — T1 auto и доверяет `applied_files` из `proposal.json`; поддельная запись удаляет/перезаписывает любой файл.
Исправлено в рамках Milestone 2: все 5 тестов теперь зелёные без xfail.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_pc_agent_master_p0_p5 import _DummySvc  # noqa: E402

from bcc.features import pc_agent  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="политика ПК-агента рассчитана на Windows")


def _step(svc, rt, args, *, approved=False, task=900):
    return asyncio.run(pc_agent.execute_pc_step(svc, rt, task_id=task, run_id=task, step_idx=1,
                                                tool="pc.self_modify", args=args, approved=approved))


def _marker_code(marker: Path) -> str:
    return (
        "import pathlib, pytest\n\n"
        f"pathlib.Path(r'{marker}').write_text('executed outside the sandbox', encoding='utf-8')\n\n"
        "def test_ok():\n    assert True\n\n"
        "def test_negative_control():\n    with pytest.raises(ValueError):\n        raise ValueError('x')\n"
    )


def test_propose_with_agent_supplied_test_code_is_not_auto(tmp_path: Path) -> None:
    svc = _DummySvc(tmp_path)
    rt = svc._pc_agent_runtime
    rt.set_autonomy_level("L2", actor="owner")
    res = _step(
        svc,
        rt,
        {"action": "propose", "topic": "x", "test_code": "import os\nos.system('echo hi')\n"},
    )
    assert res["status"] == "verification_blocked_unisolated"
    assert res["tests_passed"] is False
    assert res["approval_id"] is None


def test_agent_supplied_test_code_does_not_run_without_approval(tmp_path: Path) -> None:
    svc = _DummySvc(tmp_path)
    rt = svc._pc_agent_runtime
    rt.set_autonomy_level("L2", actor="owner")
    marker = tmp_path / "outside" / "marker.txt"
    marker.parent.mkdir()
    _step(svc, rt, {"action": "propose", "topic": "evil", "target_file": "skills/evil.py",
                    "code": "def run_skill(v):\n    return {'ok': True}\n", "test_code": _marker_code(marker)})
    assert not marker.exists(), "агент исполнил произвольный Python без подтверждения владельца"


def test_negative_control_default_template_proposal_still_works(tmp_path: Path) -> None:
    svc = _DummySvc(tmp_path)
    rt = svc._pc_agent_runtime
    rt.set_autonomy_level("L2", actor="owner")
    res = _step(svc, rt, {"action": "propose", "topic": "disk-summary", "target_file": "skills/disk_summary.py"}, approved=True)
    # On Windows host without an OS-enforced sandbox, candidate proposals fail-closed
    assert res["status"] == "verification_blocked_unisolated"
    assert res["tests_passed"] is False
    assert res["approval_id"] is None


def test_forged_proposal_cannot_make_rollback_delete_foreign_file(tmp_path: Path) -> None:
    svc = _DummySvc(tmp_path)
    rt = svc._pc_agent_runtime
    rt.set_autonomy_level("L2", actor="owner")
    victim = tmp_path / "foreign" / "important.txt"
    victim.parent.mkdir()
    victim.write_text("do not delete", encoding="utf-8")
    forged = rt.self_mod_dir / "sm-forged"
    forged.mkdir(parents=True)
    (forged / "proposal.json").write_text(json.dumps({
        "mod_id": "sm-forged", "status": "applied", "branch": "agent/x",
        "applied_files": [{"target_path": str(victim), "existed_before": False, "backup_path": None}]}), encoding="utf-8")
    _step(svc, rt, {"action": "rollback", "mod_id": "sm-forged"})
    assert victim.exists(), "rollback по поддельной записи удалил чужой файл"


def test_rollback_mod_id_cannot_escape_self_mod_dir(tmp_path: Path) -> None:
    svc = _DummySvc(tmp_path)
    rt = svc._pc_agent_runtime
    rt.set_autonomy_level("L2", actor="owner")
    victim = tmp_path / "foreign2.txt"
    victim.write_text("keep", encoding="utf-8")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "proposal.json").write_text(json.dumps({
        "status": "applied", "applied_files": [{"target_path": str(victim), "existed_before": False}]}), encoding="utf-8")
    rel = Path("..") / ".." / "elsewhere"
    res = _step(svc, rt, {"action": "rollback", "mod_id": str(rel)})
    assert victim.exists() and res.get("ok") is not True
