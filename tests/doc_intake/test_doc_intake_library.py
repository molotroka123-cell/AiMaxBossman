"""tools/doc_intake: accept → parse immediately → library (file manager) → memory notes.

Every fixture is SYNTHETIC and built in tmp_path (no owner documents). The fake
backend speaks the real /api/memory/config + /api/memory/index contract and
indexes with the real ``SQLiteMemoryBackend``, so page citations are checked
through the same chunker the live memory uses.

Run: python -m pytest tests/doc_intake -q
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from doc_intake import cli, fsutil, library, rules, watch  # noqa: E402
from doc_intake.config import ENV_BACKEND, ENV_INBOX, ENV_LIBRARY, ENV_VAULT  # noqa: E402
from doc_intake.library import Library, LibraryError, Settings  # noqa: E402
from doc_intake.memory import MemorySink  # noqa: E402

TOKEN = "t0k-test-owner"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for name in (ENV_VAULT, ENV_LIBRARY, ENV_INBOX, ENV_BACKEND, "BCC_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_lib(tmp_path: Path, sink: MemorySink | None = None, **settings) -> Library:
    return Library(Settings(library_root=tmp_path / "lib", **settings), sink)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def data_dir_with_token(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "token").write_text(TOKEN, encoding="utf-8")
    return data


class FakeBackend:
    """Loopback stand-in for bcc: same routes, same auth headers, real SQLite memory index."""

    def __init__(self, vault: Path, index_dir: Path):
        from bcc.v2.memory.sqlite_index import SQLiteMemoryBackend

        self.vault = vault
        self.index = SQLiteMemoryBackend(index_path=index_dir / "index.sqlite3", vault_root=vault)
        self.index_calls, self.bad_auth, self.bodies = 0, 0, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth(self):
                if self.headers.get("X-BCC-Token") == TOKEN and self.headers.get("X-Participant") == "owner":
                    return True
                outer.bad_auth += 1
                self._send(401, {"detail": "unauthorized"})
                return False

            def do_GET(self):
                if not self._auth():
                    return
                if self.path == "/api/memory/config":
                    self._send(200, {"configured": True, "root": str(outer.vault), "write_folder": "BOSSMAN Memory"})
                else:
                    self._send(404, {"detail": "not found"})

            def do_POST(self):
                if not self._auth():
                    return
                outer.bodies.append(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                if self.path == "/api/memory/index":
                    outer.index_calls += 1
                    self._send(200, {"result": outer.index.index_sync([outer.vault])})
                else:
                    self._send(404, {"detail": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def backend(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    server = FakeBackend(vault, tmp_path)
    yield server
    server.close()


def live_sink(tmp_path, backend: FakeBackend) -> MemorySink:
    return MemorySink(backend=backend.url, data_dir=data_dir_with_token(tmp_path), timeout=5)


def make_pdf(path: Path, pages: list[list[str]]) -> Path:
    """Minimal text PDF written by hand (no PDF-writer dependency): one Helvetica line per string."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, lines in enumerate(pages):
        ops = " ".join(f"({line}) Tj 0 -24 Td" for line in lines)
        content = f"BT /F1 14 Tf 72 720 Td {ops} ET".encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


# --------------------------------------------------------------------------- dedupe

def test_same_bytes_twice_is_one_entry_and_originals_stay(tmp_path):
    src = write(tmp_path / "in" / "report.txt", "Quarterly numbers and notes")
    copy = write(tmp_path / "in" / "report (1).txt", "Quarterly numbers and notes")
    lib = make_lib(tmp_path)
    first, second, third = lib.intake(src), lib.intake(copy), lib.intake(src)
    assert first.status == "added"
    assert (second.status, second.id) == ("duplicate", first.id)
    assert third.status == "duplicate"
    entries = lib.load()["entries"]
    assert list(entries) == [first.id]
    assert entries[first.id]["aliases"] == ["report (1).txt"]
    assert entries[first.id]["duplicates_seen"] == 2
    assert len([p for p in lib.root.rglob("*.txt")]) == 1          # one library copy
    assert src.read_text(encoding="utf-8") and copy.exists()          # originals untouched (copy, never move)


# --------------------------------------------------------------------------- categories

def test_category_rules_keywords_folders_and_owner_edits(tmp_path):
    lib = make_lib(tmp_path)
    legal = lib.intake(write(tmp_path / "in" / "Договор поставки №5.txt", "Стороны заключили договор поставки."))
    tender = lib.intake(write(tmp_path / "in" / "notes-17.txt",
                              "Извещение о закупке по 44-ФЗ. Аукцион в электронной форме. Закупка товаров."))
    plain = lib.intake(write(tmp_path / "in" / "shopping.txt", "молоко хлеб сыр"))
    assert legal.entry["category"] == "legal" and legal.entry["file"].startswith("legal/")
    assert tender.entry["category"] == "tenders"
    assert plain.entry["category"] == "other" and plain.entry["category_by"] == "default"

    loaded, path = rules.load_rules(lib.root)
    assert path == lib.root / "category_rules.json"
    # start-of-word matching: 'api' must not fire inside 'capital'
    assert rules.categorize(loaded, name="capital.txt", title="", text="capital", ext=".txt").category == "other"

    owner = json.loads(path.read_text(encoding="utf-8"))
    owner["categories"].insert(0, {"name": "medicine", "keywords": ["анализ крови"]})
    path.write_text(json.dumps(owner, ensure_ascii=False), encoding="utf-8")
    med = lib.intake(write(tmp_path / "in" / "lab.txt", "Анализ крови от 01.10. Повторный анализ крови в норме."))
    assert med.entry["category"] == "medicine"
    assert list((lib.root / "medicine").rglob("lab__*.txt"))


def test_broken_owner_rules_are_reported_not_silently_replaced(tmp_path):
    lib = make_lib(tmp_path)
    (lib.root / "category_rules.json").write_text("{ not json", encoding="utf-8")
    out = lib.intake(write(tmp_path / "in" / "a.txt", "text"))
    assert out.status == "error" and "category_rules.json" in out.reason
    assert (lib.root / "category_rules.json").read_text(encoding="utf-8") == "{ not json"


# --------------------------------------------------------------------------- manifest atomicity

def test_manifest_survives_a_failed_write_intact(tmp_path, monkeypatch):
    lib = make_lib(tmp_path)
    assert lib.intake(write(tmp_path / "in" / "a.txt", "first document")).status == "added"
    before = lib.manifest_path.read_bytes()
    real_replace = os.replace

    def disk_full_for_manifest(src, dst):
        if Path(dst).name == "library.json":
            raise OSError(28, "No space left on device")
        return real_replace(src, dst)

    monkeypatch.setattr(fsutil.os, "replace", disk_full_for_manifest)
    second = write(tmp_path / "in" / "b.txt", "second document")
    out = lib.intake(second)
    assert out.status == "error" and "манифест" in out.reason
    assert lib.manifest_path.read_bytes() == before                 # old manifest, byte for byte
    assert len(json.loads(before)["entries"]) == 1
    assert not list(lib.root.glob(".library.json.*.tmp"))           # no half-written temp left
    assert not (lib.root / ".library.lock").exists()                # lock released on failure
    monkeypatch.undo()
    assert lib.intake(second).status == "added"
    assert len(lib.load()["entries"]) == 2


def test_two_processes_never_lose_each_others_entries(tmp_path, monkeypatch):
    lib_a, lib_b = make_lib(tmp_path), make_lib(tmp_path)          # e.g. watch mode + manual CLI
    b_file = write(tmp_path / "in" / "b.txt", "document B")
    real_parse = library.parsers.parse
    state = {"interleaved": False}

    def parse_while_other_process_finishes(path, kind, **kwargs):
        result = real_parse(path, kind, **kwargs)
        if not state["interleaved"]:
            state["interleaved"] = True
            assert lib_b.intake(b_file).status == "added"
        return result

    monkeypatch.setattr(library.parsers, "parse", parse_while_other_process_finishes)
    assert lib_a.intake(write(tmp_path / "in" / "a.txt", "document A")).status == "added"
    titles = sorted(e["title"] for e in lib_a.load()["entries"].values())
    assert titles == ["a", "b"]


# --------------------------------------------------------------------------- PDF pages

def test_pdf_page_numbers_survive_into_notes_index_and_search(tmp_path):
    pytest.importorskip("pypdf")
    pdf = make_pdf(tmp_path / "in" / "Quarterly report.pdf",
                   [["ALPHA first page"], ["BETA second page", "# stray hash line"], ["GAMMA third page"]])
    lib = make_lib(tmp_path, note_units=2)
    out = lib.intake(pdf)
    entry = out.entry
    assert out.status == "added", out.reason
    assert entry["parse_status"] == "PASS" and entry["pages"] == 3 and entry["parser"].startswith("pypdf")
    assert entry["tables"] == "flattened"
    notes = [lib.root / rel for rel in entry["notes"]]
    assert [n.name for n in notes] == ["quarterly-report_p001-002.md", "quarterly-report_p003-003.md"]
    first, last = (n.read_text(encoding="utf-8") for n in notes)
    assert "pages: 1-2" in first and "page_count: 3" in first and "kind: source" in first
    assert "# Quarterly report — стр. 1–2" in first
    assert re.search(r"### Стр\. 2\n[^#]*BETA second page", first)
    assert "\\# stray hash line" in first                          # cannot open a new heading
    assert "### Стр. 3" in last and "GAMMA third page" in last
    assert re.search(r"(?m)^pages: 3$", last)

    from bcc.v2.memory.sqlite_index import chunk_markdown_v22
    chunks, _ = chunk_markdown_v22(first, "quarterly-report_p001-002.md")
    for marker in ("BETA second page", "stray hash line"):
        hits = [c for c in chunks if marker in c.content]
        # the index keeps the heading path: "<title> — стр. 1–2 > Стр. 2"
        assert hits and all(c.heading.endswith("> Стр. 2") for c in hits), marker

    found = lib.search("beta second")
    assert found and found[0]["snippets"][0]["where"] == "Стр. 2"


def test_pdf_without_text_layer_is_fail_not_pass(tmp_path):
    pytest.importorskip("pypdf")
    pdf = make_pdf(tmp_path / "in" / "scan.pdf", [[], []])
    out = make_lib(tmp_path).intake(pdf)
    assert out.status == "added"                                    # stored in the library anyway
    assert out.entry["parse_status"] == "FAIL" and "OCR" in out.entry["parse_reason"]
    assert out.entry["empty_pages"] == [1, 2]


def test_docx_headings_become_sections_and_tables_stay_tables(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("Введение", 1)
    document.add_paragraph("Текст введения про поставку.")
    document.add_heading("Цены", 1)
    table = document.add_table(rows=2, cols=2)
    for cell, text in zip(table._cells, ["Товар", "Цена", "Кабель", "100"]):
        cell.text = text
    path = tmp_path / "in" / "Прайс.docx"
    path.parent.mkdir(parents=True)
    document.save(str(path))
    entry = make_lib(tmp_path).intake(path).entry
    assert entry["parse_status"] == "PASS" and entry["parser"].startswith("python-docx")
    text = (make_lib(tmp_path).root / entry["notes"][0]).read_text(encoding="utf-8")
    assert "### Раздел 1: Введение" in text and "### Раздел 2: Цены" in text
    assert "| Товар | Цена |" in text and "| Кабель | 100 |" in text


# --------------------------------------------------------------------------- refusals

def _pe_bytes() -> bytes:
    head = bytearray(b"MZ" + b"\0" * 62)
    head[0x3C:0x40] = (64).to_bytes(4, "little")
    return bytes(head) + b"PE\0\0" + b"\0" * 64


def test_executables_scripts_and_archives_are_refused(tmp_path):
    inbox = tmp_path / "in"
    inbox.mkdir()
    (inbox / "setup.exe").write_bytes(_pe_bytes())
    (inbox / "invoice.pdf").write_bytes(_pe_bytes())                # renamed executable
    (inbox / "run.ps1").write_text("Remove-Item C:\\ -Recurse", encoding="utf-8")
    (inbox / "data.xyz").write_text("???", encoding="utf-8")
    with zipfile.ZipFile(inbox / "docs.zip", "w") as zf:
        zf.writestr("a.txt", "hello")
    with zipfile.ZipFile(inbox / "report.docx", "w") as zf:      # a ZIP pretending to be DOCX
        zf.writestr("payload/readme.txt", "hello")
    lib = make_lib(tmp_path)
    outcomes = {p.name: lib.intake(p) for p in sorted(inbox.iterdir())}
    assert {name: o.status for name, o in outcomes.items()} == dict.fromkeys(outcomes, "refused")
    assert "PE" in outcomes["invoice.pdf"].reason
    assert "архив" in outcomes["report.docx"].reason
    assert lib.load()["entries"] == {}
    assert [p.name for p in lib.root.rglob("*") if p.is_file()] == []   # nothing copied


def test_oversized_file_is_refused_before_copy(tmp_path):
    lib = make_lib(tmp_path, max_bytes=1000)
    big = lib.intake(write(tmp_path / "in" / "big.txt", "x" * 2000))
    small = lib.intake(write(tmp_path / "in" / "small.txt", "y" * 900))
    assert big.status == "refused" and "лимит" in big.reason
    assert small.status == "added"
    assert [e["original_name"] for e in lib.load()["entries"].values()] == ["small.txt"]


# --------------------------------------------------------------------------- memory

def test_backend_down_keeps_notes_and_marks_indexed_false_then_reindex(tmp_path, backend):
    data = data_dir_with_token(tmp_path)
    dead = MemorySink(vault_root=backend.vault, backend=f"http://127.0.0.1:{free_port()}", data_dir=data, timeout=2)
    lib = make_lib(tmp_path, dead)
    out = lib.intake(write(tmp_path / "in" / "Методичка продаж.txt", "Руководство: как вести переговоры."))
    mem = out.entry["memory"]
    assert out.status == "added"
    assert (mem["state"], mem["written"], mem["indexed"]) == ("written", True, False)
    assert "недоступен" in mem["index_error"]
    note = backend.vault / mem["vault_paths"][0]
    assert note.is_file()
    assert note.relative_to(backend.vault).parts[:3] == ("BOSSMAN Memory", "Library", "manuals")

    lib.memory = MemorySink(vault_root=backend.vault, backend=backend.url, data_dir=data, timeout=5)
    result = lib.reindex()
    assert result["indexed"] is True and result["pending"] == 0
    assert lib.get(out.id)["memory"]["indexed"] is True
    assert backend.index_calls == 1 and backend.bad_auth == 0
    assert set(backend.bodies) == {b"{}"}                           # no document text over the wire


def test_vault_from_api_notes_indexed_with_section_citations(tmp_path, backend):
    lib = make_lib(tmp_path, live_sink(tmp_path, backend))
    md = write(tmp_path / "in" / "spec.md", "# Спецификация\n\nОбщее.\n\n## Цены\n\nКабель медный стоит 100.\n")
    out = lib.intake(md)
    mem = out.entry["memory"]
    assert (mem["state"], mem["vault_source"], mem["indexed"]) == ("indexed", "api", True)
    assert mem["vault_dir"] == f"{out.entry['category']}/{out.entry['slug']}"
    hits = backend.index.search_sync("кабель медный", top_k=3)
    assert hits and hits[0].heading.endswith("> Раздел 2: Цены")
    assert hits[0].source.startswith("BOSSMAN Memory/Library/")


def test_secrets_are_redacted_in_memory_but_library_copy_is_untouched(tmp_path, backend):
    lib = make_lib(tmp_path, live_sink(tmp_path, backend))
    text = "Инструкция подключения.\napi_key: zzzz-canary-value\nДальше обычный текст."  # ci-secret-scan: allow
    out = lib.intake(write(tmp_path / "in" / "setup-guide.txt", text))
    vault_note = (backend.vault / out.entry["memory"]["vault_paths"][0]).read_text(encoding="utf-8")
    assert "zzzz-canary-value" not in vault_note and "[REDACTED:assigned_secret]" in vault_note
    assert "redactions: [assigned_secret]" in vault_note
    assert out.entry["memory"]["redactions"] == ["assigned_secret"]
    library_note = (lib.root / out.entry["notes"][0]).read_text(encoding="utf-8")
    assert "zzzz-canary-value" in library_note                      # owner's own copy stays exact


def test_retag_move_category_and_reparse_keep_library_and_vault_in_sync(tmp_path, backend):
    lib = make_lib(tmp_path, live_sink(tmp_path, backend))
    out = lib.intake(write(tmp_path / "in" / "memo.txt", "Простая служебная записка."))
    assert out.entry["category"] == "other"
    old_vault = [backend.vault / p for p in out.entry["memory"]["vault_paths"]]

    tagged = lib.retag(out.id, add=["q3", "важное"])
    assert tagged["tags"] == ["q3", "важное"]
    assert "tags: [library, other, q3, важное]" in (lib.root / tagged["notes"][0]).read_text(encoding="utf-8")

    moved = lib.move_category(out.id, "finance")
    assert moved["file"].startswith("finance/") and (lib.root / moved["file"]).is_file()
    assert not (lib.root / out.entry["file"]).exists()
    new_vault = [backend.vault / p for p in moved["memory"]["vault_paths"]]
    assert all(p.is_file() and "/Library/finance/" in p.as_posix() for p in new_vault)
    assert not any(p.exists() for p in old_vault)                   # no stale copy left in memory
    assert "category: finance" in new_vault[0].read_text(encoding="utf-8")
    assert moved["memory"]["indexed"] is True and backend.index_calls >= 3

    assert lib.reparse(out.id)["parse_status"] == "PASS"
    (lib.root / moved["file"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(LibraryError):
        lib.reparse(out.id)


def test_backend_address_must_be_loopback():
    with pytest.raises(ValueError):
        MemorySink(backend="http://example.com:8801")


# --------------------------------------------------------------------------- watch + CLI

def test_watch_takes_only_settled_files_and_remembers_them(tmp_path):
    inbox = tmp_path / "inbox"
    old = write(inbox / "old.txt", "ready document")
    fresh = write(inbox / "fresh.txt", "still being copied")
    write(inbox / "~$lock.docx", "office lock file")
    write(inbox / "movie.part", "partial download")
    past = time.time() - 600
    os.utime(old, (past, past))
    lib = make_lib(tmp_path)
    first = watch.scan_once(lib, inbox, settle=60)
    assert [Path(o.path).name for o in first] == ["old.txt"]
    later = watch.scan_once(lib, inbox, settle=60, clock=lambda: time.time() + 120)
    assert [Path(o.path).name for o in later] == ["fresh.txt"]
    assert watch.scan_once(lib, inbox, settle=0) == []              # unchanged files are not re-taken
    assert old.exists() and fresh.exists()                          # originals stay in the inbox
    assert len(lib.load()["entries"]) == 2


def test_cli_dry_run_intake_list_search_show(tmp_path, capsys):
    doc = write(tmp_path / "in" / "Тендер на кабель.txt", "Закупка кабеля, аукцион 44-ФЗ, срок подачи 30.10.")
    base = ["--library-root", str(tmp_path / "lib"), "--data-dir", str(tmp_path / "data"), "--no-memory", "--json"]
    assert cli.main(base + ["intake", str(doc)]) == 0
    added = json.loads(capsys.readouterr().out)[0]
    assert added["status"] == "added" and added["entry"]["memory"]["state"] == "disabled"
    assert cli.main(base + ["list"]) == 0
    assert [e["id"] for e in json.loads(capsys.readouterr().out)] == [added["id"]]
    assert cli.main(base + ["search", "аукцион"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["category"] == "tenders"
    assert cli.main(base + ["show", added["id"][:6]]) == 0
    assert json.loads(capsys.readouterr().out)["original_name"] == "Тендер на кабель.txt"
    assert cli.main(base + ["intake", str(tmp_path / "in" / "missing.pdf")]) == 2
