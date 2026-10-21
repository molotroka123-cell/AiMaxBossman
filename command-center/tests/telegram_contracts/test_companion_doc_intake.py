"""/doc in the pult: the owner's document lands in the local drop folder <data dir>/inbox-docs.

tools/doc_intake (watch mode) parses it into the knowledge library on the PC. Negative controls:
a participant's /doc does nothing; the bytes never enter the pult Store; executables are refused
before any download; the same file twice is one file; a different file with the same name is kept.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bcc.telegram_companion import service
from bcc.telegram_companion.config import CompanionError
from bcc.telegram_companion.secret_intake import SecretIntakeManager

OWNER = SimpleNamespace(key="1:1", chat_id=1, role="owner")
GUEST = SimpleNamespace(key="2:2", chat_id=2, role="participant")
PDF = b"%PDF-1.4\n% synthetic owner document for the drop folder\n"


class FakeStore:
    def __init__(self):
        self.acked, self.ingested = [], []

    def acknowledge_without_body(self, update_id):
        self.acked.append(update_id)
        return True

    def ingest(self, update_id, who, body):
        self.ingested.append((update_id, who, body))
        return False

    def lane(self, body):
        return "chat"


class FakeTelegram:
    def __init__(self, files):
        self.sent, self.files, self.fetched = [], files, []

    async def send(self, person, text, **_):
        self.sent.append(text)
        return 1

    async def fetch_file(self, file_id, max_bytes=0):
        self.fetched.append((file_id, max_bytes))
        data = self.files[file_id]
        if len(data) > max_bytes:
            raise CompanionError("IMAGE_TOO_LARGE")
        return data


def companion(person, tmp_path, files=None):
    c = object.__new__(service.Companion)
    c.store, c.telegram = FakeStore(), FakeTelegram(files or {})
    c.secret_intake = SecretIntakeManager()
    c.wake = {}
    c.authorized = lambda message: person
    c.settings = SimpleNamespace(core_data_dir=str(tmp_path))
    return c


def doc_update(name, file_id="F1", caption="/doc", uid=9, size=100):
    return {"update_id": uid, "message": {"message_id": 90, "caption": caption,
                                          "document": {"file_id": file_id, "file_name": name, "file_size": size},
                                          "chat": {"id": 1, "type": "private"}, "from": {"id": 1}}}


def inbox(tmp_path):
    folder = tmp_path / "inbox-docs"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def test_owner_document_lands_in_drop_folder_and_never_in_the_store(tmp_path):
    c = companion(OWNER, tmp_path, {"F1": PDF})
    asyncio.run(c.ingest(doc_update("Отчёт Q3.pdf")))
    assert inbox(tmp_path) == ["Отчёт Q3.pdf"]
    assert (tmp_path / "inbox-docs" / "Отчёт Q3.pdf").read_bytes() == PDF
    assert c.store.acked == [9] and c.store.ingested == []
    assert "положен в папку приёма" in c.telegram.sent[-1] and "synthetic" not in c.telegram.sent[-1]
    assert c.telegram.fetched == [("F1", service.DOC_MAX_BYTES)]


def test_same_file_twice_is_one_file_and_same_name_other_bytes_is_kept(tmp_path):
    c = companion(OWNER, tmp_path, {"F1": PDF, "F2": PDF + b"v2"})
    asyncio.run(c.ingest(doc_update("a.pdf")))
    asyncio.run(c.ingest(doc_update("a.pdf", uid=10)))
    assert inbox(tmp_path) == ["a.pdf"] and "уже лежит" in c.telegram.sent[-1]
    asyncio.run(c.ingest(doc_update("a.pdf", file_id="F2", uid=11)))
    assert inbox(tmp_path) == ["a (2).pdf", "a.pdf"]


def test_reply_doc_to_an_earlier_document(tmp_path):
    c = companion(OWNER, tmp_path, {"F9": PDF})
    upd = {"update_id": 12, "message": {"message_id": 91, "text": "/doc", "chat": {"id": 1, "type": "private"},
                                        "from": {"id": 1},
                                        "reply_to_message": {"message_id": 5, "document": {
                                            "file_id": "F9", "file_name": "contract.pdf"}}}}
    asyncio.run(c.ingest(upd))
    assert inbox(tmp_path) == ["contract.pdf"]


def test_executable_or_path_tricks_are_refused_before_download(tmp_path):
    c = companion(OWNER, tmp_path, {"F1": b"MZ"})
    asyncio.run(c.ingest(doc_update("setup.exe")))
    assert "не принят" in c.telegram.sent[-1] and c.telegram.fetched == [] and inbox(tmp_path) == []
    asyncio.run(c.ingest(doc_update("..\\..\\evil.ps1", uid=10)))
    assert c.telegram.fetched == [] and inbox(tmp_path) == []
    assert service.doc_inbox_name("../../x/Отчёт.PDF") == "Отчёт.pdf"
    assert service.doc_inbox_name("con.txt") == "doc-con.txt"


def test_too_large_for_telegram_is_refused_with_the_reason(tmp_path):
    c = companion(OWNER, tmp_path, {"F1": PDF})
    asyncio.run(c.ingest(doc_update("big.pdf", size=service.DOC_MAX_BYTES + 1)))
    assert "20 МБ" in c.telegram.sent[-1] and c.telegram.fetched == [] and inbox(tmp_path) == []


def test_participant_doc_is_not_a_command(tmp_path):
    c = companion(GUEST, tmp_path, {"F1": PDF})
    asyncio.run(c.ingest(doc_update("a.pdf")))
    assert inbox(tmp_path) == [] and c.telegram.sent == [] and c.telegram.fetched == []
