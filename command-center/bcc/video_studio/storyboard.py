"""Раздел 10: раскадровка и рецепты кадров для Video Studio.

Откуда это взялось и чего здесь НЕТ
-----------------------------------
`video-shotcraft` разобран в `docs/hybrid/THIRD_PARTY_DECISIONS.md` и принят как
REFERENCE_ONLY: исполняемая его часть — это второй браузерный движок на каждый
рендер, а ценность — в ЗНАНИИ о том, как собирается рекламный ролик. Поэтому
ни строки их кода здесь нет, а недостающая нам способность написана своя.

Задание (раздел 10) формулирует роль прямо: «Use Shotcraft primarily as a
knowledge/recipe/skill layer, not as a permanently running heavyweight service.
No need to keep it resident in RAM while unused.» Отсюда три свойства:

* это чистый модуль без процессов, сокетов и фоновых задач;
* он ничего не загружает и не держит — стоимость в покое ровно ноль;
* он ДЕТЕРМИНИРОВАН: одинаковый бриф и одинаковые материалы дают одинаковую
  раскадровку. Модель может её потом уточнить, но базовый план не зависит от
  того, ответил ли кто-то по сети.

Чем это НЕ является
-------------------
Раскадровка — это ПРЕДЛОЖЕНИЕ, а не готовый ролик и не доказательство. Раздел 0
запрещает превращать успех внешнего механизма в завершённую задачу Bossman, и
здесь то же самое в мелком масштабе: `compile_to_commands` выдаёт команды того
же словаря, который уже принимает `commands.apply_command`, а истиной остаётся
существующий путь — рендер, `ffprobe`, полное декодирование, перечитывание
файла. Этот модуль не рендерит, не проверяет и ничего не объявляет готовым.

Ритм
----
«Rhythm-aware editing» из задания сделано самым скучным честным способом: темп
задаётся в ударах в минуту, длительность удара считается в тиках, и точки
склейки ПРИТЯГИВАЮТСЯ к ударам. Никакого анализа звука здесь нет — если темп не
задан, притягивания не происходит, и это видно в самом плане (`beat_ticks=0`),
а не прячется за «ну примерно».
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Sequence

from .model import TICKS, StudioError, uid

# --------------------------------------------------------------------------
# Рецепты кадров
# --------------------------------------------------------------------------
# Каждый рецепт описан в терминах, которые УЖЕ понимает таймлайн: масштаб,
# смещение, непрозрачность, скорость. Новых сущностей в проект не вводится —
# иначе рендер и проверка пришлось бы учить второму языку.


@dataclass(frozen=True)
class ShotRecipe:
    """Один вид кадра: зачем он в ролике и как он двигается."""

    id: str
    purpose: str
    #: доля от общей длительности, к которой стремится кадр
    weight: float
    #: минимальная и максимальная длительность в тиках
    min_ticks: int
    max_ticks: int
    #: движение внутри кадра: начальный и конечный масштаб
    scale_from: float = 1.0
    scale_to: float = 1.0
    #: начальная непрозрачность (для появления из чёрного)
    opacity_from: float = 1.0
    #: нужен ли этому кадру материал; кадры без материала делаются титром
    needs_media: bool = True

    def __post_init__(self) -> None:
        if self.min_ticks <= 0 or self.max_ticks < self.min_ticks:
            raise StudioError(f"рецепт {self.id}: недопустимые границы длительности")
        if self.weight <= 0:
            raise StudioError(f"рецепт {self.id}: вес обязан быть положительным")


SECOND = TICKS

#: Библиотека рецептов. Порядок здесь — это порядок в ролике по умолчанию.
#: Числа взяты не с потолка: это обычная раскладка короткого рекламного
#: ролика — зацепка, показ, деталь, польза, призыв. Владелец может задать
#: свой порядок через `shots=`; тогда библиотека работает как словарь.
RECIPES: dict[str, ShotRecipe] = {
    r.id: r
    for r in (
        ShotRecipe("hook", "зацепка: первые секунды решают, досмотрят ли",
                   weight=1.0, min_ticks=SECOND, max_ticks=3 * SECOND,
                   scale_from=1.12, scale_to=1.0, opacity_from=0.0),
        ShotRecipe("reveal", "показ предмета целиком",
                   weight=1.4, min_ticks=2 * SECOND, max_ticks=5 * SECOND,
                   scale_from=1.0, scale_to=1.06),
        ShotRecipe("detail", "деталь крупно: то, что не видно на общем плане",
                   weight=1.0, min_ticks=SECOND, max_ticks=3 * SECOND,
                   scale_from=1.25, scale_to=1.35),
        ShotRecipe("benefit", "польза: что это даёт смотрящему",
                   weight=1.2, min_ticks=2 * SECOND, max_ticks=4 * SECOND),
        ShotRecipe("proof", "подтверждение: отзыв, число, результат",
                   weight=0.9, min_ticks=SECOND, max_ticks=3 * SECOND),
        ShotRecipe("cta", "призыв к действию",
                   weight=0.8, min_ticks=SECOND, max_ticks=3 * SECOND,
                   needs_media=False),
    )
}

DEFAULT_ORDER = ("hook", "reveal", "detail", "benefit", "cta")


# --------------------------------------------------------------------------
# План
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Shot:
    """Один кадр раскадровки: рецепт, материал, место и длительность."""

    recipe_id: str
    purpose: str
    start: int
    duration: int
    media_id: str | None
    title: str | None = None

    @property
    def end(self) -> int:
        return self.start + self.duration


@dataclass(frozen=True)
class Storyboard:
    brief: str
    shots: tuple[Shot, ...]
    beat_ticks: int
    #: чего не хватило, дословно. Пустой кортеж — значит план полный.
    shortfalls: tuple[str, ...] = field(default_factory=tuple)

    @property
    def duration(self) -> int:
        return self.shots[-1].end if self.shots else 0

    def to_dict(self) -> dict:
        return {
            "brief": self.brief,
            "beat_ticks": self.beat_ticks,
            "duration_ticks": self.duration,
            "shortfalls": list(self.shortfalls),
            "shots": [
                {"recipe": s.recipe_id, "purpose": s.purpose, "start": s.start,
                 "duration": s.duration, "media_id": s.media_id, "title": s.title}
                for s in self.shots
            ],
        }


def beat_ticks(bpm: float | None) -> int:
    """Длительность одного удара в тиках. Без темпа — ноль, и это видно."""
    if not bpm:
        return 0
    if bpm <= 0 or bpm > 400:
        raise StudioError("темп вне разумных границ (0 < bpm <= 400)")
    return int(round(60 * TICKS / bpm))


def _snap(value: int, beat: int) -> int:
    """Притянуть точку к ближайшему удару. Без темпа — вернуть как было."""
    if beat <= 0:
        return value
    return int(round(value / beat)) * beat


def plan_storyboard(brief: str, *, media_ids: Sequence[str],
                    target_ticks: int = 15 * SECOND,
                    bpm: float | None = None,
                    shots: Iterable[str] = DEFAULT_ORDER) -> Storyboard:
    """Собрать раскадровку из рецептов под имеющиеся материалы.

    Важное свойство: если материалов МЕНЬШЕ, чем кадров, план СЖИМАЕТСЯ, а не
    придумывает недостающее. Нехватка называется в `shortfalls` дословно.
    Задание это требует прямым текстом — «never manufacture success».
    """
    brief = (brief or "").strip()
    if not brief:
        raise StudioError("бриф пуст: планировать нечего")
    if target_ticks < SECOND:
        raise StudioError("цель короче секунды")

    order = [s for s in shots]
    unknown = [s for s in order if s not in RECIPES]
    if unknown:
        raise StudioError(f"неизвестные рецепты: {', '.join(sorted(unknown))}")

    beat = beat_ticks(bpm)
    # Счётчик, а не список: раньше здесь лежал `pool`, из которого НИЧЕГО не
    # вынималось, поэтому проверка «материал кончился» не срабатывала никогда.
    # Кадры сверх материала доходили до сборки и получали `media_id=None` —
    # то есть выглядели как кадры, за которыми ничего нет. Поймано тестом
    # `test_missing_footage_shrinks_the_plan_and_is_said_out_loud`.
    remaining = len(media_ids)
    shortfalls: list[str] = []

    chosen: list[ShotRecipe] = []
    for rid in order:
        recipe = RECIPES[rid]
        if recipe.needs_media:
            if remaining == 0:
                shortfalls.append(f"кадр «{rid}» пропущен: материала не осталось")
                continue
            remaining -= 1
        chosen.append(recipe)
    if not chosen:
        raise StudioError("не осталось ни одного кадра: нет материала и нет титров")

    total_weight = sum(r.weight for r in chosen)
    cursor = 0
    used = iter(list(media_ids))
    built: list[Shot] = []
    for recipe in chosen:
        share = int(target_ticks * recipe.weight / total_weight)
        duration = max(recipe.min_ticks, min(recipe.max_ticks, share))
        if beat:
            snapped = _snap(duration, beat)
            # Притягивание не имеет права вывести кадр за собственные границы
            # рецепта: ритм — украшение, границы — смысл.
            duration = max(recipe.min_ticks, min(recipe.max_ticks, snapped or beat))
        media_id = next(used, None) if recipe.needs_media else None
        built.append(Shot(recipe.id, recipe.purpose, cursor, duration, media_id,
                          title=None if recipe.needs_media else brief))
        cursor += duration

    if cursor < target_ticks:
        shortfalls.append(
            f"план короче цели на {(target_ticks - cursor) / TICKS:.1f} с: "
            f"рецепты не растягиваются сверх своих границ")
    return Storyboard(brief, tuple(built), beat, tuple(shortfalls))


# --------------------------------------------------------------------------
# Компиляция в существующий словарь команд
# --------------------------------------------------------------------------


def compile_to_commands(board: Storyboard, *, track_id: str) -> list[dict]:
    """Превратить раскадровку в команды, которые принимает `apply_command`.

    Новых типов команд не вводится намеренно. Всё, что делает раскадровка,
    выражается существующими `clip.add`, `title.add` и `clip.transform` — иначе
    рендер, проверка чтением и обмен проектами пришлось бы учить заново, а
    раздел 14 прямо запрещает менять рабочий путь без доказанной выгоды.
    """
    if not board.shots:
        return []
    out: list[dict] = []
    for shot in board.shots:
        if shot.media_id is None:
            # `title` — объект со своим `text`, а не строка: так его проверяет
            # `validate_project`. Строка проходила бы компиляцию и падала бы
            # уже на проверке проекта, то есть позже и непонятнее.
            out.append({"type": "title.add", "track_id": track_id,
                        "title": {"text": shot.title or board.brief},
                        "start": shot.start, "duration": shot.duration})
            continue
        clip_id = uid()
        out.append({"type": "clip.add", "track_id": track_id,
                    "clip": {"id": clip_id, "media_id": shot.media_id,
                             "start": shot.start, "source_in": 0,
                             "source_out": shot.duration}})
        recipe = RECIPES[shot.recipe_id]
        patch: dict = {}
        if recipe.scale_from != 1.0:
            patch["scale"] = recipe.scale_from
        if recipe.opacity_from != 1.0:
            patch["opacity"] = recipe.opacity_from
        if patch:
            out.append({"type": "clip.transform", "clip_id": clip_id, "patch": patch})
    return out


def describe(board: Storyboard) -> str:
    """Человекочитаемая раскадровка — то, что владелец читает до рендера."""
    lines = [f"Раскадровка: {board.brief}",
             f"длительность {board.duration / TICKS:.1f} с, "
             f"кадров {len(board.shots)}, "
             + (f"ритм {board.beat_ticks / TICKS:.3f} с/удар" if board.beat_ticks
                else "без притягивания к ритму")]
    for i, shot in enumerate(board.shots, 1):
        source = shot.media_id or "титр"
        lines.append(f"  {i}. [{shot.start / TICKS:6.2f}с +{shot.duration / TICKS:4.2f}с] "
                     f"{shot.recipe_id:8s} {source:>12s}  — {shot.purpose}")
    for miss in board.shortfalls:
        lines.append(f"  ! {miss}")
    return "\n".join(lines)


__all__ = ["ShotRecipe", "RECIPES", "DEFAULT_ORDER", "Shot", "Storyboard",
           "beat_ticks", "plan_storyboard", "compile_to_commands", "describe",
           "replace"]
