"""SECREM: ResourceSampler.peak_ollama_rss считает ДЕРЕВО процессов ollama.

Раньше метрика брала max RSS одного процесса с именем `ollama` — сервер-родитель
(десятки МБ), а `ollama runner` (ребёнок, держит веса модели — гигабайты) не
учитывался. Здесь psutil подменяется фейком: доказываем, что RSS родителя и
ребёнка СУММИРУЮТСЯ, что процесс с «ollama» в cmdline попадает в сумму, что
pid не считается дважды, и что отказ psutil помечает метрику partial, а не
занижает её молча.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _ab():
    import local_hardware_ab as ab
    return importlib.reload(ab)


class FakeProc:
    def __init__(self, pid, name, rss, cmdline=None, children=(), *, raise_children=False):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "cmdline": list(cmdline or [name]),
                     "memory_info": SimpleNamespace(rss=rss)}
        self._children = list(children)
        self._raise_children = raise_children

    def memory_info(self):
        return self.info["memory_info"]

    def children(self, recursive=False):
        if self._raise_children:
            raise psutil.AccessDenied(self.pid)
        return list(self._children)


@pytest.fixture
def no_gateway_no_gpu(monkeypatch):
    """Гейтвей и nvidia-smi в тесте не нужны: их отказ — штатный путь."""
    monkeypatch.setattr(psutil, "Process", lambda pid: (_ for _ in ()).throw(psutil.NoSuchProcess(pid)))
    import subprocess
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: (_ for _ in ()).throw(OSError("no nvidia-smi")))


def test_parent_and_runner_child_rss_are_summed(monkeypatch, no_gateway_no_gpu):
    ab = _ab()
    runner = FakeProc(201, "ollama", 3_000_000_000, cmdline=["/usr/bin/ollama", "runner", "--model", "x.gguf"])
    server = FakeProc(200, "ollama", 50_000_000, cmdline=["ollama", "serve"], children=[runner])
    other = FakeProc(1, "systemd", 10_000_000, cmdline=["/sbin/init"])
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None, ad_value=None: [other, server])

    s = ab.ResourceSampler(gateway_pid=999)
    s.sample_once()
    assert s.peak_ollama_rss == 3_050_000_000, "runner-ребёнок должен быть просуммирован с родителем"
    assert s.ollama_partial is False
    out = s.stop()
    assert out["peak_ollama_rss_mib"] == round(3_050_000_000 / 1024 / 1024, 2)
    assert out["peak_ollama_rss_scope"] == "process_tree"
    assert out["peak_ollama_rss_partial"] is False


def test_child_listed_by_process_iter_is_not_double_counted(monkeypatch, no_gateway_no_gpu):
    """process_iter отдаёт и родителя, и ребёнка; ребёнок также в children()."""
    ab = _ab()
    runner = FakeProc(301, "ollama", 2_000_000_000, cmdline=["ollama", "runner"])
    server = FakeProc(300, "ollama", 40_000_000, cmdline=["ollama", "serve"], children=[runner])
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None, ad_value=None: [server, runner])
    s = ab.ResourceSampler(gateway_pid=999)
    s.sample_once()
    assert s.peak_ollama_rss == 2_040_000_000


def test_llama_server_with_ollama_in_cmdline_counts_without_parent_link(monkeypatch, no_gateway_no_gpu):
    """Детач-случай: runner не ребёнок (или дерево недоступно), но cmdline содержит ollama."""
    ab = _ab()
    server = FakeProc(400, "ollama", 30_000_000, cmdline=["ollama", "serve"], raise_children=True)
    llama = FakeProc(777, "llama-server", 1_500_000_000,
                     cmdline=["/tmp/ollama/runners/cuda/llama-server", "--model", "m.gguf"])
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None, ad_value=None: [server, llama])
    s = ab.ResourceSampler(gateway_pid=999)
    s.sample_once()
    assert s.peak_ollama_rss == 1_530_000_000
    # children() упал → честная пометка partial (дерево могли обойти не полностью)
    assert s.ollama_partial is True
    assert s.stop()["peak_ollama_rss_partial"] is True


def test_process_iter_failure_marks_partial_instead_of_silent_zero(monkeypatch, no_gateway_no_gpu):
    ab = _ab()

    def boom(attrs=None, ad_value=None):
        raise psutil.AccessDenied(0)

    monkeypatch.setattr(psutil, "process_iter", boom)
    s = ab.ResourceSampler(gateway_pid=999)
    s.sample_once()
    assert s.peak_ollama_rss == 0
    assert s.stop()["peak_ollama_rss_partial"] is True


def test_peak_is_monotonic_across_samples(monkeypatch, no_gateway_no_gpu):
    ab = _ab()
    big = [FakeProc(1, "ollama", 900, cmdline=["ollama", "serve"],
                    children=[FakeProc(2, "ollama", 100, cmdline=["ollama", "runner"])])]
    small = [FakeProc(1, "ollama", 10, cmdline=["ollama", "serve"])]
    seq = iter([big, small])
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None, ad_value=None: next(seq))
    s = ab.ResourceSampler(gateway_pid=999)
    s.sample_once()
    s.sample_once()
    assert s.peak_ollama_rss == 1000
