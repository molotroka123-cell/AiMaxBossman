"""Пакет о смене интерфейса собирается вычитанием, а не добавлением.

Он уедет в изолированную копию репозитория — то есть за пределы машины
владельца. Поэтому вопрос не «что полезно туда положить», а «что оттуда
гарантированно не уйдёт».
"""
from __future__ import annotations

import pytest

from social_farm.browser import FixtureDom, FixtureElement
from social_farm.generation.drift_report import (UnsafeDriftReport,
                                                 build_drift_report,
                                                 scan_for_secrets)
from social_farm.generation.higgsfield_selectors import ACTION_SUBMIT

import higgsfield_kit as kit


async def drifted_session():
    dom = FixtureDom(kit.drifted_page())
    session = kit.session(dom)
    await session.start()
    return dom, session


async def test_the_packet_names_the_action_and_the_strategies_that_missed():
    dom, session = await drifted_session()
    report = await build_drift_report(session, failing_action=ACTION_SUBMIT,
                                      detail="цель не найдена")
    body = report.to_dict()
    assert body["failing_action"] == ACTION_SUBMIT
    assert {"kind": "role", "value": "button|Generate"} in body["expected_strategies"]
    assert body["selector_pack_version"] == session.pack_version


async def test_the_packet_carries_no_markup_at_all():
    """В разметке живут cookie в скрытых полях и токены форм."""
    dom, session = await drifted_session()
    dom.page.markup += '<input type="hidden" name="csrf" value="csrf-abc123def456">'
    report = await build_drift_report(session, failing_action=ACTION_SUBMIT)
    flat = str(report.to_dict())
    assert "<input" not in flat and "<html" not in flat
    assert "csrf-abc123def456" not in flat


async def test_the_packet_carries_no_field_values():
    dom, session = await drifted_session()
    dom.page.elements.append(FixtureElement(
        tag="input", type="text", label="Note", value="ochen-lichnyj-tekst",
        attributes={"id": "note"}))
    report = await build_drift_report(session, failing_action=ACTION_SUBMIT)
    assert "ochen-lichnyj-tekst" not in str(report.to_dict())


async def test_the_query_string_is_dropped_from_the_address():
    """Именно туда формы с method=GET уносят пароли и одноразовые коды."""
    dom, session = await drifted_session()
    dom.page.url = "https://fixture.higgsfield.local/create?otp=918273&next=/x"
    report = await build_drift_report(session, failing_action=ACTION_SUBMIT)
    assert report.url == "https://fixture.higgsfield.local/create"
    assert "918273" not in str(report.to_dict())


async def test_element_references_do_not_travel():
    """Ссылка привязана к поколению снимка и вне его бессмысленна, зато
    выглядит как что-то важное."""
    dom, session = await drifted_session()
    report = await build_drift_report(session, failing_action=ACTION_SUBMIT)
    assert "ref" not in str(report.to_dict())


async def test_a_packet_with_a_secret_shape_is_refused_not_cleaned():
    dom, session = await drifted_session()
    with pytest.raises(UnsafeDriftReport):
        await build_drift_report(session, failing_action=ACTION_SUBMIT,
                                 detail="use Bearer abcdefghijklmnopqrstuvwxyz01")


def test_the_secret_scan_recognises_the_usual_shapes():
    assert scan_for_secrets("sk-abcdefghijklmnopqrst")
    assert scan_for_secrets("ghp_abcdefghijklmnopqrstuv")
    assert scan_for_secrets("vault://x/y")
    assert scan_for_secrets("eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0")
    assert not scan_for_secrets("кнопка Generate не найдена")


async def test_the_fixture_stub_reproduces_the_surface_and_nothing_else():
    """Кодирующий работник обязан начать с воспроизведения, и воспроизводить
    надо страницу, а не слова."""
    dom, session = await drifted_session()
    report = await build_drift_report(session, failing_action=ACTION_SUBMIT)
    stub = report.fixture_stub()
    assert "FixtureElement(" in stub
    assert "def drifted_page_elements()" in stub
    assert "value=" not in stub, "значений полей в заготовке нет"
    assert "ref=" not in stub


async def test_the_surface_is_bounded():
    dom, session = await drifted_session()
    dom.page.elements.extend(
        FixtureElement(tag="div", text=f"row {n}") for n in range(200))
    report = await build_drift_report(session, failing_action=ACTION_SUBMIT,
                                      limit=10)
    assert len(report.surface) == 10
