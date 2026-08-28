"""Проба медиа — и её честное отсутствие.

ffprobe и ffmpeg внешние, и в этой среде их может не быть. Тесты написаны так,
чтобы работать в обоих случаях, но **не одинаково**:

* с настоящим ffprobe — настоящая проба настоящего файла;
* без него — `skip` с причиной, в которой сказано, чего именно не проверено.

Пропуск здесь не поражение и не формальность: он ровно то, что должно попасть в
отчёт строкой `NOT RUN`. Подделывать пробу, чтобы «тесты были зелёные», значит
получить зелёный прогон и неизвестное состояние видеоконвейера.

Заголовочный разбор изображений проверяется всегда: это наш собственный код,
читающий настоящие байты, а не заменитель ffprobe.
"""
from __future__ import annotations

import pytest

from social_farm.media.asset import AssetType
from social_farm.media.probe import (CorruptMedia, ProbeUnavailable, ffmpeg_available,
                                     ffprobe_available, probe, probe_image_header,
                                     probe_with_ffprobe, toolchain_status)

from conftest import make_gif, make_png, truncate

needs_ffprobe = pytest.mark.skipif(
    not ffprobe_available(),
    reason="NOT RUN: в системе нет ffprobe — настоящая проба видео и звука "
           "не выполнялась. Это блокер среды, а не отказ кода.")

needs_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available(),
    reason="NOT RUN: в системе нет ffmpeg — перекодирование не выполнялось.")


# --------------------------------------------------- заголовочный разбор

def test_png_dimensions_are_read_from_the_file(tmp_path):
    """Числа берутся из IHDR, а не из имени файла и не из предположения."""
    target = tmp_path / "a.png"
    target.write_bytes(make_png(640, 480))
    result = probe_image_header(target)
    assert (result.width, result.height) == (640, 480)
    assert result.type is AssetType.IMAGE
    assert result.mime == "image/png"
    assert result.prober == "header", "прибор обязан называть себя"


def test_gif_dimensions_are_read_from_the_screen_descriptor(tmp_path):
    target = tmp_path / "a.gif"
    target.write_bytes(make_gif(120, 90))
    result = probe_image_header(target)
    assert (result.width, result.height) == (120, 90)
    assert result.mime == "image/gif"


def test_a_truncated_png_is_corrupt_not_merely_unmeasured(tmp_path):
    """Обрезанная загрузка ловится и без ffprobe.

    Заголовок у такого файла целый, размеры читаются — и именно поэтому
    проверка идёт дальше, по цепочке чанков до IEND. Иначе битый файл ушёл бы
    в публикацию с правдоподобными размерами.
    """
    target = tmp_path / "broken.png"
    target.write_bytes(truncate(make_png(800, 800, noisy=True)))
    with pytest.raises(CorruptMedia, match="IEND|обрыв"):
        probe_image_header(target)


def test_a_truncated_gif_is_corrupt(tmp_path):
    target = tmp_path / "broken.gif"
    target.write_bytes(make_gif(100, 100)[:-1])          # съеден терминатор
    with pytest.raises(CorruptMedia):
        probe_image_header(target)


def test_a_png_header_on_garbage_is_corrupt(tmp_path):
    target = tmp_path / "lying.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    with pytest.raises(CorruptMedia):
        probe_image_header(target)


def test_an_unknown_format_says_not_supported_rather_than_guessing(tmp_path):
    """Незнакомый формат — `NOT_SUPPORTED`, а не «наверное, картинка»."""
    target = tmp_path / "thing.bin"
    target.write_bytes(b"\x00\x01\x02\x03" * 100)
    with pytest.raises(ProbeUnavailable, match="NOT_SUPPORTED"):
        probe_image_header(target)


def test_the_header_prober_does_not_pretend_to_know_about_video(tmp_path):
    """Он не выдумывает ни кодека видео, ни звука, ни битрейта."""
    target = tmp_path / "a.png"
    target.write_bytes(make_png(100, 100))
    result = probe_image_header(target)
    assert result.duration_ms is None
    assert result.audio_codec is None
    assert result.bitrate_bps is None


# --------------------------------------------------- поведение без ffprobe

def test_video_without_ffprobe_is_not_supported_rather_than_assumed(tmp_path):
    """Ключевой тест требования: видео без ffprobe НЕ измеряется.

    Файл заведомо не картинка. Без ffprobe единственный допустимый ответ —
    отказ измерить; вернуть правдоподобные «mp4/h264» было бы враньём, на
    котором построилась бы публикация.
    """
    if ffprobe_available():
        pytest.skip("ffprobe есть — этот путь проверяется только без него")
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512)
    with pytest.raises(ProbeUnavailable, match="NOT_SUPPORTED"):
        probe(target)


def test_probe_with_ffprobe_refuses_to_run_when_ffprobe_is_missing(tmp_path):
    if ffprobe_available():
        pytest.skip("ffprobe есть — этот путь проверяется только без него")
    target = tmp_path / "a.png"
    target.write_bytes(make_png(10, 10))
    with pytest.raises(ProbeUnavailable, match="ffprobe"):
        probe_with_ffprobe(target)


def test_toolchain_status_reports_the_truth_about_this_machine():
    """Диагностика не приукрашивает: по ней пишется строка отчёта."""
    status = toolchain_status()
    assert status["video_supported"] is ffprobe_available()
    assert status["transform_supported"] is (ffprobe_available() and ffmpeg_available())
    assert status["image_probe"] in ("header", "ffprobe")


# --------------------------------------------------- настоящая проба

@needs_ffprobe
def test_real_ffprobe_measures_a_real_png(tmp_path):
    """Настоящий прибор на настоящем файле — там, где он есть."""
    target = tmp_path / "a.png"
    target.write_bytes(make_png(321, 123))
    result = probe_with_ffprobe(target)
    assert (result.width, result.height) == (321, 123)
    assert result.type is AssetType.IMAGE
    assert result.prober == "ffprobe"


@needs_ffprobe
def test_real_ffprobe_agrees_with_the_header_prober(tmp_path):
    """Два независимых прибора обязаны сойтись — иначе один из них врёт."""
    target = tmp_path / "a.png"
    target.write_bytes(make_png(777, 555))
    by_ffprobe, by_header = probe_with_ffprobe(target), probe_image_header(target)
    assert (by_ffprobe.width, by_ffprobe.height) == (by_header.width, by_header.height)
    assert by_ffprobe.type is by_header.type


@needs_ffprobe
def test_ffprobe_alone_does_not_notice_a_truncated_image(tmp_path):
    """Найдено настоящим ffprobe, а не предположено.

    С `-show_format -show_streams` ffprobe читает заголовок и не декодирует
    данные: у обрезанного PNG есть IHDR, и этого ему достаточно. Тест
    фиксирует ограничение инструмента, чтобы никто не «починил» его удалением
    структурной проверки как лишней.
    """
    target = tmp_path / "broken.png"
    target.write_bytes(truncate(make_png(600, 600, noisy=True), keep=0.3))
    result = probe_with_ffprobe(target)          # не бросает — и это правда о ffprobe
    assert result.width == 600


@needs_ffprobe
def test_the_probe_entry_point_still_catches_a_truncated_image(tmp_path):
    """Поэтому целостность проверяется строжайшим доступным способом.

    `probe()` прогоняет структурную проверку и при наличии ffprobe — именно
    она ловит обрыв, который ffprobe пропускает.
    """
    target = tmp_path / "broken.png"
    target.write_bytes(truncate(make_png(600, 600, noisy=True), keep=0.3))
    with pytest.raises(CorruptMedia):
        probe(target)


@needs_ffmpeg
@needs_ffprobe
def test_real_video_roundtrip(tmp_path):
    """Настоящее видео: сделать ffmpeg'ом, измерить ffprobe'ом.

    Единственный тест, где появляются кодек и длительность, измеренные
    по-настоящему. Без ffmpeg он помечается NOT RUN — и это попадает в отчёт.
    """
    import subprocess
    target = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc=size=540x960:rate=25:duration=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)],
        check=True, capture_output=True, timeout=120)
    result = probe_with_ffprobe(target)
    assert result.type is AssetType.VIDEO
    assert result.codec == "h264"
    assert (result.width, result.height) == (540, 960)
    assert 3_500 <= result.duration_ms <= 4_500
