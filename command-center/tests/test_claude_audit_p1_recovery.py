"""Tests for Claude Audit P1 Recovery & Runtime items 12..18."""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import sys
from pathlib import Path
from PIL import Image

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "command-center") not in sys.path:
    sys.path.insert(0, str(ROOT / "command-center"))

from bcc.direct_gen import resident, sdcli, timings
from bcc.direct_gen.service import DirectGenService
from bcc.features import qwen_operator as qo
from bcc.telegram_companion.config import CompanionError, Person
from bcc.telegram_companion.operator_bridge import OperatorBridgeMixin


def _make_valid_png(path: Path) -> bytes:
    img = Image.new("RGB", (64, 64), (10, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


class _DummyBus:
    async def emit(self, *a, **kw):
        return None


class _DummySettings:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir


class _DummyCoreSvc:
    def __init__(self, data_dir: Path):
        self.settings = _DummySettings(data_dir)
        self.bus = _DummyBus()


# ------------------------------------------------------------------ Item 12: PNG IEND / .part + facelock pending
def test_item12_png_valid_checks_iend_and_decode(tmp_path: Path):
    good = tmp_path / "good.png"
    raw = _make_valid_png(good)
    assert sdcli.png_valid(good) == (64, 64)

    truncated = tmp_path / "truncated.png"
    truncated.write_bytes(raw[:-16])  # missing IEND trailer
    assert sdcli.png_valid(truncated) is None

    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(raw[:33] + b"\x00\x00\x00\x08IDATbad_zlib" + sdcli.PNG_IEND)
    assert sdcli.png_valid(corrupt) is None


def test_item12_atomic_write_uses_part_and_recover_blocks_facelock_pending(tmp_path: Path):
    target = tmp_path / "out" / "img.png"
    raw = _make_valid_png(tmp_path / "src.png")
    resident._atomic_write_bytes(target, raw)
    assert target.exists() and sdcli.png_valid(target) == (64, 64)
    assert not target.with_suffix(".png.part").exists()

    core_svc = _DummyCoreSvc(tmp_path / "data")
    svc = DirectGenService(core_svc, media_dir=tmp_path / "media")
    jid = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
    folder = svc.store.job_dir("owner", jid)
    res_dir = folder / "result"
    res_dir.mkdir(parents=True, exist_ok=True)
    _make_valid_png(res_dir / "image.png")

    job_pending_fl = {
        "job_id": jid,
        "participant": "owner",
        "kind": "photo",
        "status": "generating",
        "stage": "generating",
        "timeline": [],
        "facelock": {"requested": True, "status": "pending"},
    }
    assert svc._recover_completed_output(job_pending_fl, folder) is False

    job_done_fl = {
        "job_id": jid,
        "participant": "owner",
        "kind": "photo",
        "status": "generating",
        "stage": "generating",
        "timeline": [],
        "facelock": {"requested": True, "status": "applied"},
    }
    assert svc._recover_completed_output(job_done_fl, folder) is True
    assert job_done_fl["status"] == "completed"


# ------------------------------------------------------------------ Item 13: qwen_operator cancel_requested, step markers, git clean
def test_item13_qwen_operator_cancel_requested_and_git_clean_on_recover(tmp_path: Path, monkeypatch):
    repo = tmp_path / "lab"
    repo.mkdir()
    qo._git(repo, "init", "-b", "main")
    (repo / "tracked.txt").write_text("clean\n", encoding="utf-8")
    qo._git(repo, "config", "user.email", "t@t")
    qo._git(repo, "config", "user.name", "t")
    qo._git(repo, "add", "-A")
    qo._git(repo, "commit", "-m", "init")

    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    (repo / "untracked.tmp").write_text("junk\n", encoding="utf-8")

    data_dir = tmp_path / "data"
    op_dir = data_dir / "qwen-operator"
    op_dir.mkdir(parents=True)

    id_cancel = "111111111111"
    id_dirty = "222222222222"

    rec_cancel = {
        "id": id_cancel,
        "task": "test cancel",
        "status": "running",
        "cancel_requested": True,
        "created_at": dt.datetime.now(dt.timezone.utc).timestamp(),
        "lab": {"path": str(repo)},
        "steps": [],
    }
    (op_dir / f"{id_cancel}.json").write_text(json.dumps(rec_cancel), encoding="utf-8")

    rec_dirty = {
        "id": id_dirty,
        "task": "test dirty reset",
        "status": "running",
        "cancel_requested": False,
        "created_at": dt.datetime.now(dt.timezone.utc).timestamp(),
        "lab": {"path": str(repo)},
        "steps": [],
        "decisions_parked_done": True,
        "extra_commands_done": [0],
        "bossman_calls_done": [0],
    }
    (op_dir / f"{id_dirty}.json").write_text(json.dumps(rec_dirty), encoding="utf-8")

    resumed_ids: list[str] = []
    core_svc = _DummyCoreSvc(data_dir)
    core_svc.start_workers = True

    def fake_spawn(svc, rec, **kw):
        resumed_ids.append(rec["id"])

    monkeypatch.setattr(qo, "_spawn_task", fake_spawn)

    recovered = asyncio.run(qo.recover_interrupted_tasks(core_svc, resume=True))
    saved_cancel = json.loads((op_dir / f"{id_cancel}.json").read_text(encoding="utf-8"))
    assert saved_cancel["status"] == "stopped"
    assert id_cancel not in resumed_ids
    assert any(r["id"] == id_dirty and r["action"] == "resumed" for r in recovered)
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "clean\n"
    assert not (repo / "untracked.tmp").exists()


# ------------------------------------------------------------------ Item 14: Stale >30 min not auto-resumed + explicit ref_images_count
def test_item14_stale_tasks_not_auto_resumed_and_ref_images_count_checked(tmp_path: Path):
    core_svc = _DummyCoreSvc(tmp_path / "data")
    svc = DirectGenService(core_svc, media_dir=tmp_path / "media")
    stale_iso = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=DirectGenService.STALE_RESUME_SECONDS + 120)).isoformat()

    stale_id = "aaaaaaaaaaaa11111111111111111111"
    missing_id = "bbbbbbbbbbbb22222222222222222222"

    stale_job = {
        "job_id": stale_id,
        "participant": "owner",
        "kind": "photo",
        "model": "qwen-image-2.1",
        "status": "generating",
        "stage": "generating",
        "created_at": stale_iso,
        "timeline": [],
        "params": {"prompt": "test"},
    }
    svc.store.save(stale_job)

    missing_ref_job = {
        "job_id": missing_id,
        "participant": "owner",
        "kind": "photo",
        "model": "qwen-image-2.1-uc-edit",
        "status": "queued",
        "stage": "queued",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "timeline": [],
        "provenance": {"runtime": "sdcpp"},
        "params": {"prompt": "edit", "ref_images_count": 2},
        "inputs": {"images": 2, "ref_images_count": 2},
    }
    folder = svc.store.job_dir("owner", missing_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "input_image.bin").write_bytes(_make_valid_png(tmp_path / "ref1.png"))
    svc.store.save(missing_ref_job)

    async def _run_recover():
        return DirectGenService(core_svc, media_dir=tmp_path / "media")

    svc2 = asyncio.run(_run_recover())
    loaded_stale = svc2.store.load("owner", stale_id)
    assert loaded_stale["status"] == "stopped"
    assert loaded_stale.get("needs_resume_confirmation") is True
    assert loaded_stale["error"]["code"] == "stale_task_requires_confirmation"

    loaded_ref = svc2.store.load("owner", missing_id)
    assert loaded_ref["status"] == "failed"
    assert loaded_ref["error"]["reason"] == "missing_reference_image"


# ------------------------------------------------------------------ Item 16: Ollama unloads only helper model
def test_item16_direct_gen_unloads_only_helper_model(tmp_path: Path, monkeypatch):
    core_svc = _DummyCoreSvc(tmp_path / "data")
    svc = DirectGenService(core_svc, media_dir=tmp_path / "media")
    svc._last_assist_model = "huihui_ai/qwen2.5-vl-abliterated:32b"
    unloaded: list[str] = []

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url):
            raise AssertionError("Should not query /api/ps to unload every model")

        async def post(self, url, json=None):
            unloaded.append((json or {}).get("model"))

            class R:
                status_code = 200
            return R()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    asyncio.run(svc._unload_ollama())
    assert unloaded == ["huihui_ai/qwen2.5-vl-abliterated:32b"]


# ------------------------------------------------------------------ Item 17: sd-server no-progress ceiling & timings skip resumed
def test_item17_sd_server_killed_on_no_progress_and_timings_skip_resumed(tmp_path: Path):
    log = timings.TimingLog(tmp_path / "timings_root")
    normal_job = {
        "job_id": "j-normal",
        "kind": "photo",
        "status": "completed",
        "model": "qwen-image-2.1",
        "created_at": "2026-10-20T08:00:00+00:00",
        "started_at": "2026-10-20T08:00:01+00:00",
        "finished_at": "2026-10-20T08:00:25+00:00",
        "timeline": [
            {"stage": "queued", "at": "2026-10-20T08:00:00+00:00"},
            {"stage": "loading", "at": "2026-10-20T08:00:01+00:00"},
            {"stage": "generating", "at": "2026-10-20T08:00:05+00:00"},
            {"stage": "completed", "at": "2026-10-20T08:00:25+00:00"},
        ],
        "params": {"width": 1024, "height": 1024, "steps": 20},
        "provenance": {"runtime": "sdcpp"},
    }
    resumed_job = {
        **normal_job,
        "job_id": "j-resumed",
        "resumed_count": 1,
    }
    row_normal = timings.measure(normal_job)
    row_resumed = timings.measure(resumed_job)
    assert row_normal["resumed"] is False
    assert row_resumed["resumed"] is True
    log.append(row_normal)
    log.append(row_resumed)
    rows = log.read()
    assert len(rows) == 1 and rows[0]["job_id"] == "j-normal"
    summary = timings.summary([row_normal, row_resumed])
    assert summary["qwen-image-2.1"]["n"] == 1


# ------------------------------------------------------------------ Item 18: operator_bridge store persistence, send-before-remove, shutdown
class _FakeStore:
    def __init__(self):
        self.data: dict = {}

    def get(self, k, default=None):
        return self.data.get(k, default)

    def put(self, k, v):
        self.data[k] = v


class _FakeTelegram:
    def __init__(self, fail_Send=False):
        self.sent: list[tuple[str, str]] = []
        self.fail_send = fail_Send

    async def send(self, person, text, keyboard=None):
        if self.fail_send:
            raise CompanionError("SEND_FAILED")
        self.sent.append((person.key, text))


class _FakeCore:
    def __init__(self, rec: dict):
        self.rec = rec
        self.cancelled: list[str] = []

    async def operator_task(self, task_id: str) -> dict:
        return self.rec

    async def operator_cancel(self, task_id: str):
        self.cancelled.append(task_id)


class _DummyCompanion(OperatorBridgeMixin):
    POLL_SECONDS = 0.01

    def __init__(self, store: _FakeStore, tg: _FakeTelegram, core: _FakeCore):
        self.store = store
        self.telegram = tg
        self.core = core
        self.agent_jobs: dict = {}

    def agent_policy(self, person, message=None):
        return {"owner": True}

    def button(self, person, label, cmd):
        return {"text": label, "cmd": cmd}


def test_item18_operator_bridge_persists_decisions_and_keeps_backend_task_on_shutdown(monkeypatch):
    import bcc.telegram_companion.operator_bridge as ob
    monkeypatch.setattr(ob, "SEND_RETRIES", 1)
    monkeypatch.setattr(ob, "BACKOFF_BASE", 0.001)

    owner = Person(user_id=123, chat_id=123, role="owner")
    store = _FakeStore()
    store.put("operator_active_tasks", ["op-1"])

    rec = {
        "id": "op-1",
        "status": "completed_with_pending_decisions",
        "decisions": [{"approval_id": 77, "status": "PENDING", "category": "release_main", "action": "merge"}],
    }
    tg_fail = _FakeTelegram(fail_Send=True)
    core = _FakeCore(rec)
    comp_fail = _DummyCompanion(store, tg_fail, core)
    asyncio.run(comp_fail.work_turn(owner, "op-1"))
    assert "op-1" in store.get("operator_active_tasks", [])
    assert store.get("operator_unsent_receipt") == "op-1"
    assert 77 not in store.get("operator_announced_decisions:op-1", [])

    tg_ok = _FakeTelegram(fail_Send=False)
    comp_ok = _DummyCompanion(store, tg_ok, core)
    asyncio.run(comp_ok.work_turn(owner, "op-1"))
    assert 77 in store.get("operator_announced_decisions:op-1", [])
    assert "op-1" not in store.get("operator_active_tasks", [])
    decision_msgs_first = [t for _, t in tg_ok.sent if "Решение владельца" in t]
    assert len(decision_msgs_first) == 1

    tg_ok2 = _FakeTelegram(fail_Send=False)
    comp_ok2 = _DummyCompanion(store, tg_ok2, core)
    asyncio.run(comp_ok2.work_turn(owner, "op-1"))
    decision_msgs_second = [t for _, t in tg_ok2.sent if "Решение владельца" in t]
    assert len(decision_msgs_second) == 0

    rec_running = {"id": "op-run", "status": "running", "decisions": []}
    core_run = _FakeCore(rec_running)
    comp_run = _DummyCompanion(store, tg_ok, core_run)
    comp_run.POLL_SECONDS = 10.0

    async def _shutdown_cancel():
        t = asyncio.create_task(comp_run.work_turn(owner, "op-run"))
        await asyncio.sleep(0.02)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(_shutdown_cancel())
    assert core_run.cancelled == []
