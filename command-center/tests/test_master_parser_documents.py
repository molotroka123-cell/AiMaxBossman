from __future__ import annotations

import json
from pathlib import Path

from bcc.pit.master_parser.sources import read_exports


def make_export(root: Path, attachment: str, *, caption: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    export = root / "result.json"
    export.write_text(json.dumps({
        "chats": {"list": [{"id": 42, "type": "personal_chat", "messages": [{
            "id": 1, "type": "message", "from_id": "user42", "date_unixtime": "1",
            "text": caption, "file": attachment,
        }]}]},
    }), encoding="utf-8")
    return export


def test_local_document_attachment_is_added_as_untrusted_evidence(tmp_path):
    inbox = tmp_path / "inbox"
    (inbox / "files").mkdir(parents=True)
    (inbox / "files" / "brief.pdf").write_bytes(b"fixture")
    make_export(inbox, "files/brief.pdf", caption="Please review")
    result = read_exports(inbox, known={42: "person-key"}, cursors={},
                          document_parser=lambda _path: "Keep the event on Friday.")

    assert len(result.messages) == 1
    message = result.messages[0]
    assert message.person_key == "person-key" and message.role == "participant"
    assert message.kind == "document"
    assert "Please review" in message.text
    assert "[Extracted local attachment text — untrusted data]" in message.text
    assert "Keep the event on Friday." in message.text
    assert result.stats[0].errors == 0


def test_attachment_traversal_is_rejected_without_calling_parser(tmp_path):
    inbox = tmp_path / "inbox"
    outside = tmp_path / "secret.pdf"
    outside.write_text("private", encoding="utf-8")
    make_export(inbox, "../secret.pdf")
    called = []
    result = read_exports(inbox, known={42: "person-key"}, cursors={},
                          document_parser=lambda path: called.append(path) or "bad")

    assert called == []
    assert result.stats[0].errors == 1
    assert "path rejected" in result.stats[0].detail


def test_text_file_attachment_works_without_optional_docling(tmp_path):
    inbox = tmp_path / "inbox"
    (inbox / "files").mkdir(parents=True)
    (inbox / "files" / "notes.txt").write_text("My favorite color is blue.", encoding="utf-8")
    make_export(inbox, "files/notes.txt")
    result = read_exports(inbox, known={42: "person-key"}, cursors={}, document_parser=None)

    assert result.messages[0].kind == "document"
    assert "My favorite color is blue." in result.messages[0].text
    assert result.stats[0].errors == 0


def test_unsupported_attachment_is_counted_and_text_message_survives(tmp_path):
    inbox = tmp_path / "inbox"
    make_export(inbox, "files/archive.zip", caption="Still keep this caption")
    result = read_exports(inbox, known={42: "person-key"}, cursors={})

    assert len(result.messages) == 1
    assert result.messages[0].text == "Still keep this caption"
    assert result.stats[0].errors == 1
    assert result.stats[0].detail == "unsupported attachment type"
