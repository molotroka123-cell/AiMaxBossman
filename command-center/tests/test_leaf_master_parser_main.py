"""authored_by_lane jeffb: ``python -m bcc.pit.master_parser`` entry (master_parser.__main__)."""
from __future__ import annotations

import pytest

from bcc.pit.master_parser import __main__ as mp_main


def test_help_exits_zero_and_names_the_command(capsys):
    with pytest.raises(SystemExit) as e:
        mp_main.main(["--help"])
    assert e.value.code == 0
    assert "master-parse" in capsys.readouterr().out


def test_unconfigured_data_dir_returns_2_with_clear_message(tmp_path, capsys):
    rc = mp_main.main(["--data-dir", str(tmp_path), "--status"])
    assert rc == 2
    assert "PIT" in capsys.readouterr().err
    assert not (tmp_path / "pit-v1.7").exists() or not any((tmp_path / "pit-v1.7").glob("*.json*"))


def test_leading_flag_means_master_parse_default(tmp_path, capsys):
    # a flag-first argv is prefixed with the master-parse command, so an unknown --since format is rejected by cli,
    # but only after settings resolve; unconfigured dir therefore still reports the PIT error (rc 2), never crashes
    assert mp_main.main(["--data-dir", str(tmp_path), "--since", "bogus"]) == 2
