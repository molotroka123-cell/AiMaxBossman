"""HOST-BOUNDARY RED TEAM — Stage 13: AI Lab host-path containment (claim 68a9626).

Заявление: POST /api/lab/candidates принимает ТОЛЬКО sandbox_id; путь к
trajectory.jsonl вычисляет сервер (<workspace>/<sandbox_id>/trajectory.jsonl)
с полным сдерживанием после resolve(); ошибки не раскрывают путь хоста; гейт
admin стоит ДО обработчика; аренда Resource Brain возвращается на любом исходе.

Не доверяем заявке: бьём traversal-батареей (../, ..\\, C:\\, C:/, UNC,
%2e%2e, %00, смешанные слеши, 500 символов, спуф «../victim/trajectory»),
symlink-побегом (скип при отсутствии привилегии — WinError 1314), проверяем
однообразие отказов (нет оракула «плохой id vs нет файла») и отсутствие путей
хоста в телах ошибок. Маршрутный уровень — FastAPI + реальный DeviceService.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from bossman import errors
from bossman.ai_lab import routes as lab
from bossman.ai_lab.candidates import (CandidateStore, load_trajectory,
                                       sandbox_trajectory_path)
from bossman.config import settings
from bossman.errors import install_error_handlers
from bossman.remote_client import (DeviceService, InMemoryDeviceStore,
                                   reset_service, set_service)
from bossman.remote_client.auth import SCOPE_ADMIN, SCOPE_CHAT

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------- батареи вредоносных sandbox_id ----------

EVIL_SANDBOX_IDS = [
    "../etc/passwd",
    "..\\..\\windows\\system32\\config",
    "C:\\Windows\\system32",
    "C:/Windows/win.ini",
    "\\\\server\\share\\traj",
    "//server/share/traj",
    "..%2f..%2fetc",
    "%2e%2e%2f%2e%2e%2fetc",
    "s%00id",
    "s\x00id",
    "a/../b",
    "a\\..\\b",
    "a/..\\b",                                  # смешанные слеши
    "....//....//etc",
    "..",
    "../victim/trajectory",                     # спуф чужого id
    "/etc/passwd",
    "s/../../secret",
    "sid/../../../../../../etc/shadow",
    "x" * 500,                                  # сверхдлинное имя
    "-e;calc",                                  # первый символ — не буква/цифра
    "s id", "s;id", "s\nid", "s\rid", "sid.", "sid ",
    "…",                                        # unicode
    "sid/trajectory.jsonl",
]


def _assert_no_host_path(exc: BaseException, ws: Path, tmp: Path) -> None:
    msg = str(exc)
    assert str(ws) not in msg, f"утёк workspace: {msg}"
    assert str(tmp) not in msg, f"утёк tmp-путь: {msg}"
    # путь хоста к файлу содержит «\\trajectory.jsonl» (Windows) — эхо
    # client-supplied id с «/trajectory.jsonl» допускается, host-путь нет
    assert "\\trajectory.jsonl" not in msg, f"утёк путь хоста: {msg}"


# ---------- store-уровень: sandbox_trajectory_path ----------

def test_store_valid_sandbox_resolves_inside_ws(tmp_path):
    ws = tmp_path / "sbx"
    (ws / "s123").mkdir(parents=True)
    (ws / "s123" / "trajectory.jsonl").write_text('{"kind":"note","note":"ok"}\n',
                                                  encoding="utf-8")
    p = sandbox_trajectory_path("s123", ws)
    assert p == (ws / "s123" / "trajectory.jsonl").resolve()


@pytest.mark.parametrize("evil", EVIL_SANDBOX_IDS)
def test_store_traversal_battery_denied(tmp_path, evil):
    ws = tmp_path / "sbx"
    ws.mkdir()
    with pytest.raises(errors.NotFound) as ei:
        sandbox_trajectory_path(evil, ws)
    _assert_no_host_path(ei.value, ws, tmp_path)


def test_store_denied_ids_leave_no_fs_oracle(tmp_path):
    """«Плохой id» и «нет файла» — неотличимый отказ: один код, одна форма."""
    ws = tmp_path / "sbx"
    ws.mkdir()
    (ws / "real").mkdir()
    (ws / "real" / "trajectory.jsonl").write_text('{"kind":"note","note":"ok"}\n',
                                                  encoding="utf-8")
    with pytest.raises(errors.NotFound):
        sandbox_trajectory_path("real", ws / "nope")      # нет workspace
    with pytest.raises(errors.NotFound):
        sandbox_trajectory_path("../real", ws)            # traversal к существующему
    with pytest.raises(errors.NotFound):
        sandbox_trajectory_path("missing", ws)            # валидный id, нет файла


# ---------- store-уровень: create_from_sandbox ----------

def _mk_traj(ws: Path, sid: str) -> None:
    (ws / sid).mkdir(parents=True, exist_ok=True)
    (ws / sid / "trajectory.jsonl").write_text(
        '{"kind":"shell","command":"pytest -q","output":"1 passed"}\n'
        '{"kind":"tool_call","tool":"fs.read","note":"config"}\n'
        '{"kind":"test_result","result":"green"}\n', encoding="utf-8")


def test_create_from_sandbox_happy_path(tmp_path):
    ws = tmp_path / "sbx"
    _mk_traj(ws, "sbx1")
    store = CandidateStore(tmp_path / "lab")
    cand = store.create_from_sandbox("sbx1", workspace_root=ws,
                                     sandbox_id_verified=True)
    assert cand.sandbox_id == "sbx1" and cand.samples


@pytest.mark.parametrize("evil", EVIL_SANDBOX_IDS)
def test_create_from_sandbox_traversal_battery_denied(tmp_path, evil):
    ws = tmp_path / "sbx"
    ws.mkdir()
    store = CandidateStore(tmp_path / "lab")
    with pytest.raises(errors.BossmanError) as ei:
        store.create_from_sandbox(evil, workspace_root=ws, sandbox_id_verified=True)
    _assert_no_host_path(ei.value, ws, tmp_path)
    assert not (tmp_path / "lab" / "candidates.json").exists(), \
        "отказанный id не должен оставить запись кандидата"


def test_create_from_sandbox_interlock_requires_verified_flag(tmp_path):
    ws = tmp_path / "sbx"
    _mk_traj(ws, "sbx2")
    store = CandidateStore(tmp_path / "lab")
    with pytest.raises(errors.PolicyDenied):
        store.create_from_sandbox("sbx2", workspace_root=ws)  # флаг не выставлен
    # интерлок — не обход проверки: даже с флагом traversal не проходит
    with pytest.raises(errors.NotFound):
        store.create_from_sandbox("../../etc", workspace_root=ws,
                                   sandbox_id_verified=True)


def test_create_from_sandbox_symlink_file_escape(tmp_path):
    ws = tmp_path / "sbx"
    secret = tmp_path / "host_secret.jsonl"
    secret.write_text('{"kind":"note","note":"TOP SECRET"}\n', encoding="utf-8")
    (ws / "evil").mkdir(parents=True)
    try:
        (ws / "evil" / "trajectory.jsonl").symlink_to(secret)
    except OSError:
        pytest.skip("symlink privilege missing (WinError 1314)")
    store = CandidateStore(tmp_path / "lab")
    with pytest.raises(errors.NotFound) as ei:
        store.create_from_sandbox("evil", workspace_root=ws, sandbox_id_verified=True)
    _assert_no_host_path(ei.value, ws, tmp_path)


def test_create_from_sandbox_symlink_dir_escape(tmp_path):
    ws = tmp_path / "sbx"
    outside = tmp_path / "outside" / "realid"
    outside.mkdir(parents=True)
    (outside / "trajectory.jsonl").write_text('{"kind":"note","note":"ok"}\n',
                                              encoding="utf-8")
    try:
        (ws / "realid").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege missing (WinError 1314)")
    store = CandidateStore(tmp_path / "lab")
    with pytest.raises(errors.NotFound):
        store.create_from_sandbox("realid", workspace_root=ws,
                                  sandbox_id_verified=True)


def test_create_from_sandbox_vanished_file_is_uniform_404(tmp_path):
    ws = tmp_path / "sbx"
    _mk_traj(ws, "gone")
    (ws / "gone" / "trajectory.jsonl").unlink()
    store = CandidateStore(tmp_path / "lab")
    with pytest.raises(errors.NotFound) as ei:
        store.create_from_sandbox("gone", workspace_root=ws, sandbox_id_verified=True)
    _assert_no_host_path(ei.value, ws, tmp_path)


def test_load_trajectory_error_does_not_leak_host_path(tmp_path):
    missing = tmp_path / "deep" / "nested" / "secret.jsonl"
    with pytest.raises(errors.BossmanError) as ei:
        load_trajectory(missing)
    assert str(tmp_path) not in str(ei.value)
    assert ei.value.code == errors.ErrorCode.NOT_FOUND


# ---------- маршрутный уровень: /api/lab/* ----------

@pytest.fixture
def svc():
    s = DeviceService(InMemoryDeviceStore())
    set_service(s)
    try:
        yield s
    finally:
        reset_service()


@pytest.fixture
def sandbox_ws(tmp_path, monkeypatch):
    root = tmp_path / "sbx"
    root.mkdir()
    monkeypatch.setattr(lab, "_sandbox_workspace", lambda: root)
    return root


@pytest.fixture
def lab_app(tmp_path, monkeypatch, svc, sandbox_ws):
    monkeypatch.setattr(settings, "workspace_dir", tmp_path, raising=False)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(lab.router)
    return app


async def _token(svc, scopes) -> str:
    _, raw = await svc.enroll("dev", set(scopes))
    return raw


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://t")


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def test_route_anonymous_denied(lab_app):
    async with _client(lab_app) as c:
        r = await c.post("/api/lab/candidates", json={"sandbox_id": "s1"})
    assert r.status_code in (401, 403)


async def test_route_chat_scope_denied_admin_only(lab_app, svc, sandbox_ws, tmp_path):
    tok = await _token(svc, {SCOPE_CHAT})
    _mk_traj(sandbox_ws, "s9")
    async with _client(lab_app) as c:
        r = await c.post("/api/lab/candidates", json={"sandbox_id": "s9"},
                         headers=_bearer(tok))
    assert r.status_code == 403
    # обработчик не выполнялся: ни одного кандидата не создано
    assert not (tmp_path / "_ai_lab" / "candidates.json").exists()


@pytest.mark.parametrize("evil", [
    "../etc/passwd", "C:\\Windows\\system32", "\\\\srv\\share\\t",
    "%2e%2e%2f%2e%2e", "..%2f..%2fetc", "s%00id",
    "../victim/trajectory", "a\\..\\b", "x" * 500,
])
async def test_route_create_traversal_battery_404_no_host_path(
        lab_app, svc, sandbox_ws, tmp_path, evil):
    tok = await _token(svc, {SCOPE_ADMIN})
    async with _client(lab_app) as c:
        r = await c.post("/api/lab/candidates", json={"sandbox_id": evil},
                         headers=_bearer(tok))
    assert r.status_code == 404, f"{evil!r}: {r.status_code} {r.text}"
    assert str(sandbox_ws) not in r.text
    assert str(tmp_path) not in r.text
    assert "trajectory.jsonl" not in r.text


@pytest.mark.parametrize("evil", [
    "%2e%2e%2fetc", "..%2f..%2fsecret", "s%00id", "C:%5CWindows",
])
async def test_route_get_trajectory_encoded_traversal_404(lab_app, svc, evil):
    tok = await _token(svc, {SCOPE_ADMIN})
    async with _client(lab_app) as c:
        r = await c.get(f"/api/lab/trajectories/{evil}", headers=_bearer(tok))
    assert r.status_code == 404
    assert "trajectory.jsonl" not in r.text


async def test_route_create_valid_sandbox_and_no_host_path_in_list(
        lab_app, svc, sandbox_ws, tmp_path):
    tok = await _token(svc, {SCOPE_ADMIN})
    _mk_traj(sandbox_ws, "s123")
    async with _client(lab_app) as c:
        r = await c.post("/api/lab/candidates", json={"sandbox_id": "s123"},
                         headers=_bearer(tok))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "CANDIDATE" and body["samples"] >= 1
        lst = await c.get("/api/lab/candidates", headers=_bearer(tok))
    assert lst.status_code == 200
    assert str(tmp_path) not in lst.text
    assert "trajectory.jsonl" not in lst.text
    for row in lst.json():
        assert "trajectory_path" not in row


async def test_route_client_path_is_honored_never(lab_app, svc, sandbox_ws, tmp_path):
    """Файл ВНЕ workspace нельзя подсунуть никаким sandbox_id."""
    (tmp_path / "outside.jsonl").write_text(
        '{"kind":"shell","command":"x","output":"y"}\n', encoding="utf-8")
    tok = await _token(svc, {SCOPE_ADMIN})
    for sid in ("../outside", "..\\outside", "outside", "%2e%2e%2foutside"):
        async with _client(lab_app) as c:
            r = await c.post("/api/lab/candidates", json={"sandbox_id": sid},
                             headers=_bearer(tok))
        assert r.status_code == 404, f"{sid!r}: {r.status_code}"


# ---------- лизинг Resource Brain на маршруте /evals/run ----------

class _Lease:
    def __init__(self, i): self.id = i


class _SpyBrain:
    def __init__(self, *, exhausted=False):
        self.current_snapshot = None
        self.acquired = 0
        self.released = []
        self._exhausted = exhausted

    def acquire(self, req, snap=None):
        if self._exhausted:
            raise errors.ResourceExhausted("no capacity")
        self.acquired += 1
        return _Lease(f"L{self.acquired}")

    def release(self, lease_id):
        self.released.append(lease_id)
        return True

    @property
    def held(self):
        return self.acquired - len(self.released)


async def test_route_evals_run_releases_lease(lab_app, svc, monkeypatch):
    import bossman.resource_brain as rb
    b = _SpyBrain()
    monkeypatch.setattr(rb, "BRAIN", b)
    tok = await _token(svc, {SCOPE_ADMIN})
    async with _client(lab_app) as c:
        r = await c.post("/api/lab/evals/run",
                         json={"cases": [{"id": "1", "prompt": "p", "expected": "ok"}],
                               "max_cases": 1},
                         headers=_bearer(tok))
    assert r.status_code == 200, r.text
    assert b.acquired == 1 and b.held == 0, "аренда не вернулась после eval"


async def test_route_evals_run_exhausted_brain_503_no_model_calls(lab_app, svc, monkeypatch):
    import bossman.resource_brain as rb
    b = _SpyBrain(exhausted=True)
    monkeypatch.setattr(rb, "BRAIN", b)
    tok = await _token(svc, {SCOPE_ADMIN})
    async with _client(lab_app) as c:
        r = await c.post("/api/lab/evals/run",
                         json={"cases": [{"id": "1", "prompt": "p", "expected": "x"}],
                               "max_cases": 1},
                         headers=_bearer(tok))
    assert r.status_code == 503
    assert b.acquired == 0 and b.released == []
