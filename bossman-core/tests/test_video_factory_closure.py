"""Регрессии закрытия Video Factory (VF-001..VF-005).

СОЗНАТЕЛЬНО БЕЗ `skipif(not ffmpeg_available())`. Замер показал: без ffmpeg на
PATH пять тестов `test_video_factory.py` молча пропускаются, а раннер
`ubuntu-latest` ffmpeg НЕ ставит (его нет в манифесте образа) — то есть в CI
ровно эти проверки не исполнялись никогда. Скрыть это ещё одним skip-ом значит
превратить измеримую потерю покрытия в бесшумную; правильное лечение —
`apt-get install -y ffmpeg` в workflow. Поэтому файл падает громко, если
инструментов нет.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path

import pytest

from bossman import errors
from bossman.resource_brain import ResourceBrain, ResourceSnapshot
from bossman.video_factory import JobState, VideoFactory
from bossman.video_factory import ffmpeg as ffmod
from bossman.video_factory.ffmpeg import (
    ffmpeg_available,
    ffmpeg_bin,
    probe_media,
    run_testsrc,
    validate_video_output,
)
from bossman.video_factory.model import max_scene_duration_s, normalize_duration


# --- хелперы ----------------------------------------------------------------

def _generous_brain() -> ResourceBrain:
    brain = ResourceBrain(disk_reserve=0, max_ram_pressure=0.999)
    brain.set_snapshot(
        ResourceSnapshot(
            ram_total=10 ** 12, ram_available=10 ** 12,
            disk_total=10 ** 12, disk_free=10 ** 12,
        )
    )
    return brain


def _factory(root: Path, **kw) -> VideoFactory:
    kw.setdefault("brain", _generous_brain())
    kw.setdefault("est_ram", 1000)
    kw.setdefault("est_disk", 1000)
    return VideoFactory(root, **kw)


async def _make_streamed_mkv(dest: Path) -> None:
    """Настоящий воспроизводимый matroska, записанный в НЕперематываемый вывод.

    Ровно такой контейнер отдаёт облачный провайдер (и любой стример): видеопоток
    на месте, а метки длительности в заголовке нет, потому что муксер не мог
    вернуться и дописать её."""
    argv = [
        ffmpeg_bin(), "-nostdin", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=64x48:rate=10",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-f", "matroska", "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await proc.communicate()
    assert proc.returncode == 0 and out, "ffmpeg не смог собрать потоковый mkv"
    dest.write_bytes(out)


# --- канарейка окружения ----------------------------------------------------

def test_ffmpeg_toolchain_is_installed():
    """Инструменты рендера обязаны быть в окружении, где гоняется этот набор.

    Падение здесь означает не «тест плохой», а «в CI не установлен ffmpeg» —
    лечится строкой `sudo apt-get install -y ffmpeg` в шаге workflow, а не
    skip-ом: без бинаря пять проверок video_factory не исполняются вообще."""
    assert ffmpeg_available(), (
        "ffmpeg/ffprobe отсутствуют: пять тестов video_factory при этом молча "
        "пропускаются. Установите ffmpeg в окружении CI."
    )


# --- VF-001: валидный артефакт без метки длительности в контейнере ----------

async def test_streamed_container_without_metadata_duration_is_accepted(tmp_path: Path):
    """Валидное видео НЕ должно браковаться из-за отсутствия format.duration.

    Регрессия VF-001: `probe_media` читал только `format.duration`, для такого
    файла получал 0.0, и `validate_video_output` объявлял живой артефакт битым.
    Пайплайн сжигал на нём все попытки (для облачного провайдера — с оплатой
    каждой), а сцена падала с VIDEO_INVALID_OUTPUT."""
    mkv = tmp_path / "streamed.mkv"
    await _make_streamed_mkv(mkv)

    dur, has_v = await probe_media(mkv)
    assert has_v is True
    assert math.isfinite(dur) and dur > 0.0, f"длительность не восстановлена: {dur}"
    assert dur == pytest.approx(1.0, abs=0.35), f"длительность разъехалась: {dur}"

    # Валидация обязана ПРИНЯТЬ файл (раньше бросала VideoInvalidOutput).
    assert await validate_video_output(mkv) == (dur, True)


async def test_broken_outputs_are_still_rejected(tmp_path: Path):
    """Расширение источников длительности НЕ должно ослабить браковку.

    Пустой файл, обрезанный заголовок и просто текст обязаны остаться
    невалидными: иначе VF-001 «чинился» бы приёмом мусора."""
    from bossman.video_factory.ffmpeg import validate_video_output as v

    cases = {
        "empty.mp4": b"",
        "trunc.mp4": b"\x00\x00\x00\x18ftypmp42",
        "text.mp4": b"definitely not a container" * 100,
    }
    for name, blob in cases.items():
        p = tmp_path / name
        p.write_bytes(blob)
        with pytest.raises(errors.VideoInvalidOutput):
            await v(p)


async def test_probe_duration_is_always_finite(tmp_path: Path):
    """`probe_media` никогда не отдаёт NaN/inf.

    Регрессия VF-004: `float(...)` на поле ffprobe мог вернуть NaN, а
    `NaN <= 0.0` ложно — битый файл проходил валидацию как валидный."""
    real = tmp_path / "real.mp4"
    await run_testsrc(real, 1.0)
    for target in (real, tmp_path / "nope.mp4"):
        dur, _ = await probe_media(target)
        assert math.isfinite(dur), f"{target.name}: длительность не конечна"


# --- VF-002 / VF-003: типизированная причина вместо имени класса ------------

async def test_untyped_provider_error_ends_job_failed_with_reason_code(tmp_path: Path):
    """Неожиданное исключение провайдера обязано закончиться терминальным
    состоянием с машинным кодом.

    Регрессия VF-002: ловились только VideoProviderFailed/VideoInvalidOutput,
    поэтому RuntimeError улетал мимо цикла попыток и на диске навсегда оставалось
    `state: "running", error: null` — reconcile чинит такое только на рестарте
    процесса."""

    class Boom:
        async def generate(self, *, prompt, duration_s, output_dir):
            raise RuntimeError("codec segfault")

    v = _factory(tmp_path, provider=Boom(), max_attempts=2)
    job = v.create("t", ["p"])
    await v.run_job(job)

    on_disk = json.loads((tmp_path / job.id / "job.json").read_text(encoding="utf-8"))
    assert on_disk["state"] == JobState.FAILED.value
    assert on_disk["error"], "джоба провалилась без причины на уровне джобы"
    assert on_disk["error"].startswith(errors.ErrorCode.VIDEO_PROVIDER_FAILED.value)
    scene = on_disk["scenes"][0]
    assert scene["status"] == "failed"
    assert scene["attempts"] == 2, "попытки не расходовались — риск вечного цикла"
    assert scene["error_code"] == errors.ErrorCode.VIDEO_PROVIDER_FAILED.value
    assert "RuntimeError" in scene["error"], "исходная причина потеряна для разбора"


async def test_invalid_output_reason_code_is_distinguishable(tmp_path: Path):
    """Провал валидации отличим от провала провайдера ПО КОДУ, а не по тексту."""

    class WritesGarbage:
        async def generate(self, *, prompt, duration_s, output_dir):
            d = Path(output_dir)
            d.mkdir(parents=True, exist_ok=True)
            take = d / "take-001.mp4"
            take.write_bytes(b"")
            return str(take)

    v = _factory(tmp_path, provider=WritesGarbage(), max_attempts=1)
    job = v.create("t", ["p"])
    await v.run_job(job)

    reloaded = v.load(job.id)
    assert reloaded.state == JobState.FAILED
    assert reloaded.scenes[0].error_code == errors.ErrorCode.VIDEO_INVALID_OUTPUT.value
    assert reloaded.error and errors.ErrorCode.VIDEO_INVALID_OUTPUT.value in reloaded.error
    # Публичный ответ роутера несёт тот же код — владелец видит его без логов.
    assert reloaded.to_public()["scenes"][0]["error_code"] == (
        errors.ErrorCode.VIDEO_INVALID_OUTPUT.value
    )


async def test_attempts_advance_even_if_failure_precedes_the_counter(tmp_path: Path):
    """Провал ДО инкремента попытки не должен крутить цикл вечно."""

    class Stuck(VideoFactory):
        async def _generate_once(self, job, scene):  # noqa: ANN001
            raise OSError("scene dir unavailable")

    v = Stuck(tmp_path, brain=_generous_brain(), est_ram=1000, est_disk=1000, max_attempts=3)
    job = v.create("t", ["p"])
    await asyncio.wait_for(v.run_job(job), timeout=15)  # зависание = падение
    reloaded = v.load(job.id)
    assert reloaded.scenes[0].attempts == 3
    assert reloaded.state == JobState.FAILED


# --- VF-004: длительность не расходится и не ломает чекпоинт ----------------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 0.0, -5.0])
async def test_non_finite_or_nonpositive_duration_is_rejected(tmp_path: Path, bad: float):
    """Библиотечный вход обязан отказывать типизированно, а не молча чинить.

    Регрессия VF-004: `create()` принимал что угодно; NaN доезжал до модели, а
    рендерер тихо превращал его в 0.1 с (`max(0.1, nan)`) — модель, рендерер и
    UI говорили о разной длительности."""
    v = _factory(tmp_path)
    with pytest.raises(errors.ArtifactRejected):
        v.create("t", ["p"], duration_s=bad)
    with pytest.raises(errors.ArtifactRejected):
        normalize_duration(bad)


async def test_checkpoint_is_standards_valid_json(tmp_path: Path):
    """`job.json` обязан читаться СТРОГИМ парсером (браузер, jq, Go-клиент).

    Регрессия VF-004: `json.dump` по умолчанию пишет литералы `NaN`/`Infinity`,
    которых нет в JSON; такой durable-чекпоинт не читал никто, кроме Python."""
    v = _factory(tmp_path)
    job = v.create("t", ["p1", "p2"], duration_s=2.0)

    def _strict(constant: str):
        raise AssertionError(f"нестандартный литерал в job.json: {constant}")

    raw = (tmp_path / job.id / "job.json").read_text(encoding="utf-8")
    json.loads(raw, parse_constant=_strict)  # падает на NaN/Infinity/-Infinity

    # Отравлённая в памяти джоба не должна оказаться на диске битой.
    job.scenes[0].duration_s = float("nan")
    with pytest.raises(errors.ArtifactRejected):
        v.save(job)
    json.loads(
        (tmp_path / job.id / "job.json").read_text(encoding="utf-8"), parse_constant=_strict
    )
    assert not (tmp_path / job.id / "job.json.tmp").exists()


async def test_model_and_renderer_agree_on_duration(tmp_path: Path):
    """Длительность в модели и длительность настоящего артефакта совпадают."""
    v = _factory(tmp_path)
    job = v.create("t", ["p"], duration_s=1.5)
    await v.run_job(job)

    reloaded = v.load(job.id)
    assert reloaded.state == JobState.COMPLETE
    scene = reloaded.scenes[0]
    artifact = tmp_path / job.id / scene.id / scene.output
    measured, has_v = await probe_media(artifact)
    assert has_v is True
    assert measured == pytest.approx(scene.duration_s, abs=0.25), (
        f"модель обещала {scene.duration_s}s, артефакт содержит {measured}s"
    )


# --- VF-005: рендер ограничен сверху и по длительности, и по времени --------

async def test_oversized_duration_is_rejected_before_any_render(tmp_path: Path):
    """Гигантская длительность отклоняется, а не уходит в бесконечный ffmpeg.

    Регрессия VF-005: pydantic в routes.py ограничивал 120 с, но библиотечный
    вход (`create`/`create_and_enqueue`) — ничем; `duration_s=1e6` запускал
    рендер, который через 8 с всё ещё писал файл и держал воркера."""
    v = _factory(tmp_path)
    over = max_scene_duration_s() + 1.0
    with pytest.raises(errors.ArtifactRejected):
        v.create("t", ["p"], duration_s=1_000_000.0)
    with pytest.raises(errors.ArtifactRejected):
        v.create("t", ["p"], duration_s=over)
    assert not list(tmp_path.rglob("take-*")), "успел появиться артефакт"


async def test_hung_render_is_killed_and_reported_typed(tmp_path: Path, monkeypatch):
    """Зависший ffmpeg обязан быть убит по таймауту с типизированной причиной.

    Регрессия VF-005: ни один вызов ffmpeg/ffprobe в слое не имел таймаута —
    `await proc.communicate()` ждал вечно и навсегда занимал воркера (при том
    что `toolkit/media.py` рядом уже гонял ffmpeg с timeout=900)."""
    marker = tmp_path / "child-survived"
    fake = tmp_path / "hanging-ffmpeg"
    fake.write_text(f"#!/bin/sh\nsleep 3\ntouch {marker}\n", encoding="utf-8")
    fake.chmod(0o755)

    monkeypatch.setattr(ffmod, "ffmpeg_bin", lambda: str(fake))
    monkeypatch.setattr(ffmod, "_render_timeout_s", lambda _d: 0.5)

    started = time.monotonic()
    with pytest.raises(errors.VideoProviderFailed) as caught:
        await run_testsrc(tmp_path / "out.mp4", 1.0)
    elapsed = time.monotonic() - started

    assert elapsed < 4.0, f"таймаут не сработал: ждали {elapsed:.1f}s"
    assert "timed out" in caught.value.detail
    assert caught.value.code is errors.ErrorCode.VIDEO_PROVIDER_FAILED
    await asyncio.sleep(3.5)  # переживает ли процесс свою задачу
    assert not marker.exists(), "подпроцесс не убит — он пережил таймаут"


async def test_hung_probe_is_bounded(tmp_path: Path, monkeypatch):
    """Тот же потолок обязателен и для probe: валидация не висит вечно."""
    fake = tmp_path / "hanging-ffprobe"
    fake.write_text("#!/bin/sh\nsleep 3\n", encoding="utf-8")
    fake.chmod(0o755)

    real = tmp_path / "real.mp4"
    await run_testsrc(real, 1.0)

    monkeypatch.setattr(ffmod, "ffprobe_bin", lambda: str(fake))
    monkeypatch.setattr(ffmod, "_PROBE_TIMEOUT_S", 0.5)

    started = time.monotonic()
    with pytest.raises(errors.VideoProviderFailed):
        await probe_media(real)
    assert time.monotonic() - started < 4.0
