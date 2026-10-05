"""`bossman chat` and the web chat write the SAME session file
(`<data_dir>/terminal/sessions/<id>.json`). The CLI keeps the session in memory
and rewrites the whole file on every save, so a turn the web chat appended to the
same thread in the meantime used to disappear on the CLI's next save.

Now a CLI save first merges turns another writer added (file order, web-only keys
such as `attachments` kept), puts its own new turn after them, keeps its own
/compact fields, and does not bring back turns the CLI itself dropped from memory.
The web side is driven through its real writer, `chat_threads._append_turn_sync`.
"""
from __future__ import annotations

import json
from pathlib import Path

from bcc.features import chat_threads as ct
from bcc.terminal_cli.chat import Session


def _web_append(path: Path, session_id: str, task_id: int, text: str, **extra) -> None:
    """What POST /api/chat/threads/{id}/send writes (step 3: the turn, then the run)."""
    ct._append_turn_sync(path, session_id, {"task_id": task_id, "text": text,
                                            "at": "2026-09-30T12:00:00", **extra})


def _on_disk(session: Session) -> dict:
    return json.loads(session.path.read_text(encoding="utf-8"))


def _ids(turns: list) -> list:
    return [t["task_id"] for t in turns]


def test_a_web_turn_survives_the_cli_rewrite_in_file_order(tmp_path):
    cli = Session.open(tmp_path, None)
    cli.add(1, "из терминала")
    attachment = {"id": "0123456789abcdef012345", "name": "notes.txt", "kind": "text"}
    _web_append(cli.path, cli.id, 2, "из окна", attachments=[attachment])
    cli.add(3, "снова терминал")

    data = _on_disk(cli)
    assert _ids(data["turns"]) == [1, 2, 3], "ход окна не потерян и стоит между ходами терминала"
    assert data["turns"][1]["text"] == "из окна" and data["turns"][1]["attachments"] == [attachment]
    assert _ids(cli.turns) == [1, 2, 3], "терминал видит ход окна и в памяти (контекст следующего хода)"
    assert set(data) == {"id", "turns"}, "формат файла CLI не меняется"


def test_a_session_reopened_with_session_id_keeps_web_turns(tmp_path):
    first = Session.open(tmp_path, None)
    first.add(1, "a")
    cli = Session.open(tmp_path, first.id)             # `bossman chat --session <id>`
    _web_append(cli.path, cli.id, 2, "из окна")
    _web_append(cli.path, cli.id, 4, "ещё из окна")
    cli.add(3, "c")
    assert _ids(_on_disk(cli)["turns"]) == [1, 2, 4, 3]
    assert [t["text"] for t in _on_disk(cli)["turns"]] == ["a", "из окна", "ещё из окна", "c"]


def test_turns_the_cli_dropped_are_not_brought_back_but_new_web_turns_are(tmp_path):
    cli = Session.open(tmp_path, None)
    cli.add(1, "a")
    cli.add(2, "b")
    cli.turns = []                                     # as /cost's test does: the CLI dropped them itself
    _web_append(cli.path, cli.id, 5, "из окна")
    cli.add(3, "c")
    assert _ids(_on_disk(cli)["turns"]) == [5, 3]
    assert _ids(cli.turns) == [5, 3]


def test_compact_save_keeps_the_web_turn_after_the_compaction_point(tmp_path):
    cli = Session.open(tmp_path, None)
    cli.add(1, "a")
    cli.add(2, "b")
    _web_append(cli.path, cli.id, 3, "из окна, пока шёл /compact")
    cli.compacted("резюме", 99)

    data = _on_disk(cli)
    assert _ids(data["turns"]) == [1, 2, 3]
    assert (data["summary"], data["compacted_at_turn"], data["compact_tasks"]) == ("резюме", 2, [99])
    assert _ids(cli.live_turns()) == [3], "резюме не покрывает ход окна — он остаётся в контексте"

    cli.add(4, "d")
    data = _on_disk(cli)
    assert _ids(data["turns"]) == [1, 2, 3, 4]
    assert (data["summary"], data["compacted_at_turn"]) == ("резюме", 2), "поля /compact — из памяти CLI"


def test_only_new_well_formed_turns_are_merged(tmp_path):
    cli = Session.open(tmp_path, None)
    cli.add(1, "a")
    raw = _on_disk(cli)
    raw["turns"] += ["мусор", {"text": "без задачи"}, {"task_id": True, "text": "bool"},
                     {"task_id": "7", "text": "строка"}, {"task_id": 1, "text": "дубль"},
                     {"task_id": 4, "text": "из окна"}]
    cli.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    cli.add(2, "b")
    assert [(t["task_id"], t["text"]) for t in _on_disk(cli)["turns"]] == [(1, "a"), (4, "из окна"), (2, "b")]


def test_an_unreadable_file_does_not_break_the_cli_save(tmp_path):
    cli = Session.open(tmp_path, None)
    cli.add(1, "a")
    cli.path.write_text("{не json", encoding="utf-8")   # e.g. edited by hand
    cli.add(2, "b")                                    # no crash in the middle of a turn
    assert _ids(_on_disk(cli)["turns"]) == [1, 2]
