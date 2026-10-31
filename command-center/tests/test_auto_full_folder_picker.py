"""Owner 31.10: Auto Full offers a folder picker; Python / installed programs / home / drive root are refused."""
import sys
from pathlib import Path

import pytest

from bcc.approval_scope import validate_auto_full_folder


def test_python_install_and_programs_are_refused():
    with pytest.raises(PermissionError):
        validate_auto_full_folder(Path(sys.base_prefix) / "Lib")


@pytest.mark.parametrize("bad", [str(Path.home()), str(Path.home().parent), Path.home().anchor])
def test_home_profile_root_and_drive_root_are_refused(bad):
    with pytest.raises(PermissionError):
        validate_auto_full_folder(bad)


def test_a_normal_work_folder_is_accepted(tmp_path):
    work = tmp_path / "project"; work.mkdir()
    assert validate_auto_full_folder(str(work)) == work.resolve()


def test_mobile_card_has_a_folder_picker_not_a_free_default():
    ui = Path(__file__).resolve().parents[2] / "apps" / "bossman-mobile" / "bossmobile" / "ui"
    html = (ui / "index.html").read_text(encoding="utf-8")
    js = (ui / "app.js").read_text(encoding="utf-8")
    assert 'id="af-folder-select"' in html and r"C:\Users\asd\Bossman\wt-bossman-20-argon" not in html
    assert "Другая папка…" in js and "(Programs|Python" in js
