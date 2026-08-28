from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Any

MemoryKind = Literal["decision", "lesson", "fact", "task", "session", "note"]

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
    ) -> Path:
        """Write a new BOSSMAN-owned note.

        Does not edit arbitrary existing vault notes.
        """
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
        fm += ["---", ""]
        body = "\n".join(fm) + f"# {title}\n\n{content.strip()}\n"
        dest.write_text(body, encoding="utf-8")
        return dest

    def content_hash(self, path: Path) -> str:
        if not self._inside(path):
            raise PermissionError("path outside vault")
        return hashlib.sha256(path.read_bytes()).hexdigest()
