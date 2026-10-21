from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Any

MemoryKind = Literal["decision", "lesson", "fact", "task", "session", "note"]
MEMORY_KINDS: tuple[str, ...] = ("decision", "lesson", "fact", "task", "session", "note")


_FM_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}")
_FM_RESERVED = frozenset({"title", "kind", "created", "source", "project", "tags", "source_run_id", "redactions"})


def _one_line(value: str) -> str:
    """A frontmatter scalar: no line breaks (incl. U+2028/U+0085), single spaces."""
    return " ".join(str(value or "").split())

DEFAULT_EXCLUDES = {
    ".obsidian",
    ".trash",
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
}

def safe_slug(value: str) -> str:
    value = re.sub(r"[^\w\- ]+", "", value, flags=re.UNICODE).strip()
    value = re.sub(r"\s+", "-", value)
    return value[:80] or "memory"

def _atomic_write(dest: Path, body: str) -> None:
    """Записать заметку целиком или не записать вовсе.

    Прямой `write_text` открывает файл на запись и усекает его ДО того, как
    что-то записано. Падение процесса в этот момент оставляет обрезанную
    заметку — и это худший исход из возможных: не отказ, который заметят, а
    тихо испорченный источник истины. Восстановить его нечем: производные
    индексы пересобираются из заметок, а заметки — ни из чего.

    Временный файл лежит в том же каталоге: `os.replace` атомарен только в
    пределах одной файловой системы.
    """
    tmp = dest.with_name(dest.name + f".tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())      # иначе «записано» означает лишь «в кэше»
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


@dataclass(slots=True)
class ObsidianVault:
    root: Path
    index_folders: list[str] = field(default_factory=lambda: ["."])
    write_folder: str = "BOSSMAN Memory"
    excluded_dirs: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDES))

    def __post_init__(self):
        self.root = self.root.expanduser().resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"Obsidian vault not found: {self.root}")

    def _inside(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    def markdown_roots(self) -> list[Path]:
        roots: list[Path] = []
        for rel in self.index_folders:
            p = (self.root / rel).resolve()
            if not self._inside(p):
                raise PermissionError(f"index folder escapes vault: {rel}")
            if p.exists():
                roots.append(p)
        return roots

    def iter_markdown(self) -> Iterable[Path]:
        seen: set[Path] = set()
        for base in self.markdown_roots():
            for p in base.rglob("*.md"):
                if any(part in self.excluded_dirs for part in p.parts):
                    continue
                rp = p.resolve()
                if rp not in seen:
                    seen.add(rp)
                    yield rp

    @property
    def write_root(self) -> Path:
        p = (self.root / self.write_folder).resolve()
        if not self._inside(p):
            raise PermissionError("write folder escapes vault")
        p.mkdir(parents=True, exist_ok=True)
        return p

    def write_memory(
        self,
        *,
        title: str,
        content: str,
        kind: MemoryKind = "note",
        project: str = "",
        tags: list[str] | None = None,
        source_run_id: str | int | None = None,
        filename: str | None = None,
        extra_frontmatter: dict[str, str] | None = None,
    ) -> Path:
        """Write a new BOSSMAN-owned note.

        Does not edit arbitrary existing vault notes.

        Secrets never reach the vault: every free-text field goes through the
        same pre-write filter as the hybrid context store
        (`bcc.hybrid.context_filter.scrub_for_memory`). A token inside useful
        text is replaced by a typed `[REDACTED:<kind>]` mark and the kinds are
        recorded in the frontmatter; a note that is nothing but secret
        material is refused (`SecretMaterialRefused`, a ValueError). The
        markdown note is the source of truth that every derived index is
        rebuilt from, so a secret here would outlive any index cleanup.
        """
        from ...hybrid.context_filter import scrub_for_memory

        redactions: set[str] = set()

        def _clean(text: str, *, allow_empty: bool = True) -> str:
            if not text:
                return text
            try:
                res = scrub_for_memory(text)
            except ValueError:
                if allow_empty:          # a secret-only title/tag: drop it, keep the note
                    redactions.add("field_dropped")
                    return ""
                raise
            redactions.update(res.redactions)
            return res.body

        # Provenance lives in the frontmatter (`kind`, `source: bossman`,
        # `source_run_id`). A newline in any single-line field used to start a
        # new YAML key — `kind="note\nsource: owner"` forged owner provenance,
        # a title with "\n---\n" ended the frontmatter early. Kind is a closed
        # set; every single-line field is collapsed to one line.
        if kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind {kind!r}; allowed: {', '.join(MEMORY_KINDS)}")
        content = _clean(str(content or ""), allow_empty=False)
        title = _one_line(_clean(str(title or ""))) or "memory"
        project = _one_line(_clean(str(project or "")))
        tags = [t for t in (_one_line(_clean(str(t))).strip("[]") for t in (tags or [])) if t]
        if source_run_id is not None:
            source_run_id = _one_line(str(source_run_id)) or None
        now = datetime.now(timezone.utc)
        stem = filename or f"{now:%Y-%m-%d-%H%M}-{safe_slug(title)}.md"
        dest = (self.write_root / stem).resolve()
        if not self._inside(dest) or self.write_root not in dest.parents:
            raise PermissionError("memory write outside BOSSMAN write root")
        if dest.exists():
            raise FileExistsError(dest)

        tags = tags or []
        fm = [
            "---",
            f'title: "{title.replace(chr(34), chr(39))}"',
            f"kind: {kind}",
            f"created: {now.isoformat()}",
            "source: bossman",
        ]
        if project:
            fm.append(f'project: "{project.replace(chr(34), chr(39))}"')
        if tags:
            fm.append("tags: [" + ", ".join(t.replace(",", "") for t in tags) + "]")
        if source_run_id is not None:
            fm.append(f"source_run_id: {source_run_id}")
        # Caller-supplied provenance (e.g. a chat summary: session id, agent, model, summary sha256).
        # Keys are a closed grammar and may not shadow the fields above; values get the same scrub
        # and single-line treatment as every other free-text field.
        for key, value in (extra_frontmatter or {}).items():
            key = str(key)
            if not _FM_KEY.fullmatch(key) or key in _FM_RESERVED:
                raise ValueError(f"bad frontmatter key {key!r}")
            fm.append(f'{key}: "{_one_line(_clean(str(value))).replace(chr(34), chr(39))}"')
        if redactions:
            fm.append("redactions: [" + ", ".join(sorted(redactions)) + "]")
        fm += ["---", ""]
        body = "\n".join(fm) + f"# {title}\n\n{content.strip()}\n"
        _atomic_write(dest, body)
        return dest

    def content_hash(self, path: Path) -> str:
        if not self._inside(path):
            raise PermissionError("path outside vault")
        return hashlib.sha256(path.read_bytes()).hexdigest()
