"""AI Lab: sandbox_id-контейнмент вместо произвольного пути + лизинг Resource Brain.

Дыры Stage 11: (A) POST /candidates/{trajectory_path:path} принимал ЛЮБОЙ путь
хоста — чтение произвольного файла; (B) EvalRunner брал аренду Resource Brain и
терял её (finally: pass) — утечка ёмкости.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bossman import errors
from bossman.ai_lab import routes as lab
from bossman.ai_lab.export import EvalRunner


# ---------- A. контейнмент пути ----------

@pytest.fixture
def sandbox_ws(tmp_path, monkeypatch):
    root = tmp_path / "sbx"
    root.mkdir()
    monkeypatch.setattr(lab, "_sandbox_workspace", lambda: root)
    return root


def _make_traj(root: Path, sid: str) -> None:
    d = root / sid
    d.mkdir(parents=True)
    (d / "trajectory.jsonl").write_text('{"kind":"note","note":"ok"}\n', encoding="utf-8")


def test_valid_sandbox_id_resolves(sandbox_ws):
    _make_traj(sandbox_ws, "s123")
    p = lab._trajectory_path("s123")
    assert p == (sandbox_ws / "s123" / "trajectory.jsonl").resolve()


@pytest.mark.parametrize("evil", [
    "../etc/passwd",
    "../../etc/shadow",
    "/etc/passwd",
    "C:\\Windows\\system32",
    "\\\\host\\share",
    "..%2f..%2fetc",
    "s\x00id",
    "s/../../secret",
    "....//....//etc",
    "s id with spaces",
    "s;rm-rf",
    "s\nid",
])
def test_traversal_and_junk_denied(sandbox_ws, evil):
    with pytest.raises(errors.NotFound):
        lab._trajectory_path(evil)


def test_nonexistent_sandbox_id_denied(sandbox_ws):
    with pytest.raises(errors.NotFound):
        lab._trajectory_path("neverexisted")


def test_symlink_escape_denied(sandbox_ws, tmp_path):
    # sandbox_id-каталог, где trajectory.jsonl — симлинк на файл хоста ВНЕ workspace
    secret = tmp_path / "host_secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    d = sandbox_ws / "evil"
    d.mkdir()
    try:
        (d / "trajectory.jsonl").symlink_to(secret)
    except OSError:
        pytest.skip("symlink not permitted on this fs")
    with pytest.raises(errors.NotFound):
        lab._trajectory_path("evil")


def test_error_does_not_leak_host_path(sandbox_ws):
    try:
        lab._trajectory_path("/etc/passwd")
    except errors.NotFound as exc:
        assert str(sandbox_ws) not in str(exc)
        assert "/etc/passwd" not in exc.detail or "trajectory not found" in exc.detail


# ---------- B. лизинг Resource Brain ----------

class _Lease:
    def __init__(self, i): self.id = i


class _SpyBrain:
    """Считает acquire/release. По умолчанию отдаёт аренду; можно заставить
    отказать (ResourceExhausted) — тогда release НЕ должен вызываться."""

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


def test_release_on_success():
    b = _SpyBrain()
    EvalRunner(chat_fn=None, brain=b).run([{"id": 1, "prompt": "x", "expected": ""}],
                                          model_alias="m", max_cases=1)
    assert b.held == 0


def test_release_on_model_exception():
    def boom(**_):
        raise RuntimeError("model down")
    b = _SpyBrain()
    with pytest.raises(RuntimeError):
        EvalRunner(chat_fn=boom, brain=b).run(
            [{"id": 1, "prompt": "x", "expected": "y"}], model_alias="m", max_cases=1)
    assert b.held == 0, "аренда не возвращена после сбоя модели"


def test_exhausted_makes_zero_model_calls_and_no_leak():
    calls = {"n": 0}

    def chat(**_):
        calls["n"] += 1
        return {"choices": [{"message": {"content": ""}}]}

    b = _SpyBrain(exhausted=True)
    with pytest.raises(errors.ResourceExhausted):
        EvalRunner(chat_fn=chat, brain=b).run(
            [{"id": 1, "prompt": "x", "expected": ""}], model_alias="m", max_cases=1)
    assert calls["n"] == 0
    assert b.released == []       # аренды не было — releasing нечего


def test_hundred_sequential_evals_return_to_baseline():
    b = _SpyBrain()
    r = EvalRunner(chat_fn=None, brain=b)
    for _ in range(100):
        r.run([{"id": 1, "prompt": "x", "expected": ""}], model_alias="m", max_cases=1)
    assert b.held == 0
    assert b.acquired == 100 and len(b.released) == 100


def test_empty_eval_still_releases():
    b = _SpyBrain()
    EvalRunner(chat_fn=None, brain=b).run([], model_alias="m", max_cases=0)
    assert b.held == 0
