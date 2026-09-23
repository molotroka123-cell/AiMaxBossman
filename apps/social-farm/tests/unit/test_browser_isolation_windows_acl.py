"""OS-105 на Windows: приватность каталога аккаунта держится на ACL, не на chmod.

На NTFS `os.chmod` переключает только атрибут «только чтение», а `st_mode`
каталога всегда читается как 0o777. До исправления это давало сразу два
дефекта: каталог с сессией аккаунта оставался открытым всем, кому его открывает
унаследованный ACL, а `assert_private` сверял бессмысленные биты режима и
отвергал даже правильно закрытый каталог владельца.

Платформа симулируется подменой трёх швов модуля — признака Windows, имени
владельца и исполнителя icacls. Сам icacls здесь не запускается; его
настоящий вывод на машине владельца проверяет Windows CI.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from social_farm.browser import AccountContextRoot
from social_farm.browser import isolation
from social_farm.browser.isolation import MARKER_NAME

OWNER = "PC-OWNER\\владелец"


class FakeIcacls:
    """Исполнитель icacls: помнит ACL каждого пути и печатает его как icacls."""

    def __init__(self, inherited: list[str] | None = None,
                 fail_grant: bool = False, missing: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.marker_existed_at_grant: dict[str, bool] = {}
        self.acl: dict[str, list[str]] = {}
        self.inherited = inherited if inherited is not None else [
            "BUILTIN\\Users:(I)(OI)(CI)(RX)", "NT AUTHORITY\\SYSTEM:(I)(OI)(CI)(F)",
            f"{OWNER}:(I)(OI)(CI)(F)"]
        self.fail_grant = fail_grant
        self.missing = missing

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(list(argv))
        if self.missing:
            raise FileNotFoundError("icacls")
        assert argv[0] == "icacls"
        path = argv[1]
        if len(argv) == 2:
            aces = self.acl.get(path, self.inherited)
            pad = " " * len(path)
            lines = [f"{path} {aces[0]}"] + [f"{pad} {ace}" for ace in aces[1:]]
            return 0, "\n".join(lines) + "\n\nSuccessfully processed 1 files; " \
                "Failed processing 0 files\n"
        if self.fail_grant:
            return 5, "Access is denied."
        assert argv[2:4] == ["/inheritance:r", "/grant:r"]
        self.marker_existed_at_grant[path] = os.path.exists(
            os.path.join(path, MARKER_NAME))
        principal, _, rights = argv[4].partition(":")
        self.acl[path] = [f"{principal}:{rights}"]
        return 0, "processed file: " + path


@pytest.fixture
def windows(monkeypatch):
    fake = FakeIcacls()
    monkeypatch.setattr(isolation, "_on_windows", lambda: True, raising=False)
    monkeypatch.setattr(isolation, "_owner_principal", lambda: OWNER, raising=False)
    monkeypatch.setattr(isolation, "_run_icacls", fake, raising=False)
    return fake


def _grants(fake: FakeIcacls) -> dict[str, list[str]]:
    return {c[1]: c[2:] for c in fake.calls if len(c) > 2}


def test_prepare_on_windows_sets_an_owner_only_inheritable_acl(tmp_path: Path, windows):
    root = AccountContextRoot(root=tmp_path / "contexts")
    directory = root.prepare("acc-A")
    grants = _grants(windows)
    expected = ["/inheritance:r", "/grant:r", f"{OWNER}:(OI)(CI)F"]
    assert grants.get(str(directory)) == expected
    assert grants.get(str(tmp_path / "contexts")) == expected
    # Маркер пишется ПОСЛЕ закрытия каталога и наследует owner-only ACE.
    assert windows.marker_existed_at_grant[str(directory)] is False
    assert (directory / MARKER_NAME).read_text(encoding="utf-8") == "acc-A"


def test_owner_only_acl_is_private_even_though_ntfs_reports_mode_0777(
        tmp_path: Path, windows):
    root = AccountContextRoot(root=tmp_path / "contexts")
    directory = root.prepare("acc-A")
    os.chmod(directory, 0o777)          # так st_mode каталога видит Python на NTFS
    root.assert_private(directory)      # не бросает: решает ACL, а не биты режима


def test_negative_control_acl_open_to_other_users_is_refused_despite_mode_0700(
        tmp_path: Path, windows):
    root = AccountContextRoot(root=tmp_path / "contexts")
    directory = root.prepare("acc-A")
    os.chmod(directory, 0o700)
    windows.acl[str(directory)] = [f"{OWNER}:(OI)(CI)(F)",
                                   "BUILTIN\\Users:(I)(OI)(CI)(RX)"]
    with pytest.raises(PermissionError, match="BUILTIN\\\\Users"):
        root.assert_private(directory)


def test_negative_control_inherited_acl_that_was_never_narrowed_is_refused(
        tmp_path: Path, windows):
    root = AccountContextRoot(root=tmp_path / "contexts")
    directory = root.prepare("acc-A")
    del windows.acl[str(directory)]     # как каталог, созданный версией без ACL
    with pytest.raises(PermissionError):
        root.assert_private(directory)


def test_negative_control_failed_icacls_is_a_refusal_not_silence(
        tmp_path: Path, monkeypatch):
    fake = FakeIcacls(fail_grant=True)
    monkeypatch.setattr(isolation, "_on_windows", lambda: True, raising=False)
    monkeypatch.setattr(isolation, "_owner_principal", lambda: OWNER, raising=False)
    monkeypatch.setattr(isolation, "_run_icacls", fake, raising=False)
    root = AccountContextRoot(root=tmp_path / "contexts")
    with pytest.warns(UserWarning, match="icacls"):
        directory = root.prepare("acc-A")
    with pytest.raises(PermissionError):
        root.assert_private(directory)


def test_negative_control_missing_icacls_or_unknown_owner_fails_closed(
        tmp_path: Path, monkeypatch):
    monkeypatch.setattr(isolation, "_on_windows", lambda: True, raising=False)
    monkeypatch.setattr(isolation, "_owner_principal", lambda: OWNER, raising=False)
    monkeypatch.setattr(isolation, "_run_icacls", FakeIcacls(missing=True), raising=False)
    root = AccountContextRoot(root=tmp_path / "contexts")
    with pytest.warns(UserWarning):
        directory = root.prepare("acc-A")
    with pytest.raises(PermissionError):
        root.assert_private(directory)

    monkeypatch.setattr(isolation, "_owner_principal", lambda: None, raising=False)
    monkeypatch.setattr(isolation, "_run_icacls", FakeIcacls(), raising=False)
    with pytest.raises(PermissionError):
        root.assert_private(directory)


def test_parse_icacls_handles_multiline_output_and_deny_entries():
    path = "C:\\Users\\владелец\\contexts\\acc-A.1234"
    text = (f"{path} {OWNER}:(OI)(CI)(F)\n"
            f"{' ' * len(path)} Everyone:(DENY)(W)\n\n"
            "Successfully processed 1 files; Failed processing 0 files\n")
    aces = isolation._parse_icacls(text, path)
    assert aces == [(OWNER, "(OI)(CI)(F)"), ("Everyone", "(DENY)(W)")]
    # DENY не расширяет доступ — такой ACL приватен.
    assert isolation._acl_problems(aces, OWNER) == []
