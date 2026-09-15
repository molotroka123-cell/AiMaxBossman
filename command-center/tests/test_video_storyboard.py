"""Раздел 10: раскадровка и рецепты кадров.

Проверяется не «функция что-то вернула», а три свойства, ради которых слой и
писался:

* он НЕ придумывает материал, которого нет, и говорит об этом вслух;
* его вывод — команды СУЩЕСТВУЮЩЕГО словаря, и таймлайн их действительно
  принимает (проверка идёт настоящим `apply_command`, а не сверкой словарей);
* ритм притягивает склейки, но не имеет права выкинуть кадр за границы рецепта.
"""
from __future__ import annotations

import pytest

from bcc.video_studio.commands import apply_command
from bcc.video_studio.model import TICKS, StudioError, new_project, sequence_duration
from bcc.video_studio import storyboard as sb


def _project_with_media(count: int):
    project = new_project("p1", "Ролик")
    seq = project["sequences"][0]
    track = seq["tracks"][0]
    ids = []
    for i in range(count):
        mid = f"m{i}"
        project["media"][mid] = {"id": mid, "duration_ticks": 30 * TICKS,
                                 "kind": "video", "relative_path": f"{mid}.mp4"}
        ids.append(mid)
    return project, track["id"], ids


# --------------------------------------------------------------------------
# План
# --------------------------------------------------------------------------


def test_a_plan_fits_the_target_duration_and_keeps_shot_order():
    _, _, media = _project_with_media(4)
    board = sb.plan_storyboard("реклама Fresh Vibes", media_ids=media,
                               target_ticks=15 * TICKS)
    assert [s.recipe_id for s in board.shots] == list(sb.DEFAULT_ORDER)
    starts = [s.start for s in board.shots]
    assert starts == sorted(starts), "кадры обязаны идти подряд, без наложений"
    for a, b in zip(board.shots, board.shots[1:]):
        assert a.end == b.start, "между кадрами не должно быть дыр"
    assert 0 < board.duration <= 20 * TICKS


def test_missing_footage_shrinks_the_plan_and_is_said_out_loud():
    """Главное свойство: нехватка материала НЕ восполняется выдумкой.

    Задание запрещает изготавливать успех. Здесь это означает: кадров ровно
    столько, на сколько хватило материала, и каждая пропажа названа.
    """
    _, _, media = _project_with_media(1)
    board = sb.plan_storyboard("ролик", media_ids=media, target_ticks=15 * TICKS)
    with_media = [s for s in board.shots if s.media_id is not None]
    assert len(with_media) == 1, "второй кадр взять было не из чего"
    assert board.shortfalls, "молчаливое сокращение плана — это враньё по умолчанию"
    assert any("материал" in line for line in board.shortfalls)


def test_a_plan_never_reuses_one_clip_for_two_shots():
    """Один и тот же файл, поставленный дважды, выглядел бы как богатый монтаж."""
    _, _, media = _project_with_media(3)
    board = sb.plan_storyboard("ролик", media_ids=media, target_ticks=12 * TICKS)
    used = [s.media_id for s in board.shots if s.media_id]
    assert len(used) == len(set(used))


def test_an_empty_brief_is_refused():
    with pytest.raises(StudioError):
        sb.plan_storyboard("   ", media_ids=["m0"])


def test_an_unknown_recipe_is_refused_by_name():
    with pytest.raises(StudioError) as err:
        sb.plan_storyboard("ролик", media_ids=["m0"], shots=("hook", "нет-такого"))
    assert "нет-такого" in str(err.value)


# --------------------------------------------------------------------------
# Ритм
# --------------------------------------------------------------------------


def test_without_a_tempo_nothing_is_snapped_and_the_plan_says_so():
    _, _, media = _project_with_media(4)
    board = sb.plan_storyboard("ролик", media_ids=media, target_ticks=15 * TICKS)
    assert board.beat_ticks == 0
    assert "без притягивания" in sb.describe(board)


def test_a_tempo_snaps_cut_points_to_beats():
    _, _, media = _project_with_media(4)
    beat = sb.beat_ticks(120)                      # 0.5 с
    board = sb.plan_storyboard("ролик", media_ids=media, target_ticks=15 * TICKS, bpm=120)
    assert board.beat_ticks == beat
    snapped = [s.duration % beat == 0 for s in board.shots]
    assert sum(snapped) >= len(board.shots) - 1, \
        "притягивание должно быть видно в длительностях, а не только в поле"


def test_rhythm_never_pushes_a_shot_outside_its_own_recipe():
    """Обратный контроль: ритм — украшение, границы рецепта — смысл.

    Медленный темп (один удар в 4 с) при притягивании «как получится» растянул
    бы зацепку далеко за её максимум в 3 с.
    """
    _, _, media = _project_with_media(4)
    board = sb.plan_storyboard("ролик", media_ids=media, target_ticks=15 * TICKS, bpm=15)
    for shot in board.shots:
        recipe = sb.RECIPES[shot.recipe_id]
        assert recipe.min_ticks <= shot.duration <= recipe.max_ticks, shot.recipe_id


def test_an_absurd_tempo_is_refused_rather_than_clamped():
    with pytest.raises(StudioError):
        sb.beat_ticks(100000)


# --------------------------------------------------------------------------
# Компиляция в существующий словарь
# --------------------------------------------------------------------------


def test_the_plan_compiles_into_commands_the_real_timeline_accepts():
    """Самая важная проверка файла.

    Раскадровка ценна ровно настолько, насколько её принимает НАСТОЯЩИЙ
    таймлайн. Поэтому команды подаются в `apply_command`, а не сверяются с
    ожидаемым словарём: сверка словарей прошла бы и для команд, которые
    таймлайн отвергает.
    """
    project, track_id, media = _project_with_media(4)
    board = sb.plan_storyboard("реклама Fresh Vibes", media_ids=media,
                               target_ticks=15 * TICKS)
    commands = sb.compile_to_commands(board, track_id=track_id)
    assert commands, "пустая компиляция не является планом"
    # `apply_command` ЧИСТАЯ: она возвращает новый проект, а не правит текущий.
    # Написанный «на мутацию» цикл проходил бы первую команду и терял вторую —
    # так этот тест и упал в первый раз.
    for command in commands:
        project, _changed, _ = apply_command(project, command)

    seq = project["sequences"][0]
    clips = seq["tracks"][0]["clips"]
    assert len(clips) == len(board.shots)
    assert sequence_duration(seq) == board.duration
    starts = sorted(c["start"] for c in clips)
    assert starts == [s.start for s in board.shots]


def test_compilation_introduces_no_new_command_types():
    """Раздел 14: рабочий путь не меняется без доказанной выгоды.

    Новый тип команды означал бы, что рендер, проверка чтением и обмен
    проектами должны выучить второй язык. Этого здесь нет.
    """
    _, track_id, media = _project_with_media(4)
    board = sb.plan_storyboard("ролик", media_ids=media, target_ticks=15 * TICKS)
    kinds = {c["type"] for c in sb.compile_to_commands(board, track_id=track_id)}
    assert kinds <= {"clip.add", "title.add", "clip.transform"}


def test_an_empty_storyboard_compiles_to_nothing_rather_than_a_stub():
    board = sb.Storyboard("ролик", (), 0)
    assert sb.compile_to_commands(board, track_id="t") == []


def test_the_module_starts_no_process_and_holds_no_state():
    """Раздел 10 прямо требует слой ЗНАНИЯ, а не резидентную службу.

    Проверка грубая, но по существу: в модуле не должно быть ни запуска
    подпроцессов, ни сети, ни потоков.
    """
    import inspect
    source = inspect.getsource(sb)
    for forbidden in ("subprocess", "socket", "threading", "asyncio", "requests",
                      "httpx", "Popen"):
        assert forbidden not in source, f"слой знаний не имеет права на {forbidden}"
