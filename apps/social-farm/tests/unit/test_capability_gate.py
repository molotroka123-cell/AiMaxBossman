"""Возможности: действие не существует, пока возможность его не подтвердила.

Это первый барьер и самый важный. Политика решает, спрашивать ли человека;
возможность решает, есть ли действие вообще. Разрешение не создаёт возможности.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from social_farm.domain.capability import (Adapter, Capability, CapabilityError,
                                           CapabilitySnapshot, CapabilityStatus,
                                           adapters_for, is_actionable)

SPEC = Path(__file__).resolve().parents[3].parent / "_staging" / "social_farm"


def _snapshot(**caps: str) -> CapabilitySnapshot:
    return CapabilitySnapshot.from_dict({
        "account_id": "acct_ig_01", "provider": "instagram",
        "adapter_version": "1.0.0", "observed_at": "2026-08-28T00:00:00Z",
        "capabilities": [{"name": n, "status": s} for n, s in caps.items()]})


def test_all_nine_states_exist_and_none_was_invented():
    """Перечень закрыт схемой. Лишнее состояние опаснее недостающего:
    его никто не обработает, а выглядеть оно будет рабочим."""
    assert {s.value for s in CapabilityStatus} == {
        "SUPPORTED_OFFICIAL", "SUPPORTED_BROWSER", "SUPPORTED_BOTH", "READ_ONLY",
        "NOT_SUPPORTED", "REQUIRES_APP_REVIEW", "REQUIRES_ACCOUNT_TYPE",
        "REQUIRES_USER_INTERACTION", "TEMPORARILY_DISABLED"}


def test_only_three_states_permit_an_action():
    """Шесть остальных не разрешают действие никаким путём.

    Особенно REQUIRES_APP_REVIEW: заявка подана — не значит разрешено.
    """
    allowed = {s for s in CapabilityStatus if is_actionable(s)}
    assert allowed == {CapabilityStatus.SUPPORTED_OFFICIAL,
                       CapabilityStatus.SUPPORTED_BROWSER,
                       CapabilityStatus.SUPPORTED_BOTH}


def test_adapter_must_match_the_state():
    snap = _snapshot(**{"media.publish.image": "SUPPORTED_OFFICIAL",
                        "relationships.follow": "SUPPORTED_BROWSER",
                        "comments.reply": "SUPPORTED_BOTH"})
    snap.require("media.publish.image", Adapter.OFFICIAL)
    with pytest.raises(CapabilityError, match="через browser"):
        snap.require("media.publish.image", Adapter.BROWSER)

    snap.require("relationships.follow", Adapter.BROWSER)
    with pytest.raises(CapabilityError, match="через official"):
        snap.require("relationships.follow", Adapter.OFFICIAL)

    for adapter in (Adapter.OFFICIAL, Adapter.BROWSER):
        snap.require("comments.reply", adapter)


def test_capability_absent_from_the_snapshot_means_forbidden():
    """Отсутствие сведений — не разрешение.

    Иначе любой сбой сбора возможностей открывал бы действия, которых нет.
    """
    snap = _snapshot(**{"media.read": "SUPPORTED_OFFICIAL"})
    assert snap.status_of("media.delete") is CapabilityStatus.NOT_SUPPORTED
    with pytest.raises(CapabilityError, match="нет в снимке"):
        snap.require("media.delete")


def test_read_only_does_not_permit_writing():
    snap = _snapshot(**{"messages.reply": "READ_ONLY"})
    with pytest.raises(CapabilityError, match="READ_ONLY"):
        snap.require("messages.reply")
    assert adapters_for(CapabilityStatus.READ_ONLY) == frozenset()


def test_expired_snapshot_is_not_a_permission():
    """Возможность, измеренная год назад, — догадка, а не знание."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    cap = Capability(name="media.publish.image",
                     status=CapabilityStatus.SUPPORTED_OFFICIAL,
                     source="instagram", observed_at="2026-08-27T00:00:00Z",
                     expires_at=past)
    snap = CapabilitySnapshot(account_id="a", provider="instagram",
                              observed_at="2026-08-27T00:00:00Z",
                              capabilities={cap.name: cap})
    with pytest.raises(CapabilityError, match="просрочен"):
        snap.require("media.publish.image")


def test_each_state_explains_what_the_owner_should_do():
    """«Нельзя» без причины — это не ответ. Владельцу нужно разное:
    подать заявку, сменить тип аккаунта или забыть."""
    review = _snapshot(**{"messages.send": "REQUIRES_APP_REVIEW"})
    with pytest.raises(CapabilityError, match="проверка приложения"):
        review.require("messages.send")

    kind = _snapshot(**{"insights.read": "REQUIRES_ACCOUNT_TYPE"})
    with pytest.raises(CapabilityError, match="тип аккаунта"):
        kind.require("insights.read")

    never = _snapshot(**{"engagement.like": "NOT_SUPPORTED"})
    with pytest.raises(CapabilityError, match="ждать нечего"):
        never.require("engagement.like")


def test_only_actionable_capabilities_are_offered_to_the_ui():
    snap = _snapshot(**{"media.publish.image": "SUPPORTED_OFFICIAL",
                        "messages.send": "REQUIRES_APP_REVIEW",
                        "relationships.follow": "SUPPORTED_BROWSER",
                        "engagement.like": "TEMPORARILY_DISABLED"})
    assert snap.actionable_names() == ["media.publish.image", "relationships.follow"]


def test_unknown_status_is_refused_not_guessed():
    with pytest.raises(CapabilityError, match="неизвестное состояние"):
        Capability.from_dict({"name": "x", "status": "FULL_CONTROL",
                              "source": "instagram", "observed_at": "t"})


def test_unknown_field_is_refused():
    """`additionalProperties: false`. Проглоченное поле — это поле, которое
    кто-то считает работающим, а оно не работает."""
    with pytest.raises(CapabilityError, match="неизвестные поля"):
        Capability.from_dict({"name": "x", "status": "SUPPORTED_OFFICIAL",
                              "source": "s", "observed_at": "t", "full_control": True})


def test_duplicate_capability_in_a_snapshot_is_refused():
    with pytest.raises(CapabilityError, match="дважды"):
        CapabilitySnapshot.from_dict({
            "account_id": "a", "provider": "instagram", "observed_at": "t",
            "capabilities": [{"name": "media.read", "status": "SUPPORTED_OFFICIAL"},
                             {"name": "media.read", "status": "NOT_SUPPORTED"}]})


@pytest.mark.skipif(not (SPEC / "examples" / "capability_snapshot.json").exists(),
                    reason="пакет спецификации не распакован рядом")
def test_the_specs_own_example_parses():
    """Пример из спецификации обязан разбираться нашим кодом без правок."""
    raw = json.loads((SPEC / "examples" / "capability_snapshot.json").read_text(
        encoding="utf-8"))
    snap = CapabilitySnapshot.from_dict(raw)
    assert snap.account_id == "acct_ig_01"
    assert snap.status_of("media.publish.image") is CapabilityStatus.SUPPORTED_OFFICIAL
    # messages.reply в примере REQUIRES_USER_INTERACTION — действие недоступно
    assert not snap.get("messages.reply").actionable
    assert snap.actionable_names() == ["media.publish.image", "media.publish.reel",
                                       "relationships.follow"]
