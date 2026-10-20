from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _load_grechka_watcher():
    watcher_path = Path(__file__).resolve().parents[2] / "grechka-trap" / "watcher.py"
    spec = importlib.util.spec_from_file_location("grechka_trap_watcher", watcher_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_p0_item2_terminal_start_blocks_when_stop_file_exists(tmp_path: Path):
    from bcc.v2.terminal_control import TerminalManager, TerminalPolicy

    data_dir = tmp_path / "data"
    stop_file = data_dir / "computer" / "STOP"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.write_text("owner\n", encoding="utf-8")

    work = tmp_path / "work"
    work.mkdir()
    mgr = TerminalManager(data_dir=data_dir)
    policy = TerminalPolicy(allowed_roots=[work], mode="project_host")

    with pytest.raises(PermissionError, match="STOP"):
        asyncio.run(mgr.start("git status", work, policy, approved=True))


def test_p0_item2_coding_tasks_create_and_execute_block_when_stop_file_exists(tmp_path: Path):
    from bcc.features import coding_tasks as ct

    data_dir = tmp_path / "data"
    stop_file = data_dir / "computer" / "STOP"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.write_text("owner\n", encoding="utf-8")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=data_dir))
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(svc=svc)))
    body = ct.TaskIn(instruction="fix bug", source_repo=str(repo), allowed_paths=["src/"])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(ct.create_task(body, req))
    assert exc_info.value.status_code == 423
    assert exc_info.value.detail["code"] == "OWNER_STOP"

    rec = {"id": "abcdef012345", "status": "running", "source_repo": str(repo)}
    out = ct._execute(rec, repo, body, data_dir=data_dir)
    assert out["status"] == "failed"
    assert out["outcome"] == "CANCELLED"
    assert "STOP" in out["error"]


def test_p0_item2_direct_gen_and_qwen_operator_do_not_resume_when_stop_exists(tmp_path: Path):
    from bcc.direct_gen.service import DirectGenService
    from bcc.direct_gen.store import JobStore
    from bcc.features import qwen_operator as qo

    data_dir = tmp_path / "data"
    stop_file = data_dir / "computer" / "STOP"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.write_text("owner\n", encoding="utf-8")

    # 1) direct_gen recovery must mark unfinished jobs as stopped when STOP exists
    store = JobStore(data_dir)
    job_id = "a" * 32
    store.save({
        "job_id": job_id,
        "participant": "owner",
        "kind": "photo",
        "model": "qwen-image-2512",
        "status": "generating",
        "stage": "generating",
        "progress": 0.5,
        "created_at": "2026-10-20T00:00:00+00:00",
        "timeline": [],
        "provenance": {"runtime": "sdcpp"},
    })
    svc = SimpleNamespace(
        settings=SimpleNamespace(data_dir=data_dir),
        bus=SimpleNamespace(emit=lambda *a, **kw: None),
        vault=None,
    )
    DirectGenService(svc)
    recovered_job = store.load("owner", job_id)
    assert recovered_job["status"] == "stopped"
    assert recovered_job["error"]["reason"] == "owner_stop_active"

    # 2) qwen_operator recovery must mark unfinished tasks as stopped when STOP exists
    lab_dir = tmp_path / "lab"
    lab_dir.mkdir()
    rid = "123456abcdef"
    qo_dir = data_dir / "qwen-operator"
    qo_dir.mkdir(parents=True, exist_ok=True)
    (qo_dir / f"{rid}.json").write_text(
        '{"id": "123456abcdef", "task": "test", "status": "running", "created_at": 1000.0, '
        '"lab": {"path": "' + str(lab_dir).replace("\\", "\\\\") + '"}}',
        encoding="utf-8",
    )
    outcomes = asyncio.run(qo.recover_interrupted_tasks(svc, resume=True))
    assert outcomes == [{"id": rid, "action": "stopped", "reason": outcomes[0]["reason"]}]
    assert "STOP" in outcomes[0]["reason"]


def test_p0_item2_resume_after_takeover_keeps_stop_if_observe_fails(tmp_path: Path, monkeypatch):
    from bcc.features import tools_computer as tc

    data_dir = tmp_path / "data"
    svc = SimpleNamespace(
        settings=SimpleNamespace(data_dir=data_dir),
        bus=SimpleNamespace(emit=lambda *a, **kw: asyncio.sleep(0)),
    )
    asyncio.run(tc.pause_for_takeover(svc, reason="manual_step"))
    st = tc._owner_state(svc)
    assert st.stopped() is True
    assert st.stop_path.is_file()

    async def _failing_observe(*args, **kwargs):
        raise RuntimeError("screen capture failed")

    monkeypatch.setattr(tc, "observe", _failing_observe)
    with pytest.raises(RuntimeError, match="screen capture failed"):
        asyncio.run(tc.resume_after_takeover(svc))

    # STOP must remain active on disk and in state because observe() failed
    assert st.stopped() is True
    assert st.stop_path.is_file()


def test_p0_item3_rt01_direct_gen_who_and_mobile_proxy_strip_headers():
    from bcc.features import direct_gen as dg
    from bossmobile.proxy import DROP_REQ

    def _make_req(headers: list[tuple[bytes, bytes]]) -> Request:
        return Request({"type": "http", "method": "GET", "path": "/api/direct-gen/timings", "headers": headers})

    # Empty X-Participant or X-BCC-Participant must return 422
    with pytest.raises(HTTPException) as exc1:
        dg._who("", _make_req([(b"x-participant", b"")]))
    assert exc1.value.status_code == 422

    with pytest.raises(HTTPException) as exc2:
        dg._who(None, _make_req([(b"x-bcc-participant", b"   ")]))
    assert exc2.value.status_code == 422

    # Valid non-owner 64-hex participant via X-BCC-Participant on timings() must be rejected (404 owner-only)
    other_pit = "b" * 64
    req_other = _make_req([(b"x-bcc-participant", other_pit.encode("ascii"))])
    with pytest.raises(HTTPException) as exc3:
        asyncio.run(dg.timings(req_other, limit=10, x_participant=None))
    assert exc3.value.status_code == 404

    # Mobile proxy strips both headers
    assert "x-participant" in DROP_REQ
    assert "x-bcc-participant" in DROP_REQ


def test_p0_item4_grechka_trap_watcher_filters_foreign_sender_and_deduplicates():
    watcher = _load_grechka_watcher()
    owner_id = 777000
    seen: set[tuple[int, int]] = set()

    updates = [
        # Foreign sender in another chat -> rejected
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 999999},
                "from": {"id": 999999},
                "photo": [{"file_id": "foreign_file_1", "file_size": 1234}],
            },
        },
        # Foreign sender spoofed inside owner chat -> rejected
        {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "chat": {"id": owner_id},
                "from": {"id": 888888},
                "photo": [{"file_id": "foreign_file_2", "file_size": 1234}],
            },
        },
        # Owner sender in foreign group chat -> rejected
        {
            "update_id": 3,
            "message": {
                "message_id": 12,
                "chat": {"id": -10012345},
                "from": {"id": owner_id},
                "photo": [{"file_id": "group_file_3", "file_size": 1234}],
            },
        },
        # Valid owner message -> accepted
        {
            "update_id": 4,
            "message": {
                "message_id": 13,
                "chat": {"id": owner_id},
                "from": {"id": owner_id},
                "photo": [{"file_id": "owner_file_4", "file_size": 2048}],
            },
        },
        # Duplicate (chat_id, message_id) in same batch -> deduplicated
        {
            "update_id": 5,
            "message": {
                "message_id": 13,
                "chat": {"id": owner_id},
                "from": {"id": owner_id},
                "photo": [{"file_id": "owner_file_4_dup", "file_size": 2048}],
            },
        },
    ]

    todo = watcher.filter_owner_candidates(updates, owner_id, seen, first_run=False)
    assert len(todo) == 1
    assert todo[0] == (owner_id, 13, "photo", "owner_file_4", 2048)

    # Mark as seen and verify second pass is empty
    seen.add((owner_id, 13))
    todo_again = watcher.filter_owner_candidates(updates, owner_id, seen, first_run=False)
    assert todo_again == []
