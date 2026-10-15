"""/manual in the pult: the owner's document becomes Jeff's local-only methodology note without Claude.

Negative controls: a participant's /manual does nothing; the text is never stored in the inbox or echoed back;
an unsupported file is refused with a reason.
"""
from __future__ import annotations

import asyncio
import io
import zipfile
from types import SimpleNamespace

from bcc.pit import jeff_manual
from bcc.telegram_companion import service
from bcc.telegram_companion.secret_intake import SecretIntakeManager

OWNER = SimpleNamespace(key="1:1", chat_id=1, role="owner")
GUEST = SimpleNamespace(key="2:2", chat_id=2, role="participant")
BODY = "Секретная-методичка-текст-777: отвечай как тренер."


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
        self.sent, self.files = [], files

    async def send(self, person, text, **_):
        self.sent.append(text)
        return 1

    async def fetch_file(self, file_id, max_bytes=0):
        return self.files[file_id]


def companion(person, tmp_path, files=None):
    c = object.__new__(service.Companion)
    c.store, c.telegram = FakeStore(), FakeTelegram(files or {})
    c.secret_intake = SecretIntakeManager()
    c.wake = {}
    c.authorized = lambda message: person
    c.settings = SimpleNamespace(core_data_dir=str(tmp_path))
    return c


def doc_update(name, file_id="F1", caption="/manual 45", uid=9):
    return {"update_id": uid, "message": {"message_id": 90, "caption": caption,
                                          "document": {"file_id": file_id, "file_name": name, "file_size": 100},
                                          "chat": {"id": 1, "type": "private"}, "from": {"id": 1}}}


def text_update(text, uid=10):
    return {"update_id": uid, "message": {"message_id": 91, "text": text,
                                          "chat": {"id": 1, "type": "private"}, "from": {"id": 1}}}


def docx_bytes(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                   f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


def test_owner_document_becomes_the_manual_and_is_never_echoed(tmp_path):
    c = companion(OWNER, tmp_path, {"F1": BODY.encode("utf-8")})
    asyncio.run(c.ingest(doc_update("m.txt")))
    live = jeff_manual.active_manual(tmp_path)
    assert live and live["text"] == BODY
    assert c.store.acked == [9] and c.store.ingested == []
    assert "45 мин" in c.telegram.sent[-1] and "Секретная" not in " ".join(c.telegram.sent)
    asyncio.run(c.ingest(text_update("/manual")))
    assert "активна" in c.telegram.sent[-1] and "Секретная" not in c.telegram.sent[-1]
    asyncio.run(c.ingest(text_update("/manual off", uid=11)))
    assert jeff_manual.active_manual(tmp_path) is None


def test_docx_is_read(tmp_path):
    c = companion(OWNER, tmp_path, {"F2": docx_bytes("Тренерский стиль")})
    asyncio.run(c.ingest(doc_update("m.docx", file_id="F2", caption="/методичка")))
    assert jeff_manual.active_manual(tmp_path)["text"] == "Тренерский стиль"


def test_unsupported_file_is_refused(tmp_path):
    c = companion(OWNER, tmp_path, {"F3": b"%PDF-1.7"})
    asyncio.run(c.ingest(doc_update("m.pdf", file_id="F3")))
    assert jeff_manual.active_manual(tmp_path) is None
    assert "не принята" in c.telegram.sent[-1]


def test_participant_manual_is_not_a_command(tmp_path):
    c = companion(GUEST, tmp_path, {"F1": BODY.encode("utf-8")})
    asyncio.run(c.ingest(doc_update("m.txt")))
    assert jeff_manual.active_manual(tmp_path) is None and c.telegram.sent == []
