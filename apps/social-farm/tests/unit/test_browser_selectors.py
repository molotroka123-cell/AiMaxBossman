"""Пакет селекторов: схема, порядок стратегий и запрет хрупкого на опасном.

Правило порядка жёстче схемы намеренно. Схема разрешает `css` и `xpath` где
угодно; мы — нет. Хрупкий селектор на удалении означает удаление не того
объекта, и никакая настройка этого не разрешает.
"""
from __future__ import annotations

import copy
import zipfile
from pathlib import Path

import pytest

from browser_kit import pack_document
from social_farm.browser import (SelectorPackError, SelectorRegistry, Strategy,
                                 load_pack, validate_pack_document)
from social_farm.browser.selectors import LAST_RESORT_KINDS, SEMANTIC_KINDS

REPO = Path(__file__).resolve().parents[4]
SPEC_ZIP = REPO / "BOSSMAN_SOCIAL_FARM_APP4_TECH_SPEC_V1_1.zip"
SCHEMA = "schemas/selector_pack.schema.json"


def with_action(**overrides) -> dict:
    """Пакет из одного действия — чтобы проверять правило, а не окружение."""
    action = {"action": "media.delete", "target": "кнопка «Удалить»",
              "capability": "media.delete",
              "strategies": [{"kind": "role", "value": "button|Удалить"}],
              "confirmation_text": "удалить публикацию?",
              "postconditions": ["text_absent:удалить публикацию?"]}
    action.update(overrides)
    return {"provider": "fixture", "version": "1.0.0", "ui_revision": "2026-08-28",
            "actions": [action]}


# ------------------------------------------------------------------ схема

def test_the_reference_pack_loads():
    pack = load_pack(pack_document())
    assert pack.version == "1.0.0"
    assert pack.require("media.publish.image").target


def test_schema_fields_match_the_specification():
    """Перечень стратегий проверяется по самой схеме из пакета спецификации."""
    if not SPEC_ZIP.exists():                                  # pragma: no cover
        pytest.skip("пакет спецификации недоступен")
    import json
    with zipfile.ZipFile(SPEC_ZIP) as archive:
        schema = json.loads(archive.read(SCHEMA))
    strategy = (schema["properties"]["actions"]["items"]["properties"]["strategies"]
                ["items"]["properties"]["kind"]["enum"])
    assert set(strategy) == set(SEMANTIC_KINDS) | set(LAST_RESORT_KINDS)
    required = schema["required"]
    for key in required:
        broken = {k: v for k, v in pack_document().items() if k != key}
        with pytest.raises(SelectorPackError):
            validate_pack_document(broken)


def test_unknown_pack_fields_are_refused():
    """`additionalProperties: false` — не украшение схемы: незнакомое поле
    означает, что пакет писали под другой разбор."""
    raw = pack_document()
    raw["fallback_selectors"] = ["#share"]
    with pytest.raises(SelectorPackError):
        validate_pack_document(raw)


def test_an_unknown_strategy_kind_is_refused():
    with pytest.raises(SelectorPackError):
        validate_pack_document(with_action(
            strategies=[{"kind": "coordinates", "value": "120,240"}]))
    with pytest.raises(SelectorPackError):
        Strategy(kind="coordinates", value="120,240")


def test_a_strategy_without_a_value_is_refused():
    with pytest.raises(SelectorPackError):
        Strategy(kind="role", value="   ")


def test_a_duplicate_action_is_refused():
    raw = pack_document()
    raw["actions"].append(copy.deepcopy(raw["actions"][0]))
    with pytest.raises(SelectorPackError):
        load_pack(raw)


def test_an_empty_action_list_is_refused():
    raw = pack_document()
    raw["actions"] = []
    with pytest.raises(SelectorPackError):
        validate_pack_document(raw)


# ------------------------------------------------------------------ порядок

def test_a_brittle_strategy_cannot_come_first():
    with pytest.raises(SelectorPackError) as exc:
        load_pack(with_action(capability="content.draft", confirmation_text="",
                              postconditions=[],
                              strategies=[{"kind": "css", "value": "#drop"},
                                          {"kind": "role", "value": "button|Удалить"}]))
    assert "первой обязана быть семантическая" in str(exc.value)


def test_a_semantic_strategy_cannot_follow_a_brittle_one():
    with pytest.raises(SelectorPackError):
        load_pack(with_action(capability="content.draft", confirmation_text="",
                              postconditions=[],
                              strategies=[{"kind": "role", "value": "button|Черновик"},
                                          {"kind": "css", "value": "#draft"},
                                          {"kind": "label", "value": "Черновик"}]))


def test_a_brittle_strategy_is_allowed_in_the_tail_of_a_safe_action():
    pack = load_pack(with_action(
        action="content.draft", capability="content.draft", confirmation_text="",
        postconditions=[],
        strategies=[{"kind": "label", "value": "Подпись"},
                    {"kind": "css", "value": "#caption"}]))
    assert len(pack.require("content.draft").last_resort_strategies) == 1


@pytest.mark.parametrize("capability", ["media.delete", "media.archive",
                                        "comments.delete", "comments.hide",
                                        "account.password.change",
                                        "account.disconnect"])
def test_dangerous_classes_forbid_brittle_strategies_entirely(capability):
    """Удаление, модерация и безопасность аккаунта — по смыслу или никак."""
    with pytest.raises(SelectorPackError) as exc:
        load_pack(with_action(capability=capability,
                              strategies=[{"kind": "role", "value": "button|Удалить"},
                                          {"kind": "xpath", "value": "//button[@id='x']"}]))
    assert "не может использовать" in str(exc.value)


def test_a_destructive_action_must_carry_confirmation_text():
    with pytest.raises(SelectorPackError) as exc:
        load_pack(with_action(confirmation_text=""))
    assert "текст подтверждения" in str(exc.value)


def test_a_destructive_action_must_carry_a_postcondition():
    with pytest.raises(SelectorPackError) as exc:
        load_pack(with_action(postconditions=[]))
    assert "постусловие" in str(exc.value)


def test_an_unclassified_capability_is_treated_as_the_most_dangerous_one():
    """Возможность, которую никто не классифицировал, — не безопасная.

    Практическое следствие: действие без поля `capability` попадает в
    разрушающий класс и обязано нести подтверждение и постусловие. Пакет
    данных не вправе назначать себе класс безопасности сам.
    """
    action = load_pack(with_action(capability="")).require("media.delete")
    assert action.destructive and action.brittle_forbidden
    with pytest.raises(SelectorPackError):
        load_pack(with_action(capability="", confirmation_text=""))


# ------------------------------------------------------------------ реестр

def test_a_disabled_pack_is_not_handed_out():
    """«Отключить пакет после поломки» означает, что им перестают пользоваться,
    а не что о нём написали в журнале."""
    registry = SelectorRegistry()
    registry.register_document(pack_document())
    assert registry.resolve("fixture").version == "1.0.0"
    registry.disable("fixture", "1.0.0", reason="интерфейс сменился")
    with pytest.raises(SelectorPackError) as exc:
        registry.resolve("fixture")
    assert "нужна новая версия пакета, а не повтор" in str(exc.value)
    assert not registry.is_enabled("fixture", "1.0.0")


def test_versions_live_side_by_side_and_only_one_is_active():
    registry = SelectorRegistry()
    registry.register_document(pack_document())
    second = pack_document()
    second["version"] = "2.0.0"
    registry.register_document(second)
    assert registry.versions("fixture") == ["1.0.0", "2.0.0"]
    assert registry.resolve("fixture").version == "2.0.0"
    assert registry.resolve("fixture", "1.0.0").version == "1.0.0"


def test_an_unregistered_provider_is_a_refusal_not_a_guess():
    registry = SelectorRegistry()
    with pytest.raises(SelectorPackError):
        registry.resolve("instagram")
    registry.register_document(pack_document())
    with pytest.raises(SelectorPackError):
        registry.resolve("fixture", "9.9.9")


def test_a_missing_action_names_what_the_pack_does_have():
    pack = load_pack(pack_document())
    with pytest.raises(SelectorPackError) as exc:
        pack.require("media.publish.reel")
    assert "media.publish.image" in str(exc.value)


def test_pack_round_trips_through_its_dictionary_form():
    pack = load_pack(pack_document())
    again = load_pack(pack.to_dict())
    assert again.to_dict() == pack.to_dict()
