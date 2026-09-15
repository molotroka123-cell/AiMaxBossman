"""Превью обязано и играться в поставляемом браузере, и успевать к владельцу.

Два требования тянут в разные стороны, и их легко разменять друг на друга:

* контейнер и кодек должны быть теми, что умеет ПОСТАВЛЯЕМЫЙ Chromium —
  сборка без проприетарных кодеков, H.264/AAC в ней просто нет;
* кодировать при этом нельзя в режиме «максимальное качество любой ценой»,
  иначе превью готово тогда, когда его уже никто не ждёт.

Здесь нет абсолютного порога в секундах: он зависит от машины и на общем
раннере меряет чужую нагрузку. Вместо этого измеряется ОТНОШЕНИЕ двух
прогонов одного и того же проекта — быстрый режим обязан быть быстрее
умолчания. Такой замер нельзя пройти, просто удалив ключи.
"""
from __future__ import annotations

import shutil
import time

import pytest

from bcc.video_studio.render import render_project
from bcc.video_studio.media import binary, process, MediaLibrary

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                               reason="нужны настоящие двоичные файлы FFmpeg")


async def _fixture(tmp_path, duration=4):
    path = tmp_path / "src.mp4"
    await process([binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=25:duration={duration}",
                   "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)])
    media = await MediaLibrary(tmp_path).import_file(path)
    ticks = duration * 1_000_000
    return {"id": "p1", "revision": 1, "schema_version": 1, "timebase": 1_000_000,
            "active_sequence_id": "s1", "media": {media["id"]: media}, "captions": [],
            "sequences": [{"id": "s1", "width": 320, "height": 180, "fps": {"num": 25, "den": 1},
                           "sample_rate": 48000, "tracks": [{"id": "v1", "kind": "video", "clips": [
                               {"id": "c1", "media_id": media["id"], "start": 0, "source_in": 0,
                                "source_out": ticks, "speed": {"num": 1, "den": 1},
                                "transform": {}, "effects": [], "keyframes": {}}]}]}]}


# Ровно то, что запрашивает сервис для превью (bcc/video_studio/service.py).
PREVIEW = {"width": 320, "height": 180, "video_codec": "libvpx-vp9",
           "audio_codec": "libopus", "deadline": "realtime", "cpu_used": 8}


@pytest.mark.asyncio
async def test_preview_encodes_faster_than_the_quality_default(tmp_path):
    # Шесть секунд, а не одна: на коротком клипе постоянные расходы
    # (запуск ffmpeg, ffprobe-проверка) больше самого кодирования, и замер
    # мерил бы их, а не режим. Замер внутри конвейера, медиана из четырёх:
    # 1.45 с по умолчанию против 0.45 с — разница в 3.2 раза.
    project = await _fixture(tmp_path, duration=6)

    async def best_of_two(name, options):
        times = []
        for attempt in range(2):
            start = time.perf_counter()
            result = await render_project(project, tmp_path, tmp_path / f"{attempt}-{name}", options)
            assert result["verification"]["passed"], result["verification"]
            times.append(time.perf_counter() - start)
        return min(times)   # общий раннер шумит вверх, но не вниз

    quality = await best_of_two("quality.webm", {k: v for k, v in PREVIEW.items()
                                                 if k not in ("deadline", "cpu_used")})
    fast = await best_of_two("preview.webm", PREVIEW)
    # Запас намеренно грубый: доказывается, что режим ВЛИЯЕТ, а не конкретное
    # ускорение. Настоящий разрыв — 3.2 раза, порог — 20 %.
    assert fast < quality * 0.8, f"быстрый режим не быстрее: {fast:.2f} с против {quality:.2f} с"


@pytest.mark.asyncio
async def test_the_preview_profile_stays_playable_in_the_shipped_browser(tmp_path):
    project = await _fixture(tmp_path, duration=1)
    result = await render_project(project, tmp_path, tmp_path / "preview.webm", PREVIEW)
    profile = result["profile"]
    # H.264/AAC здесь были бы регрессией: поставляемый Chromium их не декодирует
    # и элемент <video> кончает MEDIA_ERR_SRC_NOT_SUPPORTED.
    assert profile["video_codec"] == "libvpx-vp9", profile
    assert profile["audio_codec"] == "libopus", profile
    assert result["verification"]["passed"] and result["verification"]["has_audio"]


@pytest.mark.asyncio
async def test_a_bogus_speed_setting_is_refused_rather_than_silently_clamped(tmp_path):
    # libvpx-vp9 молча обрезает cpu-used выше потолка режима, и тогда опечатка
    # выглядела бы как рабочая настройка. Потолок проверяется до ffmpeg.
    project = await _fixture(tmp_path, duration=1)
    with pytest.raises(ValueError):
        await render_project(project, tmp_path, tmp_path / "a.webm", {**PREVIEW, "deadline": "turbo"})
    with pytest.raises(ValueError):
        await render_project(project, tmp_path, tmp_path / "b.webm",
                             {**PREVIEW, "deadline": "good", "cpu_used": 8})
    # А в realtime тот же 8 законен — потолок зависит от режима, и проверка
    # обязана это различать, иначе она запрещает работающую настройку.
    result = await render_project(project, tmp_path, tmp_path / "c.webm", PREVIEW)
    assert result["verification"]["passed"]
