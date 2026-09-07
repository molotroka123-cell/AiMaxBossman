"""Выбор контейнера/кодеков для preview — в ПРОДУКТЕ, а не в тесте.

Кнопка «Создать preview» вызывает `startExport(true)` без имени контейнера.
Пока умолчанием был жёсткий mp4, владелец на браузере без проприетарных
декодеров получал файл с HTTP 200, валидный по ffprobe и полностью
декодируемый ffmpeg'ом, и `MediaError code 4
DEMUXER_ERROR_NO_SUPPORTED_STREAMS` в `<video>`. Здесь проверяется именно
продуктовое решение: таблица форматов и чистая функция `previewFormat` из
`ui/pages/video_studio.js`, а также то, что ни один предлагаемый ею вариант не
будет отвергнут сервером.

К каждому послаблению — негативный контроль: «webm вместо mp4» не должно
означать «теперь берём что попало».
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from bcc.video_studio.service import _check_container_codecs

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "command-center" / "ui" / "pages" / "video_studio.js"
RENDER = ROOT / "command-center" / "bcc" / "video_studio" / "render.py"

MP4 = 'video/mp4; codecs="avc1.64001E, mp4a.40.2"'
WEBM = 'video/webm; codecs="vp9, opus"'
# Измерено на этом хосте (HeadlessChrome/141, X11 Linux x86_64), а не взято из
# общих соображений: mp4 с H.264/AAC -> '', webm с VP9/Opus -> 'probably'.
CHROMIUM_NO_PROPRIETARY = {MP4: "", WEBM: "probably"}
FULL_DECODERS = {MP4: "probably", WEBM: "probably"}
HOST_ENCODERS = ["libx264", "aac", "libvpx", "libopus"]


def _node() -> str:
    node = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: production JS preview-format contracts were not executed")
    return node


def choose(tmp_path, support, encoders):
    """Запустить НАСТОЯЩУЮ функцию продукта, а не её пересказ."""
    driver = tmp_path / "choose.mjs"
    driver.write_text(
        "import { previewFormat, hasEncoder, PREVIEW_FORMATS } from "
        f"{json.dumps(MODULE.as_uri())};\n"
        "const support = JSON.parse(process.env.SUPPORT);\n"
        "const encoders = JSON.parse(process.env.ENCODERS);\n"
        "const answer = mime => (mime in support ? support[mime] : '');\n"
        "process.stdout.write(JSON.stringify({\n"
        "  chosen: previewFormat(answer, encoders),\n"
        "  formats: PREVIEW_FORMATS,\n"
        "  encoder_checks: PREVIEW_FORMATS.map(f => [f.video_codec,\n"
        "    hasEncoder(encoders, f.video_codec), f.audio_codec, hasEncoder(encoders, f.audio_codec)]),\n"
        "}));\n", encoding="utf-8")
    run = subprocess.run([_node(), str(driver)], capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, SUPPORT=json.dumps(support),
                                  ENCODERS=json.dumps(encoders)), check=False)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)


def test_browser_with_proprietary_decoders_still_gets_the_unchanged_mp4_default(tmp_path):
    """Умолчание не изменилось там, где оно работало.

    Chrome/Edge у владельца декодируют H.264/AAC — и должны получать ровно тот
    же mp4, что и до правки. «Починили webm» не должно означать «всем webm».
    """
    chosen = choose(tmp_path, FULL_DECODERS, HOST_ENCODERS)["chosen"]
    assert chosen["container"] == "mp4"
    assert chosen["video_codec"] == "libx264" and chosen["audio_codec"] == "aac"


def test_browser_without_h264_gets_a_container_it_actually_decodes(tmp_path):
    chosen = choose(tmp_path, CHROMIUM_NO_PROPRIETARY, HOST_ENCODERS)["chosen"]
    assert chosen["container"] == "webm"
    assert chosen["video_codec"] == "libvpx-vp9" and chosen["audio_codec"] == "libopus"


def test_negative_control_browser_that_decodes_nothing_gets_no_invented_format(tmp_path):
    """Негативный контроль: нечего пообещать — не обещаем.

    Возврат null оставляет прежнее умолчание и настоящий MediaError в панели;
    выдумать «какой-нибудь» контейнер значило бы снова спрятать причину.
    """
    assert choose(tmp_path, {MP4: "", WEBM: ""}, HOST_ENCODERS)["chosen"] is None


def test_negative_control_host_without_a_vp9_encoder_is_not_promised_webm(tmp_path):
    """Негативный контроль: браузер хочет webm, но собрать его нечем.

    Отсутствие кодировщика — не повод ставить в очередь заведомо падающую
    задачу. Выбор пуст, и это честнее «выбранного» формата, который не
    соберётся.
    """
    result = choose(tmp_path, CHROMIUM_NO_PROPRIETARY, ["libx264", "aac"])
    assert result["chosen"] is None
    assert result["encoder_checks"] == [["libx264", True, "aac", True],
                                        ["libvpx-vp9", False, "libopus", False]]


def test_probably_beats_maybe_on_every_ordering(tmp_path):
    """Свойство, а не повтор реализации.

    `canPlayType` отвечает '' | 'maybe' | 'probably'. 'maybe' значит «контейнер
    знаком, про кодеки ничего не обещаю» — именно на нём и получался таймаут.
    Поэтому обещанный формат обязан побеждать предположительный, в какую бы
    сторону ни легли ответы браузера.
    """
    for probably, maybe in ((MP4, WEBM), (WEBM, MP4)):
        chosen = choose(tmp_path, {probably: "probably", maybe: "maybe"}, HOST_ENCODERS)["chosen"]
        assert chosen is not None and chosen["mime"] == probably
    # 'maybe' используется только когда обещанного нет вовсе.
    chosen = choose(tmp_path, {MP4: "", WEBM: "maybe"}, HOST_ENCODERS)["chosen"]
    assert chosen is not None and chosen["container"] == "webm"


def test_unknown_encoder_list_is_not_read_as_proof_of_absence(tmp_path):
    """Пустой ответ `/capabilities` — это «неизвестно», а не «кодировщика нет».

    Список кодировщиков хоста разбирается по `\\w+` и ОБРЕЗАЕТ дефисные имена:
    на этом хосте `libvpx-vp9` приезжает как `libvpx` (проверено запуском
    `ffmpeg -encoders`, не додумано). Отсюда две проверки сразу: обрезанное имя
    принимается, а отсутствие списка не блокирует выбор.
    """
    for encoders in ([], None):
        chosen = choose(tmp_path, CHROMIUM_NO_PROPRIETARY, encoders)["chosen"]
        assert chosen is not None and chosen["container"] == "webm"
    assert choose(tmp_path, CHROMIUM_NO_PROPRIETARY, HOST_ENCODERS)["chosen"]["container"] == "webm"


def _product_formats():
    """Таблица форматов ЧИТАЕТСЯ из продукта, а не переписывается сюда."""
    source = MODULE.read_text(encoding="utf-8")
    table = re.search(r"export const PREVIEW_FORMATS = \[(.*?)\n\];", source, re.S)
    assert table, "PREVIEW_FORMATS исчез из ui/pages/video_studio.js"
    formats = [dict(re.findall(r"(\w+): '([^']*)'", line))
               for line in table.group(1).splitlines() if "container:" in line]
    assert formats, table.group(1)
    return formats


def _render_allowlists():
    source = RENDER.read_text(encoding="utf-8")
    video = re.search(r"if codec not in \{([^}]*)\}", source)
    audio = re.search(r"if audio_codec not in \{([^}]*)\}", source)
    assert video and audio, "изменилась форма проверки кодеков в render.py"
    return set(re.findall(r'"([^"]+)"', video.group(1))), set(re.findall(r'"([^"]+)"', audio.group(1)))


def test_every_format_the_ui_can_choose_survives_the_server_checks():
    """Свойство через границу языков: UI не предлагает того, что сервер отвергнет.

    Это ловит настоящий класс дефекта — тихо разъехавшиеся таблицы, из-за
    которых задача уходит в очередь и падает уже внутри ffmpeg.
    """
    allowed_video, allowed_audio = _render_allowlists()
    formats = _product_formats()
    assert {f["container"] for f in formats} >= {"mp4", "webm"}
    for fmt in formats:
        options = {"video_codec": fmt["video_codec"], "audio_codec": fmt["audio_codec"]}
        _check_container_codecs(fmt["container"], options)  # не должно бросить
        assert fmt["video_codec"] in allowed_video, fmt
        assert fmt["audio_codec"] in allowed_audio, fmt
        assert fmt["container"] in ("mp4", "mov", "mkv", "webm"), fmt


def test_negative_control_impossible_pairs_are_still_refused_before_queueing():
    """Негативный контроль к тому же гейту: он не стал пропускать всё подряд."""
    for options in ({"video_codec": "libx264", "audio_codec": "libopus"},
                    {"video_codec": "libvpx-vp9", "audio_codec": "aac"},
                    {}):  # умолчания libx264/aac в webm тоже невозможны
        with pytest.raises(ValueError):
            _check_container_codecs("webm", options)
    # ...а mp4 по-прежнему принимает свою историческую пару без исключений.
    _check_container_codecs("mp4", {"video_codec": "libx264", "audio_codec": "aac"})


def test_request_naming_no_container_still_defaults_to_mp4():
    """Контракт API не изменён: умолчание для вызывающего без контейнера — mp4.

    Формат подбирает страница, называя контейнер явно; сам эндпоинт остаётся
    прежним для агентов и скриптов, которые контейнер не называют.
    """
    from bcc.features.video_studio import Export

    body = Export(project_id="p", expected_revision=0, operation_id="op", preview=True)
    assert body.container == "mp4"
