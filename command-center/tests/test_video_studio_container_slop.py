"""Квантование длительности контейнера против настоящего выхода за пределы.

Живой прогон владельца (FFmpeg 8.1, Windows) отклонял корректный проект:

    test_rational_range_keeps_selected_final_source_frame[2-4]
    StudioError: Clip extends beyond source duration

Причина не в монтаже. ffprobe отдаёт длительность КОНТЕЙНЕРА, а не сумму
закодированных кадров, и для 30000/1001 последний законно выбранный кадр
ложится ровно на границу: source_out=133467 при duration_ticks=133467
(измерено на FFmpeg 6.1.1). Сборка, округлившая длительность на один тик
вниз, — и валидатор с нулевым допуском отклоняет файл, в котором этот кадр
физически есть.

Здесь допуск проверяется без FFmpeg: числа настоящие, поведение детерминированное,
тест идёт на любой машине. Реальную сквозную проверку кадров делает
test_video_studio_cfr_frames.py, который требует настоящие бинарники.
"""
import pytest

from bcc.video_studio.model import (TICKS, StudioError, container_slop_ticks, frame_ticks,
                                    new_clip, new_project, validate_project)

NTSC = {"num": 30000, "den": 1001}
ONE_FRAME = 33367                      # ceil(1_000_000 * 1001 / 30000)


def project(duration_ticks, source_out, *, media_fps=NTSC, seq_fps=NTSC):
    p = new_project("cfr-project", "CFR endpoints")
    seq = p["sequences"][0]
    seq.update(width=160, height=90, fps=seq_fps)
    p["media"] = {"m": {"id": "m", "relative_path": "m.mp4", "duration_ticks": duration_ticks,
                        "fps": media_fps, "has_video": True, "has_audio": True}}
    seq["tracks"][0]["clips"] = [new_clip({"id": "c", "media_id": "m", "source_out": source_out})]
    return p


def test_one_frame_is_the_declared_bound():
    assert container_slop_ticks({"fps": NTSC}) == ONE_FRAME
    assert container_slop_ticks({"fps": {"num": 25, "den": 1}}) == TICKS // 25


@pytest.mark.parametrize("frames", [1, 2, 3, 4, 7, 15, 25, 30])
def test_the_final_selected_frame_survives_a_short_container_duration(frames):
    """Каждый кадр контейнера, укоротившегося не более чем на кадр, остаётся выбираемым."""
    source_out = frame_ticks(frames, NTSC)
    for short_by in (0, 1, ONE_FRAME // 2, ONE_FRAME):
        probed = source_out - short_by
        if probed <= 0:
            continue
        validate_project(project(probed, source_out))      # не должно бросать


def test_genuinely_out_of_range_is_still_rejected():
    """Отрицательный контроль: за границей допуска — по-прежнему отказ."""
    source_out = frame_ticks(4, NTSC)
    with pytest.raises(StudioError, match="beyond source duration"):
        validate_project(project(source_out - ONE_FRAME - 1, source_out))
    with pytest.raises(StudioError, match="beyond source duration"):
        validate_project(project(source_out - 2 * ONE_FRAME, source_out))
    # Клип, требующий вдвое больше материала, чем есть, — не «квантование».
    with pytest.raises(StudioError, match="beyond source duration"):
        validate_project(project(source_out, source_out * 2))


def test_media_without_a_known_frame_rate_gets_no_slop():
    """Без частоты кадров «один кадр» неопределён: допуска нет, старое поведение."""
    source_out = frame_ticks(4, NTSC)
    for bad in ({"num": 0, "den": 1}, {"num": 30000, "den": 0}, {}, None):
        assert container_slop_ticks({"fps": bad}) == 0
        with pytest.raises(StudioError, match="beyond source duration"):
            validate_project(project(source_out - 1, source_out, media_fps=bad))


def test_slop_is_measured_on_the_media_rate_not_the_sequence_rate():
    """Допуск описывает исходник. Секвенция на 25 fps не расширяет его до 40 мс."""
    source_out = frame_ticks(4, NTSC)
    assert container_slop_ticks({"fps": NTSC}) < TICKS // 25
    validate_project(project(source_out - ONE_FRAME, source_out, seq_fps={"num": 25, "den": 1}))
    with pytest.raises(StudioError, match="beyond source duration"):
        validate_project(project(source_out - TICKS // 25, source_out, seq_fps={"num": 25, "den": 1}))


def test_zero_duration_media_is_untouched():
    """duration_ticks==0 означает «длительность неизвестна» и проверку не проходит вовсе."""
    validate_project(project(0, frame_ticks(4, NTSC)))
