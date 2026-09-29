from __future__ import annotations

from bcc.telegram_calls.stopflag import StopFlag


def test_set_clear_and_persistence_across_instances(tmp_path):
    f = StopFlag(tmp_path)
    assert f.is_set() is False
    f.set("owner")
    assert f.is_set() is True and StopFlag(tmp_path).is_set() is True        # survives a "restart"
    f.clear()
    assert StopFlag(tmp_path).is_set() is False
    f.clear()                                                                # idempotent


def test_global_computer_stop_blocks_and_is_never_cleared_by_us(tmp_path):
    (tmp_path / "computer").mkdir()
    (tmp_path / "computer" / "STOP").write_text("owner\n1\n", encoding="utf-8")
    f = StopFlag(tmp_path)
    assert f.is_set() is True and f.call_stop_set() is False and f.global_stop_set() is True
    f.clear()
    assert f.is_set() is True                                               # resume of calls does not resume the computer
    assert (tmp_path / "computer" / "STOP").is_file()


def test_no_global_stop_means_clear_flag_is_not_set(tmp_path):
    (tmp_path / "computer").mkdir()
    f = StopFlag(tmp_path)
    assert f.is_set() is False and f.info() == {"call_stop": False, "global_stop": False}
