"""W4: ярлык BOSSMAN уезжал в несуществующую папку на профиле с OneDrive.

`windows_targets` считала рабочим столом `%USERPROFILE%\\Desktop`, а `install`
делала `mkdir(parents=True)` на родителя цели. На Windows 11 с учётной записью
Microsoft (дефолт при первой настройке) рабочий стол перенесён в
`%USERPROFILE%\\OneDrive\\Desktop`: код создавал ПУСТУЮ `C:\\Users\\<u>\\Desktop`,
писал в неё BOSSMAN.lnk, возвращал «успех» — и владелец не видел на столе
ничего. Никакой ошибки при этом не было: молчаливый ложный успех.

Windows здесь симулируется: `system="Windows"` (в `install` он и так
инъектируется), `USERPROFILE` в tmp, реальный стол — подставленный резолвер
вместо known-folder API. Что не проверяется на Linux: сам вызов
SHGetKnownFolderPath/PowerShell — их возвращаемое значение видно только на
настоящей Windows.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc import desktop_install


@pytest.fixture
def win_home(tmp_path, monkeypatch):
    """Профиль Windows 11 с OneDrive: настоящий стол — внутри OneDrive."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    real = tmp_path / "OneDrive" / "Desktop"
    real.mkdir(parents=True)
    return tmp_path, real


def test_desktop_resolver_wins_over_userprofile_guess(win_home):
    home, real = win_home
    got = desktop_install.windows_desktop_dir(home, resolver=lambda: str(real))
    assert got == real


def test_desktop_falls_back_to_userprofile_when_resolver_silent(win_home):
    """Резолвер не ответил (не Windows, урезанный PowerShell) — старое поведение."""
    home, _ = win_home
    (home / "Desktop").mkdir()
    assert desktop_install.windows_desktop_dir(home, resolver=lambda: None) == home / "Desktop"


def test_desktop_resolver_failure_is_not_fatal(win_home):
    """Падение резолвера не должно валить установку ярлыка целиком."""
    home, _ = win_home
    (home / "Desktop").mkdir()

    def boom():
        raise OSError("SHGetKnownFolderPath недоступен")

    assert desktop_install.windows_desktop_dir(home, resolver=boom) == home / "Desktop"


def test_install_writes_to_real_desktop_not_phantom(win_home):
    """Главное: ярлык уходит в настоящий стол, фантомная папка не создаётся."""
    home, real = win_home
    calls: list[list[str]] = []
    spec = desktop_install.build_spec(executable="python.exe", workdir=home)
    created = desktop_install.install(
        spec, home=home, system="Windows",
        runner=lambda argv: calls.append(argv) or 0,
        desktop_resolver=lambda: str(real))

    assert created[0] == real / "BOSSMAN.lnk"
    assert not (home / "Desktop").exists(), "создана фантомная папка рабочего стола"
    assert str(real / "BOSSMAN.lnk") in calls[0][-1]


def test_install_never_creates_the_desktop_directory(win_home):
    """Даже когда стол не найден, каталог под него не выдумывается."""
    home, _ = win_home
    phantom = home / "Desktop"
    spec = desktop_install.build_spec(executable="python.exe", workdir=home)
    created = desktop_install.install(
        spec, home=home, system="Windows",
        runner=lambda argv: 0,
        desktop_resolver=lambda: str(phantom))

    assert not phantom.exists()
    # ярлык в меню «Пуск» всё равно ставится — это наш каталог, его создать можно
    assert [p.name for p in created] == ["BOSSMAN.lnk"]
    assert "Start Menu" in str(created[0])


def test_install_warns_when_desktop_missing(win_home):
    """Пропуск стола должен быть ВИДЕН, а не тихо превратиться в «успех»."""
    home, _ = win_home
    spec = desktop_install.build_spec(executable="python.exe", workdir=home)
    with pytest.warns(UserWarning, match="рабоч"):
        desktop_install.install(spec, home=home, system="Windows",
                                runner=lambda argv: 0,
                                desktop_resolver=lambda: str(home / "Desktop"))


def test_uninstall_uses_the_same_desktop_resolution(win_home):
    home, real = win_home
    (real / "BOSSMAN.lnk").write_text("x", encoding="utf-8")
    removed = desktop_install.uninstall(home=home, system="Windows",
                                        desktop_resolver=lambda: str(real))
    assert real / "BOSSMAN.lnk" in removed
    assert not (real / "BOSSMAN.lnk").exists()


def test_known_folder_probe_is_inert_off_windows():
    """Пробы должны быть безопасны на Linux: молчат, а не бросают."""
    assert desktop_install._desktop_via_known_folder() is None
    assert desktop_install._desktop_via_powershell() is None


def test_linux_and_macos_targets_untouched(tmp_path):
    """Никакого влияния на не-Windows: пути прежние."""
    assert desktop_install.linux_targets(tmp_path)[1] == tmp_path / "Desktop" / "bossman.desktop"
    assert desktop_install.macos_targets(tmp_path) == [tmp_path / "Desktop" / "BOSSMAN.command"]
