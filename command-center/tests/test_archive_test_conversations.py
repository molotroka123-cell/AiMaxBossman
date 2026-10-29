"""TZ 2.5 M0 / Задача 0: test conversations leave the working list by metadata only, archive restores on a copy."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

from bcc.features import chat_archive

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("atc", ROOT / "tools" / "archive_test_conversations.py")
atc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(atc)

SECRET_BODY = "SYNTHETIC-CONTENT-MUST-NEVER-BE-PRINTED"


def _data(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_KEYS_DIR", str(tmp_path / "keys"))          # test key, never the owner's DPAPI key
    ddir = tmp_path / "data"
    (ddir / "terminal" / "sessions").mkdir(parents=True)
    meta = {"20261029-100000-aaaaaa": {"title": "PROOF chat attachments", "project": None},
            "20261029-100001-bbbbbb": {"title": "DEMO rehearsal", "project": None},
            "20261029-100002-cccccc": {"title": "Мой рабочий чат", "project": "nimba"},
            "20261029-100003-dddddd": {"title": None, "project": "tests"},
            "20261029-100004-eeeeee": {"title": "Протестируй мне сайт", "project": None}}     # a real owner chat
    (ddir / "terminal" / "threads-meta.json").write_text(json.dumps({"threads": meta}), encoding="utf-8")
    for tid in meta:
        (ddir / "terminal" / "sessions" / f"{tid}.json").write_text(json.dumps({"turns": [{"text": SECRET_BODY + tid}]}),
                                                                     encoding="utf-8")
    return ddir


def _fake_backend(ddir):
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=ddir))

    def api(method, path, body=None, **_):
        tid = path.split("/")[4]
        live = ddir / "terminal" / "sessions" / f"{tid}.json"
        target = ddir / "chat-archive" / f"{tid}.bcarchive"
        target.parent.mkdir(exist_ok=True)
        return chat_archive._archive_sync(svc, tid, live, target)       # the backend's real encrypt-verify-delete
    return api


def test_dry_run_selects_by_metadata_only_and_prints_no_content(tmp_path, monkeypatch, capsys):
    ddir = _data(tmp_path, monkeypatch)
    monkeypatch.setenv("BCC_DATA_DIR", str(ddir))
    assert atc.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    rep = json.loads(out)
    assert {c["id"] for c in rep["candidates"]} == {"20261029-100000-aaaaaa", "20261029-100001-bbbbbb", "20261029-100003-dddddd"}
    assert SECRET_BODY not in out and "Мой рабочий чат" not in out and "Протестируй" not in out
    assert "20261029-100004-eeeeee" not in out           # «Протестируй…» is an owner chat, not a test-tool prefix


def test_run_archives_only_test_chats_and_restore_on_a_copy_matches(tmp_path, monkeypatch):
    ddir = _data(tmp_path, monkeypatch)
    res = atc.run(ddir, tmp_path / "D-archive", _fake_backend(ddir))
    ids = {r["id"] for r in res["archived"]}
    assert ids == {"20261029-100000-aaaaaa", "20261029-100001-bbbbbb", "20261029-100003-dddddd"}
    assert res["failed"] == [] and res["remaining_test_marked"] == 0          # 0 test-marked chats left in the working list
    sessions = {p.stem for p in (ddir / "terminal" / "sessions").glob("*.json")}
    assert sessions == {"20261029-100002-cccccc", "20261029-100004-eeeeee"}  # owner chats untouched
    day = Path(res["dir"])
    assert (day / "ids.txt").read_text(encoding="utf-8").split() == sorted(ids)
    assert all(SECRET_BODY.encode() not in (day / f"{i}.bcarchive").read_bytes() for i in ids)   # encrypted on D:
    check = atc.verify_restore(day)
    assert check["checked"] == 3 and check["ok"] == 3
    assert sessions == {p.stem for p in (ddir / "terminal" / "sessions").glob("*.json")}         # restore never on live data


def test_a_tampered_archive_fails_the_restore_check(tmp_path, monkeypatch):
    ddir = _data(tmp_path, monkeypatch)
    res = atc.run(ddir, tmp_path / "D-archive", _fake_backend(ddir))
    day = Path(res["dir"])
    victim = day / f"{res['archived'][0]['id']}.bcarchive"
    blob = bytearray(victim.read_bytes())
    blob[-1] ^= 0xFF
    victim.write_bytes(bytes(blob))
    check = atc.verify_restore(day)
    assert check["ok"] == 2 and check["checked"] == 3


def test_pytest_never_writes_to_the_owners_live_data_dir():
    """M0 acceptance (4): the suite's data dir is a tmp dir, never %LOCALAPPDATA%\\Bossman\\CommandCenter."""
    live = (Path(os.environ.get("LOCALAPPDATA", "~")) / "Bossman" / "CommandCenter").resolve()
    active = Path(os.environ["BCC_DATA_DIR"]).resolve()
    assert live != active and live not in active.parents
    assert atc.data_dir().resolve() == active
