"""Девять исходов движка правил (`58_MEDIA_RULE_ENGINE`).

Движок проверяется на `MediaFacts` напрямую, без файлов и без ffprobe, и это
не срезание угла. Правила провайдера — чистая функция от измерений: «кодек
hevc при разрешённых h264» не зависит от того, чем кодек измерен. Отделив это
от пробы, мы получаем все девять исходов детерминированно и в любой среде, а
пробу проверяем отдельно, там, где она настоящая.
"""
from __future__ import annotations

import pytest

from social_farm.domain.errors import ErrorClass
from social_farm.media.asset import AssetType
from social_farm.media.profiles import UNKNOWN, ProviderMediaProfile
from social_farm.media.rules import (MediaFacts, ValidationOutcome, aspect_matches,
                                     parse_ratio, validate)

# --------------------------------------------------------------- фикстуры

def profile(**over) -> ProviderMediaProfile:
    """Полностью определённый профиль: НИ ОДНОГО неизвестного правила.

    Так тест каждого конкретного отказа не спотыкается о G16.
    """
    data = {"provider": "test", "content_type": "REEL", "verified_at": "2026-08-28",
            "mime_allowlist": ["video/mp4"], "container_allowlist": ["mp4"],
            "codec_allowlist": ["h264"], "audio_codec_allowlist": ["aac"],
            "max_bytes": 10_000_000, "min_width": 540, "max_width": 1920,
            "min_height": 960, "max_height": 1920,
            "duration_min_s": 3, "duration_max_s": 90, "aspect_rules": ["9:16"]}
    data.update(over)
    return ProviderMediaProfile.from_dict(data)


def video(**over) -> MediaFacts:
    """Ролик, который проходит эталонный профиль без единой правки."""
    data = {"type": AssetType.VIDEO, "mime": "video/mp4", "container": "mp4",
            "codec": "h264", "audio_codec": "aac", "width": 1080, "height": 1920,
            "duration_ms": 15_000, "bytes": 4_000_000, "prober": "ffprobe"}
    data.update(over)
    return MediaFacts(**data)


# --------------------------------------------------------------- девять исходов

def test_pass():
    """Годный файл проходит без преобразования — и без «на всякий случай»."""
    verdict = validate(video(), profile())
    assert verdict.outcome is ValidationOutcome.PASS
    assert verdict.transforms == ()
    assert verdict.to_error() is None
    assert verdict.blocks_auto_publish is False


def test_pass_with_transform():
    """Чинимое нарушение — это разрешение, а не отказ, если профиль позволяет."""
    verdict = validate(video(codec="hevc"), profile(allow_transcode=True))
    assert verdict.outcome is ValidationOutcome.PASS_WITH_TRANSFORM
    assert verdict.transforms, "не сказано, ЧТО именно надо сделать"
    assert verdict.passed is True


def test_fail_unsupported():
    """Формат вне списка и перекодировать не разрешено."""
    verdict = validate(video(mime="video/x-matroska", container="matroska"),
                       profile(allow_transcode=False))
    assert verdict.outcome is ValidationOutcome.FAIL_UNSUPPORTED


def test_fail_corrupt_comes_from_the_probe_not_from_the_profile():
    """`FAIL_CORRUPT` — вердикт прибора: профиль о целостности ничего не знает.

    Проверяется в `test_media_probe.py` на настоящем обрезанном файле; здесь
    фиксируется граница ответственности.
    """
    from social_farm.media.rules import MediaValidation
    verdict = MediaValidation(outcome=ValidationOutcome.FAIL_CORRUPT,
                              profile_ref="test:REEL:v1", reasons=("PNG обрывается",))
    assert verdict.blocks_auto_publish is True
    assert verdict.to_error().error_class is ErrorClass.MEDIA_INVALID


def test_fail_too_large():
    verdict = validate(video(bytes=99_000_000), profile())
    assert verdict.outcome is ValidationOutcome.FAIL_TOO_LARGE


def test_fail_too_large_is_not_promised_away_by_a_transform():
    """Перекодирование МОГЛО БЫ ужать файл, но обещать это нельзя.

    Обещание, которое сорвётся в момент публикации, хуже отказа сейчас:
    работа успеет уйти в расписание.
    """
    verdict = validate(video(bytes=99_000_000),
                       profile(allow_transcode=True, allow_downscale=True))
    assert verdict.outcome is ValidationOutcome.FAIL_TOO_LARGE


@pytest.mark.parametrize("duration_ms", [1_000, 120_000])
def test_fail_duration(duration_ms):
    """И слишком коротко, и слишком длинно. Обрезать мы не предлагаем:
    это правка замысла автора, а не носителя."""
    verdict = validate(video(duration_ms=duration_ms),
                       profile(allow_transcode=True))
    assert verdict.outcome is ValidationOutcome.FAIL_DURATION


def test_fail_codec():
    verdict = validate(video(codec="vp9"), profile(allow_transcode=False))
    assert verdict.outcome is ValidationOutcome.FAIL_CODEC


def test_fail_codec_covers_audio_too():
    verdict = validate(video(audio_codec="opus"), profile(allow_transcode=False))
    assert verdict.outcome is ValidationOutcome.FAIL_CODEC
    assert any("аудиокодек" in r for r in verdict.reasons)


def test_fail_aspect():
    """Квадрат вместо 9:16, а поля добавлять профиль не разрешает."""
    verdict = validate(video(width=1080, height=1080),
                       profile(min_height=540, allow_aspect_pad=False))
    assert verdict.outcome is ValidationOutcome.FAIL_ASPECT


def test_fail_aspect_is_fixable_by_padding_only():
    """Разрешены поля — исход меняется. Растягивание не предлагается никогда."""
    verdict = validate(video(width=1080, height=1080),
                       profile(min_height=540, allow_aspect_pad=True))
    assert verdict.outcome is ValidationOutcome.PASS_WITH_TRANSFORM
    assert any("поля" in t for t in verdict.transforms)
    assert not any("растян" in t.lower() for t in verdict.transforms)


def test_fail_provider_rule_unknown():
    """Правило не проверено → не выдумываем (`58_MEDIA_RULE_ENGINE`)."""
    verdict = validate(video(), profile(duration_max_s=UNKNOWN))
    assert verdict.outcome is ValidationOutcome.FAIL_PROVIDER_RULE_UNKNOWN
    assert "duration_max_s" in verdict.unknown_rules
    assert verdict.blocks_auto_publish is True


def test_all_nine_outcomes_exist_and_no_more():
    """Перечень закрыт. Десятый исход — это изменение контракта."""
    assert {o.value for o in ValidationOutcome} == {
        "PASS", "PASS_WITH_TRANSFORM", "FAIL_UNSUPPORTED", "FAIL_CORRUPT",
        "FAIL_TOO_LARGE", "FAIL_DURATION", "FAIL_CODEC", "FAIL_ASPECT",
        "FAIL_PROVIDER_RULE_UNKNOWN"}


# --------------------------------------------------------------- различение null и UNKNOWN

def test_explicit_null_means_no_constraint_absent_key_means_unknown():
    """Ось, на которой держится G16.

    Одно и то же поле: явный `null` — «ограничения нет, проверено»,
    отсутствие ключа — «не знаем». Спутать их значит либо блокировать
    исправный контент, либо публиковать против непроверенных правил.
    """
    declared = ProviderMediaProfile.from_dict(
        {"provider": "t", "content_type": "IMAGE", "verified_at": "x",
         "max_bytes": None})
    forgotten = ProviderMediaProfile.from_dict(
        {"provider": "t", "content_type": "IMAGE", "verified_at": "x"})
    assert declared.max_bytes is None
    assert forgotten.max_bytes is UNKNOWN
    assert "max_bytes" not in declared.unknown_rules()
    assert "max_bytes" in forgotten.unknown_rules()


def test_a_forgotten_rule_blocks_rather_than_permits():
    """Направление умолчания выбрано так, что забывчивость безопасна."""
    facts = MediaFacts(type=AssetType.IMAGE, mime="image/png", container="png",
                       codec="png", width=1080, height=1350, bytes=1000,
                       prober="header")
    bare = ProviderMediaProfile.from_dict(
        {"provider": "t", "content_type": "IMAGE", "verified_at": "x"})
    assert validate(facts, bare).outcome is \
        ValidationOutcome.FAIL_PROVIDER_RULE_UNKNOWN


def test_unknown_rules_irrelevant_to_this_file_do_not_block():
    """У картинки не спрашивают длительность.

    Иначе неизвестное правило про видео блокировало бы публикацию фотографий,
    и G16 превратился бы в запрет на всё.
    """
    facts = MediaFacts(type=AssetType.IMAGE, mime="image/png", container="png",
                       codec="png", width=1080, height=1350, bytes=1000,
                       prober="header")
    image_profile = ProviderMediaProfile.from_dict(
        {"provider": "t", "content_type": "IMAGE", "verified_at": "x",
         "mime_allowlist": ["image/png"], "container_allowlist": ["png"],
         "codec_allowlist": ["png"], "max_bytes": 10_000_000,
         "min_width": 100, "max_width": 4000, "min_height": 100, "max_height": 4000,
         "aspect_rules": ["4:5"]})
    # duration_* и audio_codec_allowlist отсутствуют — и это не мешает.
    assert validate(facts, image_profile).outcome is ValidationOutcome.PASS


# --------------------------------------------------------------- приоритет исходов

def test_a_definite_failure_outranks_not_knowing():
    """Если файл заведомо велик, надо сказать это, а не «правила неизвестны».

    Первое — действие, которое владелец может выполнить. Второе — не его вина.
    """
    verdict = validate(video(bytes=99_000_000), profile(aspect_rules=UNKNOWN))
    assert verdict.outcome is ValidationOutcome.FAIL_TOO_LARGE


def test_not_knowing_outranks_a_fixable_problem():
    """Незнание правил перекодированием не лечится."""
    verdict = validate(video(codec="hevc"),
                       profile(allow_transcode=True, duration_max_s=UNKNOWN))
    assert verdict.outcome is ValidationOutcome.FAIL_PROVIDER_RULE_UNKNOWN


# --------------------------------------------------------------- отображение на ошибки

@pytest.mark.parametrize("outcome", [o for o in ValidationOutcome
                                     if o.value.startswith("FAIL_")])
def test_every_failure_maps_onto_the_closed_error_list(outcome):
    """Все `FAIL_*` → `MEDIA_INVALID`, исходный код в `safe_detail`.

    Перечень классов ошибок не расширяется: он контракт (решение C15).
    """
    from social_farm.media.rules import MediaValidation
    error = MediaValidation(outcome=outcome, profile_ref="p").to_error()
    assert error.error_class is ErrorClass.MEDIA_INVALID
    assert outcome.value in error.safe_detail
    assert error.retryable is False, "непригодное медиа не чинится повтором"
    assert error.safe_to_retry_external is False


# --------------------------------------------------------------- соотношения

def test_ratio_parsing_and_ranges():
    assert parse_ratio("9:16") == pytest.approx(0.5625)
    assert parse_ratio("1.91:1") == pytest.approx(1.91)
    assert aspect_matches(0.8, ["4:5..1.91:1"]) is True
    assert aspect_matches(1.91, ["4:5..1.91:1"]) is True
    assert aspect_matches(0.5625, ["4:5..1.91:1"]) is False


def test_rounding_from_our_own_render_is_not_a_new_aspect_ratio():
    """1080×1350 и 1078×1348 — один кадр. Допуск существует ради этого."""
    assert aspect_matches(1078 / 1348, ["4:5"]) is True
    assert aspect_matches(0.5625, ["4:5"]) is False


def test_unparseable_ratio_is_an_error_not_a_silent_pass():
    with pytest.raises(ValueError):
        parse_ratio("широкий")
