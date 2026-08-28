"""Возможность браузерного пути: чем подтверждается и как понижается сама.

Два обещания проверяются здесь. Первое: возможность, проверенная только на
фикстуре, остаётся `EXPERIMENTAL` — в этой среде живого аккаунта нет, и ни одна
возможность Instagram до `VERIFIED_BROWSER` подняться не может. Второе: три
подряд детерминированных отказа на ОДНОЙ версии пакета селекторов означают, что
интерфейс провайдера сменился, и возможность отключается сама.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from browser_kit import FEED_TEXT, feed_page, session
from social_farm.browser import (BrowserCapabilityLedger, BrowserCapabilityState,
                                 BrowserConfig, BrowserState, Evidence, FailureKind,
                                 FixtureDom, PromotionRefused)
from social_farm.browser.session import BrokenUi
from social_farm.domain.capability import CapabilityStatus

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
PACK = "1.0.0"


def ledger(**config: object) -> BrowserCapabilityLedger:
    return BrowserCapabilityLedger(account_id="acc-A", provider="fixture",
                                   config=BrowserConfig(**config))


# ------------------------------------------------------------------ повышение

def test_a_declared_capability_starts_experimental():
    book = ledger()
    record = book.declare("media.publish.image", selector_pack_version=PACK, now=NOW)
    assert record.state is BrowserCapabilityState.EXPERIMENTAL
    assert book.status_of("media.publish.image") is CapabilityStatus.TEMPORARILY_DISABLED


def test_a_capability_proven_only_on_a_fixture_is_not_promoted():
    """Главная честность этого потока: фикстура не заменяет живой браузер."""
    book = ledger()
    book.declare("media.publish.image", selector_pack_version=PACK, now=NOW)
    with pytest.raises(PromotionRefused) as exc:
        book.promote("media.publish.image", evidence=Evidence.fixture_only(), now=NOW)
    assert "real_browser" in str(exc.value)
    record = book.records["media.publish.image"]
    assert record.state is BrowserCapabilityState.EXPERIMENTAL
    assert "real_browser" in book.reason_of("media.publish.image")


def test_an_undeclared_capability_cannot_be_promoted():
    with pytest.raises(PromotionRefused):
        ledger().promote("media.publish.image", evidence=Evidence.fixture_only())


def test_promotion_requires_every_piece_of_evidence():
    """Семь условий из `44_...`, и ни одно не выводится из остальных."""
    book = ledger()
    book.declare("media.publish.image", selector_pack_version=PACK, now=NOW)
    full = Evidence(deterministic_navigation=True, target_identity=True,
                    successful_test=True, failure_behavior=True,
                    policy_classification=True, account_type_compatible=True,
                    real_browser=True, note="проверено на живом аккаунте")
    assert full.missing() == []
    record = book.promote("media.publish.image", evidence=full, now=NOW)
    assert record.state is BrowserCapabilityState.VERIFIED_BROWSER
    assert book.status_of("media.publish.image") is CapabilityStatus.SUPPORTED_BROWSER


def test_an_unknown_capability_is_not_supported_rather_than_allowed():
    assert ledger().status_of("media.publish.reel") is CapabilityStatus.NOT_SUPPORTED


# ------------------------------------------------------------------ понижение

def test_three_deterministic_failures_on_one_pack_version_demote_the_capability():
    book = ledger()
    book.declare("media.publish.image", selector_pack_version=PACK, now=NOW)
    for number in (1, 2):
        record = book.record_failure("media.publish.image", selector_pack_version=PACK,
                                     kind=FailureKind.TARGET_MISSING, now=NOW)
        assert record.consecutive_failures == number
        assert record.state is BrowserCapabilityState.EXPERIMENTAL
    record = book.record_failure("media.publish.image", selector_pack_version=PACK,
                                 kind=FailureKind.TARGET_MISSING, now=NOW)
    assert record.state is BrowserCapabilityState.BROKEN_UI_VERSION
    assert record.disabled_until == (NOW + timedelta(minutes=60)).isoformat()
    assert book.cooling_down("media.publish.image", now=NOW)
    assert not book.cooling_down("media.publish.image", now=NOW + timedelta(hours=2))
    assert "нужна новая версия пакета, а не повтор" in record.reason


def test_the_threshold_is_a_setting_not_a_constant():
    """«Когда беспокоить владельца» — его решение, а не свойство сборки."""
    book = ledger(deterministic_failure_threshold=2, cooldown_minutes=15)
    book.declare("media.delete", selector_pack_version=PACK, now=NOW)
    book.record_failure("media.delete", selector_pack_version=PACK,
                        kind=FailureKind.TARGET_AMBIGUOUS, now=NOW)
    record = book.record_failure("media.delete", selector_pack_version=PACK,
                                 kind=FailureKind.TARGET_AMBIGUOUS, now=NOW)
    assert record.state is BrowserCapabilityState.BROKEN_UI_VERSION
    assert record.disabled_until == (NOW + timedelta(minutes=15)).isoformat()


def test_a_success_between_failures_resets_the_counter():
    book = ledger()
    book.declare("media.publish.image", selector_pack_version=PACK, now=NOW)
    book.record_failure("media.publish.image", selector_pack_version=PACK,
                        kind=FailureKind.TARGET_MISSING, now=NOW)
    book.record_failure("media.publish.image", selector_pack_version=PACK,
                        kind=FailureKind.TARGET_MISSING, now=NOW)
    book.record_success("media.publish.image", selector_pack_version=PACK, now=NOW)
    record = book.record_failure("media.publish.image", selector_pack_version=PACK,
                                 kind=FailureKind.TARGET_MISSING, now=NOW)
    assert record.consecutive_failures == 1
    assert record.state is BrowserCapabilityState.EXPERIMENTAL


def test_failures_on_a_previous_pack_version_do_not_accumulate():
    """Отказы на старом пакете ничего не говорят о новом."""
    book = ledger()
    book.declare("media.publish.image", selector_pack_version=PACK, now=NOW)
    for _ in range(2):
        book.record_failure("media.publish.image", selector_pack_version="1.0.0",
                            kind=FailureKind.TARGET_MISSING, now=NOW)
    record = book.record_failure("media.publish.image", selector_pack_version="2.0.0",
                                 kind=FailureKind.TARGET_MISSING, now=NOW)
    assert record.consecutive_failures == 1
    assert record.failing_pack_version == "2.0.0"


@pytest.mark.parametrize("kind", [FailureKind.STALE_TARGET,
                                  FailureKind.TAKEOVER_REQUIRED,
                                  FailureKind.TRANSIENT])
def test_non_deterministic_failures_do_not_demote(kind):
    """Гонка и человек — не смена интерфейса. Иначе одна перерисовка страницы
    отключала бы возможность, которая работает."""
    book = ledger()
    book.declare("media.publish.image", selector_pack_version=PACK, now=NOW)
    for _ in range(5):
        record = book.record_failure("media.publish.image",
                                     selector_pack_version=PACK, kind=kind, now=NOW)
    assert record.consecutive_failures == 0
    assert record.state is BrowserCapabilityState.EXPERIMENTAL


def test_recovery_does_not_restore_a_verified_status_by_itself():
    """Одно удачное нажатие — не подтверждение. Подтверждение получают заново."""
    book = ledger()
    book.declare("media.publish.image", selector_pack_version=PACK, now=NOW)
    for _ in range(3):
        book.record_failure("media.publish.image", selector_pack_version=PACK,
                            kind=FailureKind.POSTCONDITION_FAILED, now=NOW)
    record = book.record_success("media.publish.image", selector_pack_version=PACK,
                                 now=NOW)
    assert record.state is BrowserCapabilityState.EXPERIMENTAL
    assert record.disabled_until is None


def test_a_disabled_capability_stays_disabled():
    book = ledger()
    book.declare("media.delete", selector_pack_version=PACK, now=NOW)
    record = book.disable("media.delete", reason="выключено владельцем", now=NOW)
    assert record.state is BrowserCapabilityState.DISABLED
    assert book.status_of("media.delete") is CapabilityStatus.TEMPORARILY_DISABLED


# ------------------------------------------------------------------ снимок

def test_snapshot_carries_reasons_and_expiry():
    book = ledger()
    book.declare("media.publish.image", selector_pack_version=PACK, now=NOW)
    for _ in range(3):
        book.record_failure("media.publish.image", selector_pack_version=PACK,
                            kind=FailureKind.TARGET_MISSING, now=NOW)
    book.declare("content.draft", selector_pack_version=PACK, now=NOW)
    snapshot = book.snapshot(now=NOW, adapter_version="browser/0.1.0")
    broken = snapshot.capabilities["media.publish.image"]
    fresh = snapshot.capabilities["content.draft"]
    assert broken.status is CapabilityStatus.TEMPORARILY_DISABLED
    assert broken.expires_at == (NOW + timedelta(minutes=60)).isoformat()
    assert fresh.expires_at == (NOW + timedelta(hours=24)).isoformat()
    assert fresh.source == "fixture/browser"
    assert "real_browser" in fresh.reason


# --------------------------------------------------- то же самое через сессию

async def test_the_session_demotes_a_capability_after_three_real_failures():
    """Тот же порог, но пройденный настоящими действиями, а не вызовами учёта.

    Интерфейс «сменился»: подтверждающего текста на странице нет, и каждая
    попытка удаления отказывается по одной и той же причине.
    """
    dom = FixtureDom(feed_page())
    dom.page.text = FEED_TEXT                      # диалога подтверждения нет
    sess = session(dom)
    await sess.start()
    for _ in range(3):
        with pytest.raises(BrokenUi):
            await sess.click("media.delete")
        assert sess.state is BrowserState.BROKEN_UI
        sess.recover_from_broken_ui()
    record = sess.ledger.records["media.delete"]
    assert record.consecutive_failures == 3
    assert record.state is BrowserCapabilityState.BROKEN_UI_VERSION
    assert record.failing_pack_version == "1.0.0"
    assert sess.ledger.status_of("media.delete") is CapabilityStatus.TEMPORARILY_DISABLED
    assert dom.clicks == [], "разрушающее действие так и не было выполнено"
