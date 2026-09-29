"""Jeff 1.1 doctor identity: own SHA vs backend SHA, data path, pollers,
scheduler tasks. Fakes only: no network, no Task Scheduler, no processes."""
from __future__ import annotations

import json
from pathlib import Path

from bcc.build_identity import data_dir_fingerprint
from bcc.pit import cli as pit_cli
from bcc.pit import doctor_identity as di
from bcc.pit.version import JEFF_VERSION

from .test_pit_runtime import make_settings

SHA_A = "a" * 40
SHA_B = "b" * 40
OWN = {"build_sha": SHA_A, "source_identity": "PASS"}
GOOD_HOME = "C:\\Bossman\\app\\BOSSMAN-Windows-x64-aaaaaaaaaaaa"
OTHER_HOME = "C:\\Bossman\\app\\BOSSMAN-Windows-x64-bbbbbbbbbbbb"


def run(tmp_path, *, backend=None, tasks=None, pollers=0, beat=None, own=OWN, settings=None,
        code_dir="C:/x/BOSSMAN-Windows-x64-aaaaaaaaaaaa/runtime/bcc"):
    settings = settings or make_settings(tmp_path)
    if backend == "ok":
        backend = {"build_sha": SHA_A, "data_dir_fingerprint": data_dir_fingerprint(tmp_path)}
    rows = di.identity_checks(
        settings, tmp_path / "pit-v1.7", tmp_path, own=own,
        fetch_identity=lambda url: backend, read_tasks=lambda: tasks,
        process_count=lambda: pollers, heartbeat_read=lambda home: beat, code_dir=code_dir)
    return {row["check"]: row for row in rows}


def task(home: str) -> dict:
    return {"command": home + "\\runtime\\pythonw.exe", "arguments": "-I x launch", "workdir": home}


def test_version_constant_is_one_place():
    assert JEFF_VERSION == "1.1"


def test_own_and_backend_sha_match_passes_and_prints_data_path(tmp_path):
    rows = run(tmp_path, backend="ok")
    assert rows["own_build"]["status"] == "PASS" and SHA_A[:12] in rows["own_build"]["detail"]
    assert JEFF_VERSION in rows["own_build"]["detail"]
    assert rows["backend_identity"]["status"] == "PASS"
    assert str(tmp_path) in rows["data_path"]["detail"]


def test_backend_sha_mismatch_is_blocked_with_remedy(tmp_path):
    rows = run(tmp_path, backend={"build_sha": SHA_B,
                                  "data_dir_fingerprint": data_dir_fingerprint(tmp_path)})
    row = rows["backend_identity"]
    assert row["status"] == "BLOCKED" and row["remedy"]
    assert SHA_A[:12] in row["detail"] and SHA_B[:12] in row["detail"]


def test_backend_with_foreign_data_dir_is_blocked(tmp_path):
    rows = run(tmp_path, backend={"build_sha": SHA_A,
                                  "data_dir_fingerprint": data_dir_fingerprint(tmp_path / "other")})
    assert rows["backend_identity"]["status"] == "BLOCKED"
    assert "data directory" in rows["backend_identity"]["detail"]


def test_backend_without_data_fingerprint_or_sha_is_not_a_pass(tmp_path):
    assert run(tmp_path, backend={"build_sha": SHA_A})["backend_identity"]["status"] == "BLOCKED"
    assert run(tmp_path, backend={"build_sha": None,
                                  "data_dir_fingerprint": data_dir_fingerprint(tmp_path)}
               )["backend_identity"]["status"] == "BLOCKED"


def test_unreachable_backend_is_red_not_pass(tmp_path):
    row = run(tmp_path, backend=None)["backend_identity"]
    assert row["status"] == "FAIL" and row["remedy"]


def test_unproven_own_build_is_blocked(tmp_path):
    rows = run(tmp_path, backend="ok",
               own={"build_sha": None, "source_identity": "SOURCE_IDENTITY_UNKNOWN"})
    assert rows["own_build"]["status"] == "BLOCKED"
    assert rows["backend_identity"]["status"] == "BLOCKED"


def test_more_than_one_poller_is_blocked(tmp_path):
    assert run(tmp_path, backend="ok", pollers=2)["pollers"]["status"] == "BLOCKED"
    assert run(tmp_path, backend="ok", pollers=1)["pollers"]["status"] == "PASS"
    assert run(tmp_path, backend="ok", pollers=-1)["pollers"]["status"] == "SKIP"


def test_running_jeff_from_another_build_is_blocked(tmp_path):
    beat = {"availability": "up", "build_sha": SHA_B, "tts": {"available": True, "engine": "piper"},
            "stt": {"available": False, "engine": "local"}}
    rows = run(tmp_path, backend="ok", beat=beat)
    assert rows["running_jeff_build"]["status"] == "BLOCKED"
    assert "tts=piper:ok" in rows["voice"]["detail"]


def test_scheduled_tasks_missing_or_split_or_other_build(tmp_path):
    good = {name: task(GOOD_HOME) for name in di.TASK_NAMES}
    rows = run(tmp_path, backend="ok", tasks=good)
    assert rows["scheduled_tasks"]["status"] == "PASS"
    assert rows["scheduler_build"]["status"] == "PASS"

    missing = dict(good, **{"BossmanOne-2-Jeff": None})
    assert run(tmp_path, backend="ok", tasks=missing)["scheduled_tasks"]["status"] == "FAIL"

    split = dict(good, **{"BossmanOne-3-Companion": task(OTHER_HOME)})
    row = run(tmp_path, backend="ok", tasks=split)["scheduled_tasks"]
    assert row["status"] == "BLOCKED" and row["remedy"]

    other = {name: task(OTHER_HOME) for name in di.TASK_NAMES}
    assert run(tmp_path, backend="ok", tasks=other)["scheduler_build"]["status"] == "BLOCKED"


def test_no_scheduler_is_skip_not_pass(tmp_path):
    assert run(tmp_path, backend="ok", tasks=None)["scheduled_tasks"]["status"] == "SKIP"


def test_task_xml_is_parsed_from_schtasks_output():
    xml = ('<?xml version="1.0" encoding="UTF-16"?><Task xmlns="http://schemas.microsoft.com/windows/'
           '2004/02/mit/task"><Actions><Exec><Command>C:\\h\\runtime\\pythonw.exe</Command>'
           '<Arguments>-I t launch</Arguments><WorkingDirectory>C:\\h</WorkingDirectory></Exec>'
           '</Actions></Task>')
    parsed = di.parse_task_xml(di._decode(xml.encode("utf-16")))
    assert parsed == {"command": "C:\\h\\runtime\\pythonw.exe", "arguments": "-I t launch",
                      "workdir": "C:\\h"}


def test_status_shows_version_and_build(tmp_path, capsys):
    from bcc.pit.config import config_path
    pit_cli.cmd_status(config_path(tmp_path))
    out = json.loads(capsys.readouterr().out)
    assert out["jeff_version"] == JEFF_VERSION and "build_sha" in out


def test_heartbeat_carries_version_and_build(tmp_path):
    from bcc.pit.heartbeat import Heartbeat
    snap = Heartbeat(Path(tmp_path), "telegram").snapshot()
    assert snap["jeff_version"] == JEFF_VERSION and "build_sha" in snap


def test_data_dir_fingerprint_is_short_and_not_a_path(tmp_path):
    assert len(data_dir_fingerprint(tmp_path)) == 12
    assert str(tmp_path) not in data_dir_fingerprint(tmp_path)
