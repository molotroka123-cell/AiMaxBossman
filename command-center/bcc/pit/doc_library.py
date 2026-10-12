"""Per-person document library with BM25 retrieval and citations (Jeff paid document assistant).

* Storage lives INSIDE the participant's own vault directory (``<person_dir>/docs``), so another person's
  documents are not reachable by construction and ``/delete_me`` (vault.delete) removes them with the rest.
* Text comes from the existing ``bossman.file_intel`` parsers (txt/md/csv/json/docx/xlsx/pptx/epub, PDF through
  ``pypdf`` when installed). A format the install cannot read is refused honestly, never guessed.
* A participant's file is hostile input. It is NEVER parsed inside the Jeff process: ``extract_sections`` hands it to
  a child interpreter (``bcc.pit.doc_extract_child``) with a hard wall-clock limit that is killed, with everything it
  spawned, when it overruns. A thread cannot be killed, a process can. At most MAX_CONCURRENT_PARSES run at once.
* Retrieval is plain BM25 over this person's chunks. No embeddings, no network, no model.
* A citation names the document and the place (page / sheet / fragment) and carries the exact stored quote, so the
  caller can show the user what the answer rests on.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

MAX_DOC_CHARS = 300_000
CHUNK_CHARS = 1100
MAX_CHUNKS_PER_DOC = 400
_ID = re.compile(r"^[0-9a-f]{12}$")
_WORD = re.compile(r"[0-9a-zа-яё]+")
_STOP = frozenset("и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне "
                  "было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него "
                  "до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для "
                  "мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому "
                  "этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда "
                  "можно при наконец два об другой хоть после над больше тот через эти нас про всего них какая "
                  "много разве три эту моя впрочем свою этой перед иногда лучше чуть том нельзя такой им более всегда "
                  "конечно всю между the a an of to and in is are for on with as by at from".split())


class LibraryError(Exception):
    """Refusal with a participant-safe reason code."""

    def __init__(self, code: str, text: str):
        super().__init__(code)
        self.code, self.text = code, text


@dataclass(frozen=True)
class DocRecord:
    id: str
    name: str
    sha256: str
    chars: int
    chunks: int
    added_at: float


@dataclass(frozen=True)
class Hit:
    doc_id: str
    doc_name: str
    n: int
    ref: str
    text: str
    score: float

    @property
    def label(self) -> str:
        return f"{self.doc_name} · {_place(self.ref, self.n)}"


def _place(ref: str, n: int) -> str:
    m = re.match(r"^(page|sheet|slide|sec|chapter)=(.+)$", ref or "")
    if m:
        word = {"page": "стр.", "sheet": "лист", "slide": "слайд", "sec": "раздел", "chapter": "глава"}[m.group(1)]
        return f"{word} {m.group(2)}"[:60]
    return f"фрагмент {n}"


def tokens(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.casefold()):
        if w in _STOP or len(w) < 2:
            continue
        out.append(w[:6] if len(w) > 6 else w)          # crude stemmer: shared prefix covers RU/EN inflections
    return out


MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
MAX_XML_DEPTH = 64                    # real documents nest a handful of levels; the parser is quadratic in depth
MAX_XML_ELEMENTS = 1_000_000          # all parsed XML members together (memory of the tree, time of the parser)
CHILD_TIMEOUT_SECONDS = 20.0          # wall clock for one parse, then the child and its children are killed
PARSE_QUEUE_SECONDS = 30.0            # how long an upload waits for one of the parse slots
MAX_CONCURRENT_PARSES = 2
MAX_CHILD_OUTPUT_BYTES = 8 * 1024 * 1024

_PARSE_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_PARSES)
#: The members ``bossman.file_intel`` runs an XML parser over (docx / xlsx / pptx / epub).
_XML_TARGETS = re.compile(r"^(word/document\.xml|xl/sharedStrings\.xml|xl/workbook\.xml|xl/worksheets/sheet\d+\.xml"
                          r"|ppt/slides/slide\d+\.xml|META-INF/container\.xml|.+\.opf)$")


def _unreadable() -> LibraryError:
    return LibraryError("unreadable", "Не удалось прочитать файл: он повреждён или имеет неподдерживаемый вид.")


def _too_complex() -> LibraryError:
    return LibraryError("too_long", "Файл слишком сложный для разбора.")


def _refuse_zip_bomb(data: bytes) -> None:
    """Office files are zips: decide from the central directory BEFORE any parser inflates them."""
    if data[:2] != b"PK":
        return
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile:
        return                                              # the parser reports it as unreadable
    except Exception:  # noqa: BLE001 - NotImplementedError ("zip file version"), UnicodeDecodeError in a member name...
        raise _unreadable() from None
    if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
        raise LibraryError("too_long", "Файл слишком большой после распаковки — такой не принимаю.")


class _TooComplex(Exception):
    pass


def _scan_xml(xml: bytes, budget: int) -> int:
    """Stream one XML part through expat and count: no tree is built, and the first violation aborts the scan.
    Returns what is left of the element budget. Office XML never declares entities, so any declaration is refused."""
    from xml.parsers import expat

    depth = count = 0

    def start(_name, _attrs):
        nonlocal depth, count
        depth += 1
        count += 1
        if depth > MAX_XML_DEPTH or count > budget:
            raise _TooComplex

    def end(_name):
        nonlocal depth
        depth -= 1

    def entity(*_args):
        raise _TooComplex

    parser = expat.ParserCreate()
    parser.StartElementHandler, parser.EndElementHandler, parser.EntityDeclHandler = start, end, entity
    try:
        parser.Parse(xml, True)
    except _TooComplex:
        raise _too_complex() from None
    except expat.ExpatError:
        raise _unreadable() from None
    return budget - count


def _prescan_office_xml(data: bytes) -> None:
    """Refuse an office file whose XML is deeper than MAX_XML_DEPTH or larger than MAX_XML_ELEMENTS elements BEFORE
    the quadratic ``iter()`` inside ``iter()`` of the real parser runs: a 900-byte docx of 40 000 nested paragraphs
    burned 27 s of CPU there."""
    if data[:4] != b"PK\x03\x04":
        return
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            budget = MAX_XML_ELEMENTS
            for info in zf.infolist():
                if _XML_TARGETS.match(info.filename):
                    budget = _scan_xml(zf.read(info), budget)
    except LibraryError:
        raise
    except zipfile.BadZipFile:
        return
    except Exception:  # noqa: BLE001 - a part that cannot even be read is a refusal, not a crash
        raise _unreadable() from None


def parse_in_process(path: str, name: str = "") -> list[tuple[str, str]]:
    """(ref, text) pairs through the existing file_intel parsers; a sniffed type beats the claimed one.

    This is THE parse and it must only ever run inside the child process (see ``extract_sections``): it is the
    code that hostile input can make slow. It also owns every refusal that depends on the file's content."""
    from bossman.file_intel import ParseUnavailable, parse_file

    data = Path(path).read_bytes()
    _refuse_zip_bomb(data)
    _prescan_office_xml(data)
    try:
        art = parse_file(path)
    except ParseUnavailable as exc:
        raise LibraryError("parser_unavailable", f"Этот формат пока не читается на этой установке: {exc}") from None
    except (MemoryError, RecursionError):
        raise _too_complex() from None
    except Exception:  # noqa: BLE001 - malformed xml / zip / json from any parser is a refusal, never a crash
        raise _unreadable() from None
    sections: list[tuple[str, str]] = []
    for sec in art.sections:
        if sec.table:
            body = "\n".join(" | ".join(str(c) for c in row) for row in sec.table)
        else:
            body = sec.text
        body = body.replace("\x00", "").strip()
        if body:
            sections.append((str(sec.ref or ""), body))
    total = sum(len(body) for _ref, body in sections)
    if total > MAX_DOC_CHARS:
        raise LibraryError("too_long", f"Документ слишком длинный: {total} символов, максимум {MAX_DOC_CHARS}.")
    return sections


def _child_argv(tmp: str, name: str) -> list[str]:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)[:60] or "document"      # never a NUL or a quote on argv
    return [sys.executable, "-X", "utf8", "-m", "bcc.pit.doc_extract_child", tmp, safe]


def _package_roots() -> list[str]:
    """Absolute directories ``bcc`` and ``bossman`` are imported from by THIS process (no import is performed)."""
    import importlib.util

    roots: list[str] = []
    for package in ("bcc", "bossman"):
        try:
            spec = importlib.util.find_spec(package)
            places = list(getattr(spec, "submodule_search_locations", None) or [])
            root = str(Path(places[0]).resolve().parent) if places else ""
        except Exception:  # noqa: BLE001 - the inherited environment is the fallback
            root = ""
        if root and root not in roots:
            roots.append(root)
    return roots


#: Computed at import, while relative sys.path entries ("." from a PYTHONPATH of a source tree) still mean what they
#: meant when the process started.
_PACKAGE_ROOTS = _package_roots()


def _child_env() -> dict[str, str]:
    """The child imports ``bcc`` and ``bossman`` by name. From a source tree PYTHONPATH may be unset or relative, so
    the directories the PARENT imported them from are put first; in the bundle's embedded python a ``._pth`` file
    decides sys.path and this is simply ignored."""
    env = dict(os.environ)
    inherited = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([*_PACKAGE_ROOTS, *([inherited] if inherited else [])])
    env["PYTHONUTF8"] = "1"
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the child AND whatever it started (a venv launcher on Windows is a parent of the real interpreter)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001 - fall through to the plain kill below
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _run_child(tmp: str, name: str) -> list[tuple[str, str]]:
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(_child_argv(tmp, name), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=_child_env(), **kwargs)
    except OSError:
        raise LibraryError("parser_unavailable", "Разбор файлов сейчас недоступен на этой установке.") from None
    try:
        out, _err = proc.communicate(timeout=CHILD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        raise LibraryError("too_long", "Файл слишком сложный: разбор занял слишком много времени и остановлен.") from None
    except BaseException:                                   # cancelled / interrupted: never leave the child behind
        _kill_tree(proc)
        raise
    if len(out) > MAX_CHILD_OUTPUT_BYTES:
        raise _too_complex()
    try:
        lines = [line for line in out.decode("utf-8").splitlines() if line.strip()]
        reply = json.loads(lines[-1])
        if reply.get("ok") is True:
            return [(str(ref), str(text)) for ref, text in reply["sections"]]
        raise LibraryError(str(reply.get("code") or "unreadable"), str(reply.get("text") or _unreadable().text))
    except LibraryError:
        raise
    except Exception:  # noqa: BLE001 - a crashed child (killed, out of memory) printed nothing usable
        raise _unreadable() from None


def extract_sections(name: str, data: bytes) -> list[tuple[str, str]]:
    """(ref, text) pairs for one file, parsed by a child process with a hard time limit."""
    suffix = Path(name).suffix.lower()[:8] or ".txt"
    if not re.fullmatch(r"\.[a-z0-9]{1,7}", suffix):
        suffix = ".txt"
    if not _PARSE_SLOTS.acquire(timeout=PARSE_QUEUE_SECONDS):
        raise LibraryError("busy", "Сейчас разбирается много файлов. Повторите загрузку через минуту.")
    try:
        fd, tmp = tempfile.mkstemp(prefix="jdoc-", suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            return _run_child(tmp, "document" + suffix)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    finally:
        _PARSE_SLOTS.release()


def chunk_sections(sections: list[tuple[str, str]], size: int = CHUNK_CHARS) -> list[dict]:
    chunks: list[dict] = []
    for ref, body in sections:
        buf = ""
        for para in re.split(r"\n{2,}|(?<=[.!?])\s+(?=[A-ZА-ЯЁ0-9])", body):
            para = para.strip()
            if not para:
                continue
            while len(para) > size:                       # one huge paragraph: cut on whitespace
                cut = para.rfind(" ", 0, size)
                cut = cut if cut > size // 2 else size
                if buf:
                    chunks.append({"ref": ref, "text": buf})
                    buf = ""
                chunks.append({"ref": ref, "text": para[:cut].strip()})
                para = para[cut:].strip()
            if buf and len(buf) + len(para) + 1 > size:
                chunks.append({"ref": ref, "text": buf})
                buf = ""
            buf = f"{buf}\n{para}".strip() if buf else para
        if buf:
            chunks.append({"ref": ref, "text": buf})
    for n, chunk in enumerate(chunks, 1):
        chunk["n"] = n
    return chunks


class DocLibrary:
    def __init__(self, root: Path):
        self.root = Path(root)

    # -- storage ------------------------------------------------------------------------------
    @property
    def _index_path(self) -> Path:
        return self.root / "index.json"

    def _read_index(self) -> list[dict]:
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            return [d for d in data.get("docs", []) if _ID.fullmatch(str(d.get("id", "")))]
        except (OSError, ValueError, AttributeError):
            return []

    def _ensure_root(self) -> None:
        """Create ``docs`` inside an EXISTING person directory only: a profile deleted while a file was being parsed
        must not come back as a bare folder holding that file."""
        if not self.root.parent.is_dir():
            raise LibraryError("gone", "Профиль был удалён во время загрузки — документ не сохранён.")
        self.root.mkdir(exist_ok=True)

    @staticmethod
    def _replace(src: Path, dst: Path) -> None:
        """os.replace, retried: on Windows a concurrent reader's open handle makes the rename fail for a moment."""
        for attempt in range(8):
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.02 * (attempt + 1))

    def _write_index(self, docs: list[dict]) -> None:
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "docs": docs}, ensure_ascii=False), encoding="utf-8")
        self._replace(tmp, self._index_path)

    def _chunk_path(self, doc_id: str) -> Path:
        if not _ID.fullmatch(doc_id):
            raise LibraryError("bad_id", "Неверный номер документа.")
        return self.root / f"{doc_id}.jsonl"

    def list(self) -> list[DocRecord]:
        return [DocRecord(d["id"], d["name"], d["sha256"], d["chars"], d["chunks"], d["added_at"])
                for d in self._read_index()]

    def count(self) -> int:
        return len(self._read_index())

    # -- mutation -----------------------------------------------------------------------------
    def add(self, name: str, data: bytes, *, limit: int, now: float | None = None, lock=None,
            valid=None) -> tuple[DocRecord, bool]:
        """Store one document; returns (record, is_new). Re-uploading identical bytes is free and not counted.

        The slow part (parsing) runs OUTSIDE ``lock``; the index is read again under it right before the write, so a
        ``/deldoc`` or another upload that ran meanwhile is never undone by a stale copy of the index. ``valid`` is
        asked under the lock too: False means the person was deleted while the file was parsing, and nothing is
        written."""
        now = time.time() if now is None else now
        digest = hashlib.sha256(data).hexdigest()
        guard = lock if lock is not None else contextlib.nullcontext()

        def known(docs: list[dict]) -> DocRecord | None:
            for d in docs:
                if d["sha256"] == digest:
                    return DocRecord(d["id"], d["name"], d["sha256"], d["chars"], d["chunks"], d["added_at"])
            return None

        def check(docs: list[dict]) -> None:
            if len(docs) >= limit:
                raise LibraryError("doc_limit",
                                   f"Достигнут лимит документов: {limit}. Удалите лишний: /deldoc <номер>.")

        with guard:
            docs = self._read_index()
            found = known(docs)
            if found is not None:
                return found, False
            check(docs)
        sections = extract_sections(name, data)
        total = sum(len(t) for _, t in sections)
        if total == 0:
            raise LibraryError("empty", "В файле не найден текст (возможно, это скан без распознавания).")
        if total > MAX_DOC_CHARS:
            raise LibraryError("too_long", f"Документ слишком длинный: {total} символов, максимум {MAX_DOC_CHARS}.")
        chunks = chunk_sections(sections)
        if len(chunks) > MAX_CHUNKS_PER_DOC:
            raise LibraryError("too_long", "Документ слишком длинный для разбора.")
        doc_id = digest[:12]
        with guard:
            if valid is not None and not valid():
                raise LibraryError("gone", "Профиль был удалён во время загрузки — документ не сохранён.")
            docs = self._read_index()
            found = known(docs)
            if found is not None:                                  # the same bytes landed while this one parsed
                return found, False
            check(docs)
            self._ensure_root()
            tmp = self._chunk_path(doc_id).with_suffix(".tmp")
            tmp.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in chunks), encoding="utf-8")
            self._replace(tmp, self._chunk_path(doc_id))
            rec = {"id": doc_id, "name": re.sub(r"[\r\n\t]+", " ", name)[:120] or "документ", "sha256": digest,
                   "chars": total, "chunks": len(chunks), "added_at": now}
            docs.append(rec)
            self._write_index(docs)
        return DocRecord(**rec), True

    def remove(self, doc_id: str) -> bool:
        docs = self._read_index()
        kept = [d for d in docs if d["id"] != doc_id]
        if len(kept) == len(docs):
            return False
        self._write_index(kept)
        try:
            self._chunk_path(doc_id).unlink()
        except OSError:
            pass
        return True

    def purge(self) -> None:
        for d in self._read_index():
            self.remove(d["id"])

    # -- retrieval ----------------------------------------------------------------------------
    def _chunks(self, doc_id: str) -> list[dict]:
        try:
            return [json.loads(line) for line in self._chunk_path(doc_id).read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        except (OSError, ValueError):
            return []

    def search(self, query: str, *, k: int = 5, doc_id: str | None = None) -> list[Hit]:
        names = {d["id"]: d["name"] for d in self._read_index()}
        scope = [doc_id] if doc_id else list(names)
        corpus: list[tuple[str, dict, Counter]] = []
        for did in scope:
            if did not in names:
                continue
            for chunk in self._chunks(did):
                corpus.append((did, chunk, Counter(tokens(chunk.get("text", "")))))
        q = tokens(query)
        if not corpus or not q:
            return []
        n = len(corpus)
        avg = sum(sum(c.values()) for _, _, c in corpus) / n or 1.0
        df = Counter()
        for _, _, c in corpus:
            for term in set(q):
                if term in c:
                    df[term] += 1
        scored: list[Hit] = []
        for did, chunk, c in corpus:
            length = sum(c.values()) or 1
            score = 0.0
            for term in set(q):
                f = c.get(term, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * length / avg))
            if score > 0:
                scored.append(Hit(did, names[did], int(chunk.get("n", 0)), str(chunk.get("ref", "")),
                                  str(chunk.get("text", "")), round(score, 4)))
        scored.sort(key=lambda h: (-h.score, h.doc_id, h.n))
        return scored[:k]

    def overview(self, doc_id: str, *, max_chars: int = 9000) -> list[Hit]:
        """Evenly spaced chunks of one document that fit the budget (for summaries)."""
        names = {d["id"]: d["name"] for d in self._read_index()}
        if doc_id not in names:
            return []
        chunks = self._chunks(doc_id)
        if not chunks:
            return []
        per = max(1, max_chars // CHUNK_CHARS)
        step = max(1, math.ceil(len(chunks) / per))
        picked = chunks[::step][:per]
        return [Hit(doc_id, names[doc_id], int(c.get("n", 0)), str(c.get("ref", "")), str(c.get("text", "")), 0.0)
                for c in picked]
