"""Настройки, отпечаток цели и запись аудита.

Три маленьких модуля, каждый из которых охраняет что-то большое: числа, из-за
которых работа останавливается; способ отличить «ту же кнопку» от «другой»;
и то, что владелец увидит в журнале через месяц.
"""
from __future__ import annotations

import pytest

from social_farm.browser import (FINGERPRINT_ALGORITHM, BrowserAuditRecord,
                                 BrowserConfig, InMemoryAuditSink, Redactor,
                                 TargetDescriptor, fingerprint_of, normalize_text,
                                 target_fingerprint)
from social_farm.browser.secrets import MASK

FIELDS = dict(role="button", accessible_name="Опубликовать", text="Опубликовать",
              tag="button", ordinal=0)


# ------------------------------------------------------------------ настройки

def test_the_browser_path_is_off_unless_it_is_switched_on():
    """Забытая настройка означает «выключено», а не «работает как получится»."""
    assert BrowserConfig().enabled is False
    assert BrowserConfig.from_env({}).enabled is False
    assert BrowserConfig.from_env({"SF_BROWSER_FALLBACK": "1"}).enabled is True


def test_numbers_come_from_the_environment_and_are_clamped():
    config = BrowserConfig.from_env({
        "SF_BROWSER_FAILURE_THRESHOLD": "5",
        "SF_BROWSER_COOLDOWN_MINUTES": "0",           # ниже допустимого
        "SF_BROWSER_ACTION_TIMEOUT_MS": "99999999",   # выше допустимого
        "SF_BROWSER_REFRESH_ATTEMPTS": "не число",
    })
    assert config.deterministic_failure_threshold == 5
    assert config.cooldown_minutes == 1
    assert config.action_timeout_ms == 600_000
    assert config.refresh_attempts == BrowserConfig().refresh_attempts


def test_an_unknown_account_setting_is_an_error_not_a_shrug():
    """Молча проглоченный ключ — это настройка, которую владелец считает
    применённой, а она не применена."""
    with pytest.raises(ValueError):
        BrowserConfig().merged({"headles": True})


def test_account_settings_can_enable_the_browser_path():
    assert BrowserConfig().merged({"enabled": True}).enabled is True


def test_there_is_no_switch_for_the_defences_themselves():
    """Проверка личности, редакция и отпечаток цели — не настройки, а границы."""
    names = set(BrowserConfig.__slots__)
    for forbidden in ("verify_identity", "redact_secrets", "check_fingerprint",
                      "solve_challenges", "challenge_attempts", "stealth"):
        assert forbidden not in names


def test_context_permissions_cannot_be_widened_by_account_settings():
    """Настройки аккаунта приходят данными. Права каталога с сессией — нет."""
    with pytest.raises(ValueError):
        BrowserConfig().merged({"context_dir_mode": 0o777})
    with pytest.raises(ValueError):
        BrowserConfig(context_dir_mode=0o755)
    assert BrowserConfig().merged({"context_dir_mode": 0o600}).context_dir_mode == 0o600


def test_settings_are_printable_without_secrets():
    printed = BrowserConfig().as_dict()
    assert printed["context_dir_mode"] == "0o700"
    assert set(printed) >= {"enabled", "headless", "context_root"}


# ------------------------------------------------------------------ отпечаток

def test_the_algorithm_is_stated_and_stable():
    assert FINGERPRINT_ALGORITHM == (
        "sha256(role|accessible_name|normalized_text|tag|ordinal|"
        "selector_pack_version)")
    assert target_fingerprint(**FIELDS, pack_version="1.0.0") == \
        target_fingerprint(**FIELDS, pack_version="1.0.0")


def test_a_new_selector_pack_invalidates_old_fingerprints():
    """Обновление пакета меняет семантику поиска: старый отпечаток о ней ничего
    не знает и действительным считаться не может."""
    assert target_fingerprint(**FIELDS, pack_version="1.0.0") != \
        target_fingerprint(**FIELDS, pack_version="1.0.1")


def test_identical_elements_are_told_apart_by_their_ordinal():
    """Три одинаковые кнопки «Удалить» — три разные записи."""
    first = dict(FIELDS, ordinal=0)
    second = dict(FIELDS, ordinal=1)
    assert target_fingerprint(**first, pack_version="1.0.0") != \
        target_fingerprint(**second, pack_version="1.0.0")


@pytest.mark.parametrize("changed", [
    {"role": "link"}, {"accessible_name": "Удалить"}, {"text": "Удалить"},
    {"tag": "a"},
])
def test_a_different_meaning_is_a_different_target(changed):
    assert target_fingerprint(**dict(FIELDS, **changed), pack_version="1") != \
        target_fingerprint(**FIELDS, pack_version="1")


def test_repainting_is_not_a_different_target():
    """`Опубликовать` и `ОПУБЛИКОВАТЬ` — одна кнопка. Считать иначе значит
    ломать работу при смене темы оформления."""
    loud = dict(FIELDS, text="  ОПУБЛИКОВАТЬ\n", accessible_name="ОПУБЛИКОВАТЬ")
    assert target_fingerprint(**loud, pack_version="1") == \
        target_fingerprint(**FIELDS, pack_version="1")


def test_the_value_of_a_field_is_not_part_of_the_target_identity():
    """Значение поля в отпечаток не входит: пользователь мог допечатать символ
    между снимком и действием, и это не повод считать кнопку другой.

    Значение секретного поля сюда физически не доходит — порт его не отдаёт.
    """
    assert "value" not in TargetDescriptor.__slots__
    descriptor = TargetDescriptor.from_dict({
        "ref": "sf-1-0", "tag": "input", "role": "textbox", "type": "password",
        "accessible_name": "Пароль", "text": "", "secret": True, "filled": True})
    assert descriptor.secret and descriptor.filled
    assert fingerprint_of(descriptor, "1.0.0")


def test_normalization_is_bounded():
    long_text = "а" * 500
    assert len(normalize_text(long_text)) == 220
    assert normalize_text("  Привет\t\nмир  ") == "привет мир"


def test_semantic_identity_is_readable_by_a_human():
    descriptor = TargetDescriptor.from_dict(
        {"ref": "sf-1-2", "tag": "button", "role": "button", "text": "Удалить",
         "ordinal": 2})
    assert descriptor.semantic_identity() == "button[удалить]#2"


# ------------------------------------------------------------------ аудит

def record(**overrides) -> BrowserAuditRecord:
    fields = dict(account_id="acc-A", action="media.publish.image", result="ok",
                  at="2026-08-28T12:00:00+00:00", target_identity="button[поделиться]#0",
                  url_before="https://fixture.local/", url_after="https://fixture.local/",
                  secret_ref="vault://login")
    fields.update(overrides)
    return BrowserAuditRecord(**fields)


def test_a_record_cannot_be_serialized_past_the_redactor():
    """Забыть редакцию не получится — не на что будет вызвать."""
    with pytest.raises(TypeError):
        record().to_dict()


def test_the_reference_to_the_secret_stays_visible():
    """Аудит обязан говорить, чем именно входили. Ссылка — не значение."""
    row = record().to_dict(Redactor())
    assert row["vault_ref"] == "vault://login"
    assert "secret_ref" not in row


def test_a_known_secret_value_is_cleaned_out_of_the_record():
    row = record(detail="в поле оказался parol-2026-ochen-dlinnyj").to_dict(
        Redactor(["parol-2026-ochen-dlinnyj"]))
    assert "parol-2026" not in row["detail"]
    assert MASK in row["detail"]


def test_page_markup_never_enters_a_record():
    """В разметке лежат и cookie в скрытых полях, и токены форм, и переписка."""
    assert "markup" not in BrowserAuditRecord.__slots__
    assert "html" not in BrowserAuditRecord.__slots__
    assert "elements" not in BrowserAuditRecord.__slots__


def test_a_record_carries_what_the_specification_demands():
    """`09_INSTAGRAM_BROWSER_FALLBACK`: тип действия, личность цели, адреса до и
    после, ссылка на снимок, результат, отпечаток."""
    fields = set(BrowserAuditRecord.__slots__)
    assert {"action", "target_identity", "url_before", "url_after",
            "screenshot_ref", "result", "target_fingerprint"} <= fields


def test_the_sink_serializes_everything_through_one_redactor():
    sink = InMemoryAuditSink()
    sink.write(record(detail="пароль parol-2026-ochen-dlinnyj в тексте"))
    rows = sink.dicts(Redactor(["parol-2026-ochen-dlinnyj"]))
    assert "parol-2026" not in str(rows)
    assert sink.by_action("media.publish.image")
