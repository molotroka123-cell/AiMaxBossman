"""Per-person document library with BM25 retrieval and citations (Jeff paid document assistant).

* Storage lives INSIDE the participant's own vault directory (``<person_dir>/docs``), so another person's
  documents are not reachable by construction and ``/delete_me`` (vault.delete) removes them with the rest.
* Text comes from the existing ``bossman.file_intel`` parsers (txt/md/csv/json/docx/xlsx/pptx/epub, PDF through
  ``pypdf`` when installed). A format the install cannot read is refused honestly, never guessed.
* Retrieval is plain BM25 over this person's chunks. No embeddings, no network, no model.
* A citation names the document and the place (page / sheet / fragment) and carries the exact stored quote, so the
  caller can show the user what the answer rests on.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
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
    if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
        raise LibraryError("too_long", "Файл слишком большой после распаковки — такой не принимаю.")


def _forget_parsed(data: bytes) -> None:
    """The shared parse cache must not keep a person's document text after the library took it (or refused it)."""
    try:
        from bossman.exec_cache import get_cache
        cache = get_cache()
        cache.invalidate(cache.key("parsed_file", hashlib.sha256(data).hexdigest()))
    except Exception:  # noqa: BLE001 - cache hygiene never blocks a refusal or a store
        pass


def extract_sections(name: str, data: bytes) -> list[tuple[str, str]]:
    """(ref, text) pairs through the existing file_intel parsers; a sniffed type beats the claimed one."""
    from bossman.file_intel import ParseUnavailable, parse_file

    _refuse_zip_bomb(data)
    suffix = Path(name).suffix.lower()[:8] or ".txt"
    if not re.fullmatch(r"\.[a-z0-9]{1,7}", suffix):
        suffix = ".txt"
    fd, tmp = tempfile.mkstemp(prefix="jdoc-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        try:
            art = parse_file(tmp)
        except ParseUnavailable as exc:
            raise LibraryError("parser_unavailable", f"Этот формат пока не читается на этой установке: {exc}") from None
        except (MemoryError, RecursionError):
            raise LibraryError("too_long", "Файл слишком сложный для разбора.") from None
        except Exception:  # noqa: BLE001 - malformed xml / zip / json from any parser is a refusal, never a crash
            raise LibraryError("unreadable", "Не удалось прочитать файл: он повреждён или имеет неподдерживаемый вид.") from None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        _forget_parsed(data)
    sections: list[tuple[str, str]] = []
    for sec in art.sections:
        if sec.table:
            body = "\n".join(" | ".join(str(c) for c in row) for row in sec.table)
        else:
            body = sec.text
        body = body.replace("\x00", "").strip()
        if body:
            sections.append((str(sec.ref or ""), body))
    return sections


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

    def _write_index(self, docs: list[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "docs": docs}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._index_path)

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
    def add(self, name: str, data: bytes, *, limit: int, now: float | None = None) -> tuple[DocRecord, bool]:
        """Store one document; returns (record, is_new). Re-uploading identical bytes is free and not counted."""
        now = time.time() if now is None else now
        digest = hashlib.sha256(data).hexdigest()
        docs = self._read_index()
        for d in docs:
            if d["sha256"] == digest:
                return DocRecord(d["id"], d["name"], d["sha256"], d["chars"], d["chunks"], d["added_at"]), False
        if len(docs) >= limit:
            raise LibraryError("doc_limit", f"Достигнут лимит документов: {limit}. Удалите лишний: /deldoc <номер>.")
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
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._chunk_path(doc_id).with_suffix(".tmp")
        tmp.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in chunks), encoding="utf-8")
        os.replace(tmp, self._chunk_path(doc_id))
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
