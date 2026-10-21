"""The knowledge library: copy-only file manager + manifest + immediate parse + memory notes.

Layout under the library root (default ``<data dir>\\library``, configurable):

    library.json                              manifest (atomic writes, lock-protected)
    category_rules.json                       owner-editable category rules
    <category>/<YYYY-MM>/<slug>__<sha8>.<ext> the COPY of the original (originals are never moved or deleted)
    <category>/<YYYY-MM>/<slug>__<sha8>.parsed/pages.json      parsed units (re-render without re-parsing)
    <category>/<YYYY-MM>/<slug>__<sha8>.parsed/notes/*.md      Markdown notes (same as in the vault)

The manifest lock is held only for the read-modify-write of ``library.json``;
parsing and copying happen outside it, and the manifest is re-read under the
lock, so two intakes (watch + manual CLI) never lose each other's entries.
"""
from __future__ import annotations

import json
import os
import re
import stat
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import parsers, safety
from .config import DEFAULT_MAX_BYTES
from .fsutil import FileLock, LockBusy, SourceChanged, atomic_write_text, copy_verified, sha256_file
from .memory import MemorySink, MemoryUnavailable
from .notes import DEFAULT_NOTE_CHARS, DEFAULT_NOTE_UNITS, Note, clean_tag, render_notes
from .rules import RulesError, categorize, load_rules, valid_category

MANIFEST = "library.json"
LOCK = ".library.lock"
VERSION = 1

ADDED, DUPLICATE, REFUSED, ERROR = "added", "duplicate", "refused", "error"
MEM_DISABLED, MEM_PENDING, MEM_WRITTEN, MEM_INDEXED = "disabled", "pending", "written", "indexed"

_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_JUNK_TITLE = re.compile(r"^(microsoft (word|excel|powerpoint) - |untitled|без имени|документ\d*$|document\d*$"
                         r"|presentation\d*$|powerpoint presentation$|slide \d+$)", re.I)


class LibraryError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str, max_len: int = 48) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = re.sub(r"[^\w\s-]", " ", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")[:max_len].strip("-")
    if not text:
        return "document"
    return f"doc-{text}" if text in _RESERVED else text


def pick_title(meta_title: str, path: Path) -> str:
    title = " ".join((meta_title or "").split())
    if (4 <= len(title) <= 150 and not _JUNK_TITLE.match(title)
            and not re.search(r"\.(docx?|pdf|xlsx?|pptx?)$", title, re.I)):
        return title
    stem = " ".join(path.stem.replace("_", " ").split())
    return stem[:150] or path.name


def clean_tags(tags) -> list[str]:
    out: list[str] = []
    for tag in tags or []:
        for part in str(tag).split(","):
            part = clean_tag(part)
            if part and part not in out:
                out.append(part)
    return out


@dataclass
class Settings:
    library_root: Path
    max_bytes: int = DEFAULT_MAX_BYTES
    max_pdf_pages: int = parsers.MAX_PDF_PAGES
    note_chars: int = DEFAULT_NOTE_CHARS
    note_units: int = DEFAULT_NOTE_UNITS
    use_docling: bool = False
    use_ocr: bool = False


@dataclass
class Outcome:
    status: str                 # added | duplicate | refused | error
    path: str
    id: str = ""
    reason: str = ""
    entry: dict | None = None
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return {"status": self.status, "path": self.path, "id": self.id, "reason": self.reason,
                "seconds": round(self.seconds, 2), "entry": self.entry}

    def line(self) -> str:
        name = Path(self.path).name
        if self.status == ADDED and self.entry:
            e = self.entry
            pages = f"{e['pages']} стр., " if e.get("pages") else ""
            mem = (e.get("memory") or {}).get("state", "")
            return (f"ПРИНЯТ {e['id']} «{e['title']}» → {e['category']} · {pages}{len(e['notes'])} заметок · "
                    f"разбор {e['parse_status']} ({e['parser']}) · память: {mem} · {self.seconds:.1f} с")
        if self.status == DUPLICATE:
            return f"ДУБЛИКАТ {name}: {self.reason} (id {self.id})"
        return f"{'ОТКАЗ' if self.status == REFUSED else 'ОШИБКА'} {name}: {self.reason}"


class Library:
    def __init__(self, settings: Settings, memory: MemorySink | None = None):
        self.s = settings
        self.root = Path(settings.library_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.memory = memory

    # ------------------------------------------------------------------ manifest
    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST

    def load(self) -> dict:
        path = self.manifest_path
        if not path.exists():
            return {"version": VERSION, "entries": {}, "memory": {}}
        for attempt in range(5):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                break
            except PermissionError:          # Windows: a concurrent os.replace in flight
                if attempt == 4:
                    raise
                time.sleep(0.05)
            except (OSError, ValueError) as exc:
                raise LibraryError(f"манифест повреждён или недоступен: {path} ({type(exc).__name__}); "
                                   "файл НЕ перезаписан — восстановите его или перенесите в сторону") from None
        if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
            raise LibraryError(f"манифест {path} имеет неверную структуру; файл НЕ перезаписан")
        data.setdefault("memory", {})
        return data

    def _save(self, data: dict) -> None:
        data["version"] = VERSION
        data["updated"] = now_iso()
        atomic_write_text(self.manifest_path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")

    @contextmanager
    def transaction(self) -> Iterator[dict]:
        """Lock, re-read, let the caller mutate, write atomically. An exception writes nothing."""
        with FileLock(self.root / LOCK):
            data = self.load()
            yield data
            self._save(data)

    def get(self, ref: str, data: dict | None = None) -> dict:
        data = data if data is not None else self.load()
        entries = data["entries"]
        if ref in entries:
            return entries[ref]
        matches = [e for k, e in entries.items() if len(ref) >= 4 and (k.startswith(ref) or e["sha256"].startswith(ref))]
        if len(matches) == 1:
            return matches[0]
        raise LibraryError(f"запись {ref!r} не найдена" if not matches
                           else f"{ref!r} неоднозначен: подходит {len(matches)} записей")

    @staticmethod
    def _by_sha(data: dict, sha: str) -> dict | None:
        return next((e for e in data["entries"].values() if e["sha256"] == sha), None)

    def entries(self, category: str | None = None) -> list[dict]:
        items = list(self.load()["entries"].values())
        if category:
            items = [e for e in items if e["category"] == category]
        return sorted(items, key=lambda e: e["created"], reverse=True)

    # ------------------------------------------------------------------ intake
    def intake(self, path: str | Path, *, tags=(), index: bool = True) -> Outcome:
        """Accept one file: refuse / dedupe / copy / parse / notes / memory / manifest."""
        started = time.monotonic()
        src = Path(path).expanduser()
        try:
            st = src.stat()
            src = src.resolve()
        except OSError as exc:
            return Outcome(REFUSED, str(src), reason=f"файл недоступен: {exc.strerror or type(exc).__name__}")
        if not stat.S_ISREG(st.st_mode):
            return Outcome(REFUSED, str(src), reason="это не обычный файл")
        if self.root in src.parents:
            return Outcome(REFUSED, str(src), reason="файл уже лежит внутри библиотеки")
        refusal = safety.check(src, st.st_size, self.s.max_bytes)
        if refusal:
            return Outcome(REFUSED, str(src), reason=refusal.reason)
        try:
            sha = sha256_file(src)
        except OSError as exc:
            return Outcome(ERROR, str(src), reason=f"файл не читается: {exc.strerror or type(exc).__name__}")
        existing = self._by_sha(self.load(), sha)
        if existing:
            entry = self._alias(existing["id"], src)
            return Outcome(DUPLICATE, str(src), existing["id"], reason=f"уже в библиотеке как «{existing['title']}»",
                           entry=entry, seconds=time.monotonic() - started)
        try:
            rules, _ = load_rules(self.root)
        except RulesError as exc:
            return Outcome(ERROR, str(src), reason=str(exc))

        kind = safety.kind_of(src.suffix)
        result = parsers.parse(src, kind, max_pages=self.s.max_pdf_pages, use_docling=self.s.use_docling,
                               use_ocr=self.s.use_ocr)
        title = pick_title(result.title, src)
        decision = categorize(rules, name=src.name, title=title, text=result.text(), ext=src.suffix.lower())
        slug = slugify(title)
        base = f"{slug}__{sha[:8]}"
        rel_dir = f"{decision.category}/{datetime.now().strftime('%Y-%m')}"
        entry = {
            "id": sha[:12], "title": title, "slug": slug, "original_name": src.name, "source_path": str(src),
            "aliases": [], "sha256": sha, "size": st.st_size, "ext": src.suffix.lower(), "kind": kind,
            "category": decision.category, "category_by": decision.by,
            "category_matched": list(decision.matched[:10]), "tags": clean_tags(tags),
            "file": f"{rel_dir}/{base}{src.suffix.lower()}", "parsed_dir": f"{rel_dir}/{base}.parsed",
            "created": now_iso(), "notes": [], "memory": {},
        }
        try:
            copy_verified(src, self.root / entry["file"], sha)
        except SourceChanged as exc:
            return Outcome(ERROR, str(src), reason=str(exc))
        except OSError as exc:
            return Outcome(ERROR, str(src), reason=f"копирование в библиотеку не удалось: {exc.strerror or type(exc).__name__}")
        notes = self._store_parse(entry, result)
        entry["memory"] = self._write_memory(entry, notes)
        entry["intake_seconds"] = round(time.monotonic() - started, 2)
        try:
            with self.transaction() as data:
                raced = self._by_sha(data, sha)
                if raced is None:
                    data["entries"][entry["id"]] = entry
                    self._remember_vault(data, entry)
        except (OSError, LockBusy, LibraryError) as exc:
            # The previous manifest is intact (atomic replace). The copy and notes are
            # re-used by the next intake of the same file (same deterministic paths).
            return Outcome(ERROR, str(src), reason=f"манифест не обновлён: {type(exc).__name__}: {exc}"[:300])
        if raced is not None:
            return Outcome(DUPLICATE, str(src), raced["id"], reason="тот же файл только что принят другим процессом",
                           entry=raced, seconds=time.monotonic() - started)
        if index and entry["memory"].get("state") == MEM_WRITTEN:
            self.index_now()
            entry = self.get(entry["id"])
        return Outcome(ADDED, str(src), entry["id"], entry=entry, seconds=time.monotonic() - started)

    def intake_many(self, paths, *, tags=()) -> list[Outcome]:
        """Several files, one memory index call at the end."""
        outcomes = [self.intake(p, tags=tags, index=False) for p in paths]
        if any(o.status == ADDED and (o.entry or {}).get("memory", {}).get("state") == MEM_WRITTEN for o in outcomes):
            self.index_now()
            outcomes = [Outcome(o.status, o.path, o.id, o.reason, self.get(o.id), o.seconds)
                        if o.status == ADDED else o for o in outcomes]
        return outcomes

    def _alias(self, lib_id: str, src: Path) -> dict | None:
        with self.transaction() as data:
            entry = data["entries"].get(lib_id)
            if entry is None:
                return None
            if src.name != entry["original_name"] and src.name not in entry["aliases"]:
                entry["aliases"].append(src.name)
            entry["duplicates_seen"] = int(entry.get("duplicates_seen", 0)) + 1
            entry["last_seen"] = now_iso()
        return entry

    # ------------------------------------------------------------------ parse + notes
    def _store_parse(self, entry: dict, result: parsers.ParseResult) -> list[Note]:
        parsed_dir = self.root / entry["parsed_dir"]
        notes = render_notes(entry, result, max_chars=self.s.note_chars, max_units=self.s.note_units)
        atomic_write_text(parsed_dir / "pages.json", json.dumps(result.to_json(), ensure_ascii=False))
        notes_dir = parsed_dir / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        keep = set()
        for note in notes:
            atomic_write_text(notes_dir / note.filename, note.text)
            keep.add(note.filename)
        for old in notes_dir.glob("*.md"):
            if old.name not in keep:
                old.unlink()
        entry.update({
            "parse_status": result.status, "parser": result.parser, "parse_reason": result.reason,
            "warnings": result.warnings, "tables": result.tables, "pages": result.page_count,
            "units": len(result.units), "unit_kind": result.units[0].kind if result.units else "",
            "empty_pages": result.meta.get("empty_pages", []), "chars": len(result.text()),
            "parsed": now_iso(), "notes": [f"{entry['parsed_dir']}/notes/{n.filename}" for n in notes],
        })
        return notes

    def _parse_result(self, entry: dict) -> parsers.ParseResult:
        path = self.root / entry["parsed_dir"] / "pages.json"
        try:
            return parsers.ParseResult.from_json(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:
            raise LibraryError(f"нет сохранённого разбора {path.name} ({type(exc).__name__}): выполните reparse") from None

    # ------------------------------------------------------------------ memory
    def _vault_dir(self, entry: dict, data: dict) -> str:
        current = (entry.get("memory") or {}).get("vault_dir") or ""
        if current.startswith(entry["category"] + "/"):
            return current
        candidate = f"{entry['category']}/{entry['slug']}"
        taken = {(e.get("memory") or {}).get("vault_dir") for k, e in data["entries"].items() if k != entry["id"]}
        return candidate if candidate not in taken else f"{candidate}-{entry['sha256'][:8]}"

    def _write_memory(self, entry: dict, notes: list[Note]) -> dict:
        previous = dict(entry.get("memory") or {})
        if self.memory is None or not self.memory.enabled:
            if previous.get("state") in (MEM_WRITTEN, MEM_INDEXED):
                return previous           # dry-run must not forget notes already in the vault
            return {"state": MEM_DISABLED, "written": False, "indexed": False, "vault_paths": [],
                    "reason": "запись в память выключена (--no-memory)"}
        data = self.load()
        try:
            target = self.memory.resolve(data.get("memory"))
        except MemoryUnavailable as exc:
            return {**previous, "state": MEM_PENDING, "written": False, "indexed": False,
                    "vault_paths": previous.get("vault_paths", []), "reason": str(exc)[:300]}
        vault_dir = self._vault_dir(entry, data)
        try:
            written, skipped, kinds = self.memory.write_notes(target, vault_dir, notes,
                                                              source_name=entry["original_name"])
        except (OSError, ImportError) as exc:
            return {**previous, "state": MEM_PENDING, "written": False, "indexed": False,
                    "reason": f"заметки не записаны в vault: {type(exc).__name__}: {exc}"[:300]}
        stale = [p for p in previous.get("vault_paths", []) if p not in written]
        if stale and previous.get("vault_root") == str(target.root):
            self.memory.remove(target, stale)
        return {"state": MEM_WRITTEN, "written": True, "indexed": False, "vault_root": str(target.root),
                "write_folder": target.write_folder, "vault_dir": vault_dir, "vault_paths": written,
                "vault_source": target.source, "skipped_secret_notes": skipped, "redactions": kinds,
                "written_at": now_iso(), "reason": "", "index_error": ""}

    @staticmethod
    def _remember_vault(data: dict, entry: dict) -> None:
        mem = entry.get("memory") or {}
        if mem.get("vault_root"):
            data["memory"] = {"vault_root": mem["vault_root"], "write_folder": mem.get("write_folder", ""),
                              "seen": now_iso()}

    def index_now(self) -> tuple[bool, str]:
        """One call to the live memory index; marks every written entry of that vault."""
        if self.memory is None or not self.memory.enabled:
            return False, "память выключена"
        try:
            target = self.memory.resolve(self.load().get("memory"))
        except MemoryUnavailable as exc:
            ok, detail, root = False, str(exc), ""
        else:
            ok, detail = self.memory.index(target)
            root = str(target.root)
        with self.transaction() as data:
            for entry in data["entries"].values():
                mem = entry.get("memory") or {}
                if mem.get("state") != MEM_WRITTEN:
                    continue
                if ok and mem.get("vault_root") == root:
                    mem.update(state=MEM_INDEXED, indexed=True, indexed_at=now_iso(), index_error="")
                elif not ok:
                    mem.update(indexed=False, index_error=detail[:300], index_attempt_at=now_iso())
        return ok, detail

    def reindex(self, refs=None, *, include_disabled: bool = False, rewrite: bool = False) -> dict:
        """Write missing/pending vault notes from the library, then index once."""
        if self.memory is None or not self.memory.enabled:
            raise LibraryError("память выключена: запустите без --no-memory")
        data = self.load()
        chosen = [self.get(r, data) for r in refs] if refs else list(data["entries"].values())
        rewritten = 0
        for entry in chosen:
            mem = entry.get("memory") or {}
            state = mem.get("state")
            if state == MEM_DISABLED and not (include_disabled or refs):
                continue
            root = mem.get("vault_root")
            missing = not mem.get("vault_paths") or not root or any(
                not (Path(root) / p).exists() for p in mem.get("vault_paths", []))
            if rewrite or state in (MEM_PENDING, MEM_DISABLED) or missing:
                notes = render_notes(entry, self._parse_result(entry), max_chars=self.s.note_chars,
                                     max_units=self.s.note_units)
                new_mem = self._write_memory(entry, notes)
                with self.transaction() as d:
                    d["entries"][entry["id"]]["memory"] = new_mem
                    self._remember_vault(d, d["entries"][entry["id"]])
                rewritten += 1
        ok, detail = self.index_now()
        pending = sum(1 for e in self.load()["entries"].values()
                      if (e.get("memory") or {}).get("state") in (MEM_PENDING, MEM_WRITTEN))
        return {"rewritten": rewritten, "indexed": ok, "detail": detail, "pending": pending}

    # ------------------------------------------------------------------ file manager
    def _rerender(self, lib_id: str, result: parsers.ParseResult | None = None) -> dict:
        entry = dict(self.get(lib_id))
        result = result or self._parse_result(entry)
        notes = self._store_parse(entry, result)
        if (entry.get("memory") or {}).get("state") != MEM_DISABLED:
            entry["memory"] = self._write_memory(entry, notes)
        with self.transaction() as data:
            if lib_id not in data["entries"]:
                raise LibraryError(f"запись {lib_id} удалена во время операции")
            data["entries"][lib_id] = entry
            self._remember_vault(data, entry)
        if entry["memory"].get("state") == MEM_WRITTEN:
            self.index_now()
        return self.get(lib_id)

    def retag(self, ref: str, *, add=(), remove=(), set_to=None) -> dict:
        with self.transaction() as data:
            entry = self.get(ref, data)
            base = clean_tags(set_to) if set_to is not None else list(entry.get("tags", []))
            drop = set(clean_tags(remove))
            entry["tags"] = [t for t in clean_tags(base + clean_tags(add)) if t not in drop]
            lib_id = entry["id"]
        return self._rerender(lib_id)

    def move_category(self, ref: str, category: str) -> dict:
        if not valid_category(category):
            raise LibraryError("категория: латиница/цифры/-/_ , до 40 знаков (например finance, legal)")
        entry = dict(self.get(ref))
        if entry["category"] == category:
            return entry
        month = entry["file"].split("/")[1]
        new_file = f"{category}/{month}/{Path(entry['file']).name}"
        new_parsed = f"{category}/{month}/{Path(entry['parsed_dir']).name}"
        for rel in (new_file, new_parsed):
            if (self.root / rel).exists():
                raise LibraryError(f"в категории {category} уже есть {rel}")
        (self.root / new_file).parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.root / entry["file"], self.root / new_file)
        os.replace(self.root / entry["parsed_dir"], self.root / new_parsed)
        old_dir = (self.root / entry["file"]).parent
        for folder in (old_dir, old_dir.parent):
            try:
                folder.rmdir()                 # only if empty
            except OSError:
                break
        entry.update(category=category, category_by="owner", file=new_file, parsed_dir=new_parsed,
                     notes=[n.replace(entry["parsed_dir"], new_parsed, 1) for n in entry["notes"]])
        with self.transaction() as data:
            data["entries"][entry["id"]] = entry
        return self._rerender(entry["id"])

    def reparse(self, ref: str, *, recategorize: bool = False) -> dict:
        entry = self.get(ref)
        copy = self.root / entry["file"]
        if sha256_file(copy) != entry["sha256"]:
            raise LibraryError(f"копия в библиотеке изменена или повреждена: {copy}")
        result = parsers.parse(copy, entry["kind"], max_pages=self.s.max_pdf_pages,
                               use_docling=self.s.use_docling, use_ocr=self.s.use_ocr)
        updated = self._rerender(entry["id"], result)
        if recategorize:
            rules, _ = load_rules(self.root)
            decision = categorize(rules, name=entry["original_name"], title=entry["title"], text=result.text(),
                                  ext=entry["ext"])
            if decision.category != updated["category"]:
                updated = self.move_category(entry["id"], decision.category)
        return updated

    # ------------------------------------------------------------------ search
    def search(self, query: str, *, limit: int = 20, per_doc: int = 3) -> list[dict]:
        needle = " ".join((query or "").split()).casefold()
        if not needle:
            raise LibraryError("пустой запрос")
        words = needle.split()
        hits = []
        for entry in self.entries():
            meta = " ".join([entry["title"], entry["original_name"], *entry.get("aliases", []),
                             *entry.get("tags", []), entry["category"]]).casefold()
            bodies = []
            for rel in entry.get("notes", []):
                try:
                    text = (self.root / rel).read_text(encoding="utf-8")
                except OSError:
                    continue
                bodies.append((rel, text.split("\n---\n", 1)[-1]))
            blob = meta + "\n" + "\n".join(body.casefold() for _, body in bodies)
            if not all(word in blob for word in words):
                continue
            snippets = []
            for rel, body in bodies:
                low = body.casefold()
                probe = needle if needle in low else words[0]
                pos = low.find(probe)
                while pos >= 0 and len(snippets) < per_doc:
                    head = body.rfind("\n### ", 0, pos)
                    where = body[head + 5: body.find("\n", head + 5)].strip() if head >= 0 else ""
                    start, end = max(0, pos - 90), pos + len(probe) + 90
                    snippets.append({"where": where, "note": rel,
                                     "text": ("…" if start else "") + " ".join(body[start:end].split()) + "…"})
                    pos = low.find(probe, end)
                if len(snippets) >= per_doc:
                    break
            hits.append({"id": entry["id"], "title": entry["title"], "category": entry["category"],
                         "pages": entry.get("pages"), "match": "text" if snippets else "meta", "snippets": snippets})
            if len(hits) >= limit:
                break
        return hits
