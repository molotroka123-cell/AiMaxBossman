"""VRAM: что именно считается и не устарело ли оно.

Оба теста написаны по дефектам, найденным в бою, а не по спецификации.
nvidia-smi здесь подделан — настоящей карты в CI нет, — но разбирают его вывод
те же функции, что пойдут в бой, и формат вывода взят из реального
`--format=csv,noheader,nounits`.
"""
from __future__ import annotations

import subprocess

import pytest

from bcc import metrics


GPU_CSV = ("GPU-aaa, NVIDIA GeForce RTX 4090, 37, 9216, 24564, 61\n"
           "GPU-bbb, NVIDIA GeForce RTX 3060, 0, 512, 12288, 40\n")
# used_gpu_memory приходит в МиБ; «[N/A]» бывает в WSL и в контейнере без --gpus
APPS_CSV = ("GPU-aaa, 4242, ollama.exe, 7680\n"
            "GPU-aaa, 999, chrome.exe, 1024\n"
            "GPU-bbb, 31337, python, [N/A]\n")


@pytest.fixture(autouse=True)
def _no_cache():
    metrics._gpu_cache = (0.0, None)
    yield
    metrics._gpu_cache = (0.0, None)


def _fake_smi(monkeypatch, gpu_csv=GPU_CSV, apps_csv=APPS_CSV):
    calls: list[list[str]] = []

    def run(cmd, **kw):
        calls.append(cmd)
        body = apps_csv if any("compute-apps" in a for a in cmd) else gpu_csv
        return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")

    monkeypatch.setattr(metrics.shutil, "which", lambda _: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(metrics.subprocess, "run", run)
    return calls


def test_vram_of_the_whole_card_is_not_reported_as_the_model(monkeypatch):
    """Дефект: «занято VRAM» показывало всю карту — вместе с браузером.

    9 ГиБ на карте, но наши вычислительные процессы держат 8.5: списывать
    разницу на модель нельзя, поэтому цифры разделены.
    """
    _fake_smi(monkeypatch)
    gpus = metrics._nvidia()
    assert gpus is not None and len(gpus) == 2

    card = gpus[0]
    assert card["vram_used_mb"] == 9216          # вся карта
    assert card["vram_procs_mb"] == 8704         # 7680 ollama + 1024 chrome
    assert card["vram_free_mb"] == 24564 - 9216
    assert [p["name"] for p in card["procs"]] == ["ollama.exe", "chrome.exe"]
    assert card["procs"][0]["vram_used_mb"] == 7680     # самый прожорливый первым


def test_na_from_wsl_is_dropped_not_counted_as_zero(monkeypatch):
    """«[N/A]» — это «не измерено», а не «ноль»: сумма не должна врать."""
    _fake_smi(monkeypatch)
    second = metrics._nvidia()[1]
    assert second["procs"] == []
    assert second["vram_procs_mb"] == 0.0


def test_gpu_cache_is_shorter_than_the_sampling_step():
    """Дефект: кэш 30 с при шаге сэмплирования 10 с.

    Два сэмпла из трёх писали в БД одно и то же старое значение VRAM — график
    становился лестницей, а разница «до/после прогона» теряла смысл.
    """
    assert metrics.GPU_CACHE_TTL < metrics.SAMPLE_SECONDS


def test_cache_still_absorbs_a_burst_of_requests(monkeypatch):
    """Но подряд идущие HTTP-запросы не должны плодить процессы nvidia-smi."""
    calls = _fake_smi(monkeypatch)
    metrics.gpu_info()
    spawned = len(calls)
    for _ in range(5):
        metrics.gpu_info()
    assert len(calls) == spawned, "кэш перестал работать — nvidia-smi на каждый запрос"


def test_missing_nvidia_smi_falls_through_quietly(monkeypatch):
    monkeypatch.setattr(metrics.shutil, "which", lambda _: None)
    assert metrics._nvidia() is None
