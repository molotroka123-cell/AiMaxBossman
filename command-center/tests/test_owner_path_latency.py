"""Два наблюдения, которые стоили владельцу сотню миллисекунд каждое.

Измерено на живом приложении, а не выведено из кода: медиана по маршрутам
`/api/*` показала четыре ручки дороже 100 мс, и три из них тратили время не на
работу, а на ожидание.

  * `/api/video-studio/capabilities` — 131 мс: ffmpeg просили перечислить ВСЕ
    фильтры и ВСЕ кодировщики на каждый запрос. Сборка ffmpeg между двумя
    запросами не меняется;
  * `/api/reality/observe` и `/api/reality/world` — по 115 мс: `cpu_percent`
    вызывался с `interval=0.1`, то есть честно спал десятую долю секунды.

Эти тесты держат обе починки. Они проверяют не «быстро», а ОТСУТСТВИЕ
конкретной причины медленности: кэш с ключом от самого двоичного файла и
неблокирующий счётчик. Порог в миллисекундах на общей машине сборки мерил бы
загрузку сборщика, а не наш код.
"""
from __future__ import annotations

import time

import pytest

from bcc.reality import observers
from bcc.video_studio import media


def test_the_ffmpeg_build_is_probed_once_not_per_request(monkeypatch):
    calls = {"n": 0}
    real_run = media.__dict__.get("subprocess")

    import subprocess as _sp
    original = _sp.run

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    media._BUILD_PROBE.clear()
    monkeypatch.setattr(_sp, "run", counting)
    first = media.capabilities()
    after_first = calls["n"]
    second = media.capabilities()

    assert calls["n"] == after_first, "вторая выдача снова спрашивала ffmpeg"
    assert first["filters"] == second["filters"]
    assert first["encoders"] == second["encoders"]


def test_a_changed_ffmpeg_binary_invalidates_the_probe(tmp_path, monkeypatch):
    """Кэш с ключом от файла: поставили другой ffmpeg — узнали об этом."""
    media._BUILD_PROBE.clear()
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    first_key = media._probe_key(str(fake))
    time.sleep(0.01)
    fake.write_text("#!/bin/sh\necho other\nexit 0\n", encoding="utf-8")
    assert media._probe_key(str(fake)) != first_key


def test_a_missing_ffmpeg_is_not_cached_as_a_build():
    media._BUILD_PROBE.clear()
    probed = media._probe_build(None)
    assert probed == {"filters": [], "encoders": []}


def test_the_cpu_counter_does_not_sleep():
    """`interval=0.1` спит; `interval=None` считает с прошлого вызова."""
    import inspect
    source = inspect.getsource(observers.observe_process)
    assert "cpu_percent(interval=None)" in source
    assert "interval=0.1" not in source


def test_the_first_cpu_reading_is_absent_rather_than_zero():
    """Ноль означал бы «простаивает». Сравнивать ещё не с чем — так и сказано."""
    observers._CPU_PRIMED = False
    first = observers.observe_process()
    if not first.available:
        pytest.skip(first.reason)
    assert "cpu_percent" not in first.value
    assert first.value["cpu_percent_reason"]

    second = observers.observe_process()
    assert "cpu_percent" in second.value


def test_memory_is_measured_on_the_very_first_observation():
    """Загрузка процессора ждёт второго вызова, память — нет.

    От памяти зависит отказ по ресурсам, и «не измерено» на первом наблюдении
    закрыло бы локальную модель на ровном месте.
    """
    observers._CPU_PRIMED = False
    first = observers.observe_process()
    if not first.available:
        pytest.skip(first.reason)
    assert isinstance(first.value.get("ram_available_mb"), int)
    assert first.value["ram_available_mb"] > 0


def test_repeated_observation_is_cheap():
    """Грубая верхняя граница, а не измерение: сон в сто миллисекунд её
    пробивает, а разброс загруженного сборщика — нет."""
    observers.observe_process()                   # прогрев счётчика
    started = time.perf_counter()
    for _ in range(5):
        observers.observe_process()
    elapsed = time.perf_counter() - started
    assert elapsed < 0.25, f"пять наблюдений заняли {elapsed:.3f} с"
