"""Stage 9 — restart/recovery: песочница (recover), project state (.bak),
video job checkpoint — без дублей side-эффектов и без утечек аренд."""
import asyncio
import json
import os
from pathlib import Path

import pytest

from bossman.resource_brain import ResourceBrain, ResourceSnapshot


def _mgr(tmp_path):
    from bossman.sandbox import ResourceLeaseAdapter, SandboxManager
    from bossman.sandbox.runtime import FakeRuntime
    from bossman.sandbox.runtimes import SafeRuntime
    brain = ResourceBrain(max_ram_pressure=0.95, disk_reserve=100)
    rt = SafeRuntime(workspace_root=tmp_path) if os.name == "posix" else FakeRuntime()
    m = SandboxManager(rt, enabled=True, workspace_root=tmp_path,
                       resources=ResourceLeaseAdapter(brain=brain))
    snap = ResourceSnapshot(1_000_000_000, 800_000_000, 100_000_000_000, 80_000_000_000)
    return m, snap


def test_stage9_sandbox_recover_idempotent_no_duplicates(tmp_path):
    """recover() после «падения»: сессия честно сносится (resume нет — и не
    обещается), аренда отпущена; повторный recover идемпотентен (пусто)."""
    from bossman.sandbox import SandboxSpec, ResourceRequest, SandboxState
    m, snap = _mgr(tmp_path)
    s = asyncio.run(
        m.create(SandboxSpec(task="t", resources=ResourceRequest(wall_time_seconds=10),
                             labels={"argv": ["/bin/echo", "x"], "fake_scenario": "ok"}),
                 snap=snap))
    held_before = m.resources.brain.held()[0] if hasattr(m.resources, "brain") else 0
    assert held_before > 0                    # аренда выдана

    first = asyncio.run(m.recover())
    assert first == [s.id]
    assert s.state == SandboxState.DESTROYED  # честный исход, не «продолжаем»
    held_after = m.resources.brain.held()[0] if hasattr(m.resources, "brain") else 0
    assert held_after == 0                    # утечки аренды нет

    second = asyncio.run(m.recover())
    assert second == []                       # идемпотентно, дублей нет


def test_stage9_state_json_bak_recovery(tmp_path, monkeypatch):
    """Битый state.json → восстановление из .bak (атомарная запись Stage 8.1)."""
    import bossman.projects.plan as plan_mod
    monkeypatch.setattr(plan_mod.settings, "projects_dir", tmp_path)
    slug = "recovery-slug"
    st = plan_mod.State(slug)
    st.data["task_index"] = 3
    st.data["status"] = "running"
    st.save()
    good = st.path.read_text(encoding="utf-8")
    st.path.with_suffix(".json.bak").write_text(good, encoding="utf-8")
    st.path.write_text("{broken", encoding="utf-8")
    st2 = plan_mod.State(slug)               # коррапт → падение на .bak
    assert st2.data.get("task_index") == 3


def test_stage9_video_job_checkpoint_resume_contract():
    """Video job возобновляем: INTERRUPTED-реконсиляция и per-scene checkpoint
    существуют (детально покрыто test_video_factory — здесь фиксируем контракт шва)."""
    from bossman.video_factory import model as vm, pipeline, service
    assert hasattr(vm.JobState, "INTERRUPTED")
    assert callable(pipeline.VideoFactory.checkpoint_scene)
    assert callable(service.VideoFactoryService.reconcile)
