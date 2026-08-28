"""Контекст аккаунта A не может обслуживать аккаунт B.

Это главный инвариант потока: ошибка здесь означает публикацию от чужого имени,
и откатить её нельзя. Поэтому проверяется не «вызывающий код аккуратен», а что
неаккуратный вызывающий код будет остановлен — на каждой из четырёх опор:
каталог, маркер владельца, права, процесс.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from browser_kit import feed_page, session
from social_farm.browser import (AccountContextRoot, BrowserConfig,
                                 BrowserWorkerHandle, CrossAccountViolation,
                                 FixtureDom, SecretInTransit, WorkerRequest,
                                 account_slug, guard_account, guard_payload)
from social_farm.browser.isolation import MARKER_NAME

POSIX = os.name == "posix"
posix_only = pytest.mark.skipif(
    not POSIX, reason="NOT RUN: права каталогов проверяются только на POSIX")


# ------------------------------------------------------------------ каталоги

def test_accounts_get_different_directories(tmp_path: Path):
    root = AccountContextRoot(root=tmp_path)
    a, b = root.prepare("acc-A"), root.prepare("acc-B")
    assert a != b
    assert a.parent == b.parent == tmp_path


def test_identifiers_differing_only_in_unsafe_characters_do_not_collide(tmp_path: Path):
    """`acc/1` и `acc:1` дают одно читаемое имя. Хеш разводит их обратно."""
    assert account_slug("acc/1") != account_slug("acc:1")
    root = AccountContextRoot(root=tmp_path)
    assert root.prepare("acc/1") != root.prepare("acc:1")


def test_directory_carries_the_owner_marker(tmp_path: Path):
    root = AccountContextRoot(root=tmp_path)
    directory = root.prepare("acc-A")
    assert (directory / MARKER_NAME).read_text(encoding="utf-8") == "acc-A"
    assert root.owner_of(directory) == "acc-A"


def test_foreign_directory_is_refused(tmp_path: Path):
    """Каталог другого аккаунта, поданный вручную, отвергается по маркеру."""
    root = AccountContextRoot(root=tmp_path)
    root.prepare("acc-A")
    foreign = root.prepare("acc-B")
    with pytest.raises(CrossAccountViolation) as exc:
        root.assert_owned("acc-A", foreign)
    assert exc.value.expected == "acc-A"


def test_directory_without_a_marker_is_refused(tmp_path: Path):
    """Подставленный руками каталог с правильным именем, но без маркера."""
    root = AccountContextRoot(root=tmp_path)
    directory = root.path_for("acc-A")
    directory.mkdir(parents=True)
    with pytest.raises(CrossAccountViolation):
        root.assert_owned("acc-A", directory)


def test_marker_rewritten_by_hand_is_refused(tmp_path: Path):
    """Маркер — не украшение: его содержимое сверяется перед каждым открытием."""
    root = AccountContextRoot(root=tmp_path)
    directory = root.prepare("acc-A")
    (directory / MARKER_NAME).write_text("acc-B", encoding="utf-8")
    with pytest.raises(CrossAccountViolation) as exc:
        root.assert_owned("acc-A", directory)
    assert exc.value.actual == "acc-B"


@posix_only
def test_context_directory_is_private(tmp_path: Path):
    root = AccountContextRoot(root=tmp_path)
    directory = root.prepare("acc-A")
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / MARKER_NAME).stat().st_mode) == 0o600
    root.assert_private(directory)


@posix_only
def test_widened_permissions_are_refused(tmp_path: Path):
    """Каталог с сессией, читаемый другими пользователями машины, — чужой вход."""
    root = AccountContextRoot(root=tmp_path)
    directory = root.prepare("acc-A")
    os.chmod(directory, 0o755)
    with pytest.raises(PermissionError):
        root.assert_private(directory)


def test_known_accounts_lists_owners_only(tmp_path: Path):
    root = AccountContextRoot(root=tmp_path)
    root.prepare("acc-A")
    root.prepare("acc-B")
    (tmp_path / "мусор").mkdir()
    assert root.known_accounts() == ["acc-A", "acc-B"]


def test_permissions_cannot_be_widened_by_configuration():
    """Права каталога не берутся из окружения: это не настройка, а граница."""
    config = BrowserConfig.from_env({"SF_BROWSER_CONTEXT_DIR_MODE": "0777",
                                     "SF_BROWSER_FALLBACK": "1"})
    assert config.context_dir_mode == 0o700
    assert config.enabled is True


# ------------------------------------------------------------------ сессия

def test_session_account_cannot_be_switched():
    """У объекта, которому можно переставить аккаунт, его рано или поздно
    переставят. Поэтому переставить нельзя."""
    dom = FixtureDom(feed_page())
    sess = session(dom, account_id="acc-A")
    with pytest.raises(AttributeError):
        sess.account_id = "acc-B"
    with pytest.raises(AttributeError):
        sess.expected_identity = "chuzhoj"
    assert sess.account_id == "acc-A"


def test_session_requires_an_expected_identity():
    dom = FixtureDom(feed_page())
    with pytest.raises(ValueError):
        session(dom, identity="")


# ------------------------------------------------------------------ воркер

def test_envelope_for_another_account_is_refused_before_sending():
    """Первая проверка: чужой конверт не попадает в очередь вовсе."""
    handle = BrowserWorkerHandle(account_id="acc-A")
    with pytest.raises(CrossAccountViolation):
        handle.call("ping", account_id="acc-B")


def test_worker_loop_refuses_a_foreign_envelope():
    """Вторая проверка, внутри воркера. Ловит то, что мимо первой прошло."""
    request = WorkerRequest(id="r1", account_id="acc-B", op="ping")
    with pytest.raises(CrossAccountViolation):
        guard_account("acc-A", request)


def test_secret_value_cannot_cross_the_process_boundary():
    with pytest.raises(SecretInTransit):
        guard_payload({"password": "ochen-sekretnyj-parol-2026"})
    with pytest.raises(SecretInTransit):
        guard_payload({"form": {"api_key": "sf-live-0123456789"}})


def test_secret_reference_may_cross_the_boundary():
    guard_payload({"secret_ref": "vault://login", "vault_ref": "vault://login"})


def test_secret_inside_a_list_cannot_cross_the_boundary():
    """Нагрузка редко бывает плоской: шаги входа приходят списком."""
    with pytest.raises(SecretInTransit):
        guard_payload({"steps": [{"op": "fill", "password": "parol-2026"}]})


@pytest.mark.skipif(sys.platform == "win32", reason="NOT RUN: spawn на Windows не проверен")
def test_running_worker_refuses_a_foreign_envelope_put_into_its_queue(tmp_path: Path):
    """Настоящий процесс, конверт мимо отправителя — и всё равно отказ.

    Это та самая четвёртая опора: у процесса ровно один аккаунт, и подложенный
    в очередь конверт не делает его двухаккаунтным.
    """
    handle = BrowserWorkerHandle(account_id="acc-A",
                                 config=BrowserConfig(context_root=tmp_path)).start()
    try:
        own = handle.call("context", timeout=60)
        assert own.ok, own.error
        assert own.payload["context_dir"].endswith(account_slug("acc-A"))

        foreign = handle.send_raw(WorkerRequest(id="r9", account_id="acc-B",
                                                op="context"), timeout=60)
        assert not foreign.ok
        assert foreign.error_type == "CrossAccountViolation"

        # И каталога чужого аккаунта после этого не появилось.
        assert not (tmp_path / account_slug("acc-B")).exists()
    finally:
        handle.stop()
