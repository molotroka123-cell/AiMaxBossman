"""Per-person document library with BM25 retrieval and citations (Jeff paid document assistant).

* Storage lives INSIDE the participant's own vault directory (``<person_dir>/docs``), so another person's
  documents are not reachable by construction and ``/delete_me`` (vault.delete) removes them with the rest.
* Text comes from the existing ``bossman.file_intel`` parsers (txt/md/csv/json/docx/xlsx/pptx/epub, PDF through
  ``pypdf`` when installed). A format the install cannot read is refused honestly, never guessed.
* A participant's file is hostile input. It is NEVER parsed inside the Jeff process: ``extract_sections`` hands it to
  a child interpreter (``bcc.pit.doc_extract_child``) with a hard wall-clock limit that is killed, with everything it
  spawned, when it overruns. A thread cannot be killed, a process can. At most MAX_CONCURRENT_PARSES run at once.
  The child also has a memory ceiling (a Windows Job Object, RLIMIT_AS elsewhere), a CPU-time ceiling, a watchdog
  that ends an orphan, and an environment reduced to an allow-list: it never sees a token or a key.
* Retrieval is plain BM25 over this person's chunks. No embeddings, no network, no model.
* A citation names the document and the place (page / sheet / fragment) and carries the exact stored quote, so the
  caller can show the user what the answer rests on.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
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

log = logging.getLogger(__name__)

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
    #: The parser cut the file at one of its own caps (see ``_truncation``): only the first ``kept`` of ~``total``
    #: ``unit`` are stored. ``total`` 0 means "more than kept, count unknown". Said in the upload reply and in /docs.
    partial: bool = False
    kept: int = 0
    total: int = 0
    unit: str = ""

    @property
    def partial_note(self) -> str:
        return describe_partial(self.unit, self.kept, self.total) if self.partial else ""


def describe_partial(unit: str, kept: int, total: int) -> str:
    """The honest sentence for a document stored only in part (never an empty string for a partial one)."""
    if total > kept:
        return f"сохранён частично: первые {kept} из ~{total} {unit}"
    return f"сохранён частично: первые {kept} {unit}, в файле их больше"


def _record(d: dict) -> DocRecord:
    return DocRecord(d["id"], d["name"], d["sha256"], d["chars"], d["chunks"], d["added_at"],
                     bool(d.get("partial")), int(d.get("kept") or 0), int(d.get("total") or 0),
                     str(d.get("unit") or ""))


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


def bm25_scores(query_terms: list[str], counters: list[Counter]) -> list[float]:
    """Plain BM25 (k1=1.5, b=0.75) of ``query_terms`` (already ``tokens()``) against each term counter; 0 = no match.

    Shared by the participant document library and the owner's Jeff manual (``bcc.pit.jeff_kb``)."""
    n = len(counters)
    if not n or not query_terms:
        return [0.0] * n
    terms = set(query_terms)
    avg = sum(sum(c.values()) for c in counters) / n or 1.0
    df = Counter()
    for c in counters:
        for term in terms:
            if term in c:
                df[term] += 1
    out: list[float] = []
    for c in counters:
        length = sum(c.values()) or 1
        score = 0.0
        for term in terms:
            f = c.get(term, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * length / avg))
        out.append(score)
    return out


MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
MAX_XML_DEPTH = 64                    # real documents nest a handful of levels; the parser is quadratic in depth
MAX_XML_ELEMENTS = 1_000_000          # all parsed XML members together (memory of the tree, time of the parser)
MAX_TABLE_DEPTH = 5                   # nested w:tbl: file_intel walks tbl -> tr -> tc -> t per level (work x levels)
MAX_MARKUP_MEMBER_BYTES = 2 * 1024 * 1024   # one EPUB/ODT html/xml member, inflated; a 21 KB epub took 5.5 GB of RAM
CHILD_TIMEOUT_SECONDS = 20.0          # wall clock for one parse, then the child and its children are killed
CHILD_WATCHDOG_SECONDS = 25.0         # the child ends ITSELF after this (an orphan of a dead parent stops)
CHILD_CPU_SECONDS = 30                # CPU-time ceiling of the child (kernel-enforced, also inside a C-level loop)
CHILD_MEMORY_BYTES = 1 << 30          # committed-memory ceiling of the child (Job Object / RLIMIT_AS)
MEMORY_ENV = "JEFF_DOC_CHILD_MEMORY"  # how the parent tells the child the ceiling it set (tests lower it)
PARSE_QUEUE_SECONDS = 30.0            # how long an upload waits for one of the parse slots
MAX_CONCURRENT_PARSES = 2
MAX_CHILD_OUTPUT_BYTES = 8 * 1024 * 1024
STALE_TEMP_SECONDS = 3600.0           # a ``jdoc-*`` temp copy older than this belongs to a crashed parse

_PARSE_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_PARSES)
#: The members ``bossman.file_intel`` runs an XML parser over (docx / xlsx / pptx / epub).
_XML_TARGETS = re.compile(r"^(word/document\.xml|xl/sharedStrings\.xml|xl/workbook\.xml|xl/worksheets/sheet\d+\.xml"
                          r"|ppt/slides/slide\d+\.xml|META-INF/container\.xml|.+\.opf)$")
#: Markup members of a zip that is not an OOXML package (EPUB, ODT...): chapter html / xhtml, content.xml, opf, ncx.
_MARKUP_MEMBER = re.compile(r"\.(?:x?html?|xml|opf|ncx)$", re.IGNORECASE)
#: What the child process may see of the parent's environment. Everything else (tokens, API keys, BOSSMAN_* paths)
#: is dropped: the child parses a stranger's file and has no business holding a secret.
_ENV_ALLOW = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "OS",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL", "LC_CTYPE", "PYTHONUTF8",
    "PYTHONIOENCODING", "PYTHONHOME", "PYTHONNOUSERSITE", "PYTHONUSERBASE", "PYTHONDONTWRITEBYTECODE"})


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
    if not any(i.filename.startswith(("word/", "xl/", "ppt/")) for i in infos):
        # EPUB / ODT: the chapter html is fed to a forgiving html parser whose memory is not bounded by the element
        # budget of ``_prescan_office_xml`` (21 KB of ``<a`` expanded to 5.5 GB). One member inflating past
        # MAX_MARKUP_MEMBER_BYTES is refused here, from the central directory, before anything reads it.
        if any(_MARKUP_MEMBER.search(i.filename) and i.file_size > MAX_MARKUP_MEMBER_BYTES for i in infos):
            raise _too_complex()


class _TooComplex(Exception):
    pass


def _scan_xml(xml: bytes, budget: int, stats: dict | None = None, *, table_cap: bool = False) -> int:
    """Stream one XML part through expat and count: no tree is built, and the first violation aborts the scan.
    Returns what is left of the element budget. Office XML never declares entities, so any declaration is refused.

    ``table_cap`` also refuses tables nested deeper than MAX_TABLE_DEPTH (the real parser walks every level again);
    ``stats`` collects ``paragraphs`` (with text) and ``tables`` of the part, for the "stored partly" counts."""
    from xml.parsers import expat

    depth = count = tables_open = 0
    paragraphs: list[bool] = []          # one entry per open <p>: has it text yet
    named = table_cap or stats is not None

    def start(name, _attrs):
        nonlocal depth, count, tables_open
        depth += 1
        count += 1
        if depth > MAX_XML_DEPTH or count > budget:
            raise _TooComplex
        if named:
            local = name.rpartition(":")[2]
            if local == "tbl":
                tables_open += 1
                if table_cap and tables_open > MAX_TABLE_DEPTH:
                    raise _TooComplex
                if stats is not None:
                    stats["tables"] += 1
            elif local == "p" and stats is not None:
                paragraphs.append(False)

    def end(name):
        nonlocal depth, tables_open
        depth -= 1
        if named:
            local = name.rpartition(":")[2]
            if local == "tbl":
                tables_open -= 1
            elif local == "p" and paragraphs:
                if paragraphs.pop() and stats is not None:
                    stats["paragraphs"] += 1

    def text(data):
        if paragraphs and not paragraphs[-1] and data.strip():
            paragraphs[-1] = True

    def entity(*_args):
        raise _TooComplex

    parser = expat.ParserCreate()
    parser.StartElementHandler, parser.EndElementHandler, parser.EntityDeclHandler = start, end, entity
    if stats is not None:
        parser.CharacterDataHandler = text
    try:
        parser.Parse(xml, True)
    except _TooComplex:
        raise _too_complex() from None
    except expat.ExpatError:
        raise _unreadable() from None
    return budget - count


def _prescan_office_xml(data: bytes) -> dict:
    """Refuse an office file whose XML is deeper than MAX_XML_DEPTH or larger than MAX_XML_ELEMENTS elements BEFORE
    the quadratic ``iter()`` inside ``iter()`` of the real parser runs: a 900-byte docx of 40 000 nested paragraphs
    burned 27 s of CPU there. The tables of a document may nest only MAX_TABLE_DEPTH deep.

    Returns the counts of ``word/document.xml`` (``paragraphs`` with text, ``tables``), empty for other files."""
    if data[:4] != b"PK\x03\x04":
        return {}
    import io
    import zipfile
    stats = {"paragraphs": 0, "tables": 0}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            budget = MAX_XML_ELEMENTS
            for info in zf.infolist():
                if _XML_TARGETS.match(info.filename):
                    is_body = info.filename == "word/document.xml"
                    budget = _scan_xml(zf.read(info), budget, stats if is_body else None, table_cap=is_body)
    except LibraryError:
        raise
    except zipfile.BadZipFile:
        return {}
    except Exception:  # noqa: BLE001 - a part that cannot even be read is a refusal, not a crash
        raise _unreadable() from None
    return stats


class _Sections(list):
    """(ref, text) pairs, plus ``partial`` when the parser cut the file at one of its caps (None when it did not).
    Still a plain list for every caller that only wants the sections."""
    partial: dict | None = None


_JSON_TEXT_CAP = 200_000       # ``bossman.file_intel`` cuts the pretty-printed json text here, without a word
_UNITS = {"docx": "абзацев", "md": "разделов", "pptx": "слайдов", "pdf": "страниц", "epub": "глав",
          "zip": "файлов", "xlsx": "листов"}


def _truncation(art, data: bytes, stats: dict) -> dict | None:
    """Did ``bossman.file_intel`` stop before the end of this file? It silently keeps the first MAX_SECTIONS sections,
    the first 5000 table rows and the first 200 000 characters of a json text. Returns ``{"unit", "kept", "total"}``
    (``total`` 0 = more than kept, count unknown) or None. Detection only: the parser itself is not changed."""
    from bossman import file_intel as fi

    sections = list(art.sections)
    kind = art.kind
    if kind in ("csv", "xlsx"):
        kept = total = 0
        for sec in sections:
            if not sec.table:
                continue
            if kind == "csv":
                found = re.match(r"^rows=1:(\d+)$", str(sec.ref or ""))
                rows = int(found.group(1)) if found else len(sec.table)
            else:
                rows = (sec.meta or {}).get("rows")
                rows = rows if type(rows) is int else len(sec.table)
            kept += len(sec.table)
            total += max(rows, len(sec.table))
        if total > kept:
            return {"unit": "строк", "kept": kept, "total": total}
        if kind == "csv":
            return None
    elif kind == "json":
        text = sections[0].text if sections else ""
        if len(text) >= _JSON_TEXT_CAP:
            return {"unit": "символов", "kept": len(text), "total": 0}
        return None
    if len(sections) < fi.MAX_SECTIONS:
        return None
    kept = len(sections)
    total = 0
    try:
        if kind == "docx":
            total = int(stats.get("paragraphs", 0)) + int(stats.get("tables", 0))
        elif kind == "md":
            text = data.decode("utf-8", "replace")
            first = re.search(r"(?m)^#{1,4} .*$", text)
            intro = text[:first.start()] if first else text
            total = len(re.findall(r"(?m)^#{1,4} .*$", text)) + (1 if intro.strip() else 0)
        elif kind == "pptx":
            import io
            import zipfile
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                total = sum(1 for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
        elif kind == "pdf":
            import io
            from pypdf import PdfReader
            total = len(PdfReader(io.BytesIO(data)).pages)
    except Exception:  # noqa: BLE001 - an estimate only; "more than kept, unknown" is still honest
        total = 0
    return {"unit": _UNITS.get(kind, "частей"), "kept": kept, "total": total if total > kept else 0}


def parse_in_process(path: str, name: str = "") -> list[tuple[str, str]]:
    """(ref, text) pairs through the existing file_intel parsers; a sniffed type beats the claimed one.

    This is THE parse and it must only ever run inside the child process (see ``extract_sections``): it is the
    code that hostile input can make slow. It also owns every refusal that depends on the file's content.
    The result is a ``_Sections``: its ``partial`` says when the parser kept only the first part of the file."""
    from bossman.file_intel import ParseUnavailable, parse_file

    data = Path(path).read_bytes()
    _refuse_zip_bomb(data)
    stats = _prescan_office_xml(data)
    try:
        art = parse_file(path)
    except ParseUnavailable as exc:
        raise LibraryError("parser_unavailable", f"Этот формат пока не читается на этой установке: {exc}") from None
    except (MemoryError, RecursionError):
        raise _too_complex() from None
    except Exception:  # noqa: BLE001 - malformed xml / zip / json from any parser is a refusal, never a crash
        raise _unreadable() from None
    sections = _Sections()
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
    try:
        sections.partial = _truncation(art, data, stats)
    except Exception:  # noqa: BLE001 - detection never turns a readable file into a refusal
        sections.partial = None
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
    decides sys.path and this is simply ignored.

    The rest of the environment is an ALLOW-LIST (``_ENV_ALLOW``): a token, an API key or a BOSSMAN_* variable of the
    Jeff process is not inherited by the process that opens a stranger's file."""
    env = {name: value for name, value in os.environ.items() if name.upper() in _ENV_ALLOW}
    inherited = os.environ.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([*_PACKAGE_ROOTS, *([inherited] if inherited else [])])
    env["PYTHONUTF8"] = "1"
    env[MEMORY_ENV] = str(int(CHILD_MEMORY_BYTES))
    return env


# -- resource ceilings of the child ---------------------------------------------------------------
_REPORTED: set[str] = set()


def _unavailable(reason: str) -> None:
    """A ceiling that could not be applied is logged once per reason, never raised: parsing still has the wall-clock
    limit and the process kill, which is what it had before the ceilings existed."""
    if reason not in _REPORTED:
        _REPORTED.add(reason)
        log.warning("jeff document child: resource ceiling not applied (%s)", reason)


class _Job:
    """A Windows Job Object handle; ``close`` ends the job, which kills whatever is still inside it."""

    def __init__(self, api, handle):
        self._api, self._handle = api, handle

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle:
            try:
                self._api.CloseHandle(handle)
            except Exception:  # noqa: BLE001
                pass


def _win_make_job(memory_bytes: int, cpu_seconds: int):
    """A Job Object with: committed memory per process, CPU time per process, kill-on-close, no crash dialog.
    Returns ``(kernel32, handle)``; raises OSError / AttributeError where the API is not there."""
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                      "OtherOperationCount", "ReadTransferCount",
                                                      "WriteTransferCount", "OtherTransferCount")]

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    info = Extended()
    # PROCESS_TIME 0x2 | PROCESS_MEMORY 0x100 | DIE_ON_UNHANDLED_EXCEPTION 0x400 | KILL_ON_JOB_CLOSE 0x2000
    info.BasicLimitInformation.LimitFlags = 0x2 | 0x100 | 0x400 | 0x2000
    info.BasicLimitInformation.PerProcessUserTimeLimit = int(cpu_seconds) * 10_000_000      # 100 ns units
    info.ProcessMemoryLimit = int(memory_bytes)
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):   # JobObjectExtendedLimit
        error = ctypes.get_last_error()
        k32.CloseHandle(job)
        raise OSError(error, "SetInformationJobObject failed")
    return k32, job


def _limit_child_process(proc: subprocess.Popen):
    """Parent side, right after the spawn. Windows: put the child (and, by inheritance, what it starts) into a Job
    Object: a memory ceiling, a CPU ceiling, and it dies with this process (``KILL_ON_JOB_CLOSE``: a parent that
    crashed leaves no orphan). POSIX applies its limits inside the child (see ``apply_child_limits``): a
    ``preexec_fn`` is not safe with the threads this is called from. Returns a ``_Job`` or None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        k32, job = _win_make_job(CHILD_MEMORY_BYTES, CHILD_CPU_SECONDS)
        owned = _Job(k32, job)
        process = k32.OpenProcess(0x0101, False, proc.pid)         # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if not process:
            owned.close()
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        try:
            if not k32.AssignProcessToJobObject(job, process):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        except BaseException:
            owned.close()
            raise
        finally:
            k32.CloseHandle(process)
        return owned
    except Exception as exc:  # noqa: BLE001 - an old Windows, a restricted token, a missing API: fall back, say so
        _unavailable(f"job object: {type(exc).__name__}: {exc}")
        return None


def apply_child_limits() -> str:
    """Child side, first thing in ``doc_extract_child.main``: ceilings on THIS process, so they hold from the first
    parsed byte (the parent's Job Object is assigned a moment after the spawn). Memory comes from ``MEMORY_ENV``.
    Returns "" when everything applied, otherwise the reason of the first failure (the child carries on)."""
    try:
        memory = int(os.environ.get(MEMORY_ENV) or CHILD_MEMORY_BYTES)
    except ValueError:
        memory = CHILD_MEMORY_BYTES
    problems: list[str] = []
    if os.name == "nt":
        try:
            import ctypes
            k32, job = _win_make_job(memory, CHILD_CPU_SECONDS)        # nested under the parent's job on Windows 8+
            if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"job object: {type(exc).__name__}: {exc}")
        return "; ".join(problems)
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (CHILD_CPU_SECONDS, CHILD_CPU_SECONDS))
    except Exception as exc:  # noqa: BLE001 - macOS refuses RLIMIT_AS; the wall-clock kill still holds
        problems.append(f"rlimit: {type(exc).__name__}: {exc}")
    if sys.platform.startswith("linux"):
        try:                                         # the child dies with the parent: no orphan keeps parsing
            import ctypes
            ctypes.CDLL(None).prctl(1, int(signal.SIGKILL), 0, 0, 0)      # PR_SET_PDEATHSIG
        except Exception as exc:  # noqa: BLE001
            problems.append(f"pdeathsig: {type(exc).__name__}")
    return "; ".join(problems)


def sweep_stale_temp(max_age: float = STALE_TEMP_SECONDS, now: float | None = None,
                     root: Path | str | None = None) -> int:
    """Delete plaintext temp copies a crashed parse left behind: ``jdoc-*`` files in OUR temp root only, older than
    ``max_age``. Nothing else in the directory is looked at. Returns how many were removed."""
    base = Path(root) if root is not None else Path(tempfile.gettempdir())
    now = time.time() if now is None else now
    removed = 0
    try:
        entries = list(os.scandir(base))
    except OSError:
        return 0
    for entry in entries:
        if not re.fullmatch(r"jdoc-[a-z0-9_]+\.[a-z0-9]{1,7}", entry.name):
            continue
        try:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                continue
            if now - entry.stat(follow_symlinks=False).st_mtime < max_age:
                continue
            os.unlink(entry.path)
            removed += 1
        except OSError:
            continue
    return removed


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
    job = _limit_child_process(proc)                        # memory / CPU ceilings, dies with this process
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
    finally:
        if job is not None:
            job.close()                                     # ends the job: nothing the child started outlives it
    if len(out) > MAX_CHILD_OUTPUT_BYTES:
        raise _too_complex()
    try:
        lines = [line for line in out.decode("utf-8").splitlines() if line.strip()]
        reply = json.loads(lines[-1])
        if reply.get("ok") is True:
            sections = _Sections((str(ref), str(text)) for ref, text in reply["sections"])
            cut = reply.get("partial")
            if isinstance(cut, dict):
                sections.partial = {"unit": str(cut.get("unit") or "частей"), "kept": int(cut.get("kept") or 0),
                                    "total": int(cut.get("total") or 0)}
            return sections
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
        return [_record(d) for d in self._read_index()]

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
                    return _record(d)
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
            cut = getattr(sections, "partial", None)
            if isinstance(cut, dict) and cut.get("kept"):
                # The parser kept only the first part of the file: the record says so, and so does every reply.
                rec.update(partial=True, kept=int(cut["kept"]), total=int(cut.get("total") or 0),
                           unit=str(cut.get("unit") or "частей"))
            docs.append(rec)
            self._write_index(docs)
        return _record(rec), True

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
        scores = bm25_scores(q, [c for _, _, c in corpus])
        scored: list[Hit] = []
        for (did, chunk, _c), score in zip(corpus, scores):
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
