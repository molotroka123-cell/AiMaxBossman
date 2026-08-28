from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Any

import yaml

SKILL_FILE = "SKILL.md"

@dataclass(slots=True)
class Skill:
    id: str
    name: str
    description: str
    path: Path
    source_root: Path
    frontmatter: dict[str, Any]
    body: str
    fingerprint: str

def parse_skill(path: Path, source_root: Path) -> Skill:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm: dict[str, Any] = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0:
            # устойчивость: битый YAML (напр. двоеточие в description) не должен
            # ронять discovery — извлекаем хотя бы name/description построчно
            try:
                fm = yaml.safe_load(text[4:end]) or {}
            except yaml.YAMLError:
                fm = _lenient_frontmatter(text[4:end])
            body = text[end + 4:].lstrip("\r\n")
    sid = path.parent.name
    name = str(fm.get("name") or sid)  # noqa: E501 (см. _lenient_frontmatter ниже)
    desc = str(fm.get("description") or "")
    fp = hashlib.sha256(text.encode()).hexdigest()
    return Skill(sid, name, desc, path, source_root, fm, body, fp)

class SkillLibrary:
    """Explicit-root skill discovery. Never scans the whole machine."""

    def __init__(self, roots: Iterable[Path], canonical_root: Path):
        self.roots = [Path(r).expanduser().resolve() for r in roots]
        self.canonical_root = Path(canonical_root).expanduser().resolve()

    def discover(self) -> list[Skill]:
        found: list[Skill] = []
        seen_paths: set[Path] = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob(f"*/{SKILL_FILE}")):
                rp = path.resolve()
                if rp in seen_paths:
                    continue
                seen_paths.add(rp)
                found.append(parse_skill(rp, root))
        return found

    def by_id(self) -> dict[str, Skill]:
        # Earlier roots have priority.
        out: dict[str, Skill] = {}
        for skill in self.discover():
            out.setdefault(skill.id, skill)
        return out

    def import_skill(self, skill: Skill, *, overwrite: bool = False) -> Skill:
        dest_dir = self.canonical_root / skill.id
        dest = dest_dir / SKILL_FILE
        if dest.exists() and not overwrite:
            raise FileExistsError(dest)
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill.path, dest)
        return parse_skill(dest, self.canonical_root)

    def create(self, skill_id: str, content: str, *, overwrite: bool = False) -> Skill:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", skill_id):
            raise ValueError("skill id must be lower kebab-case")
        dest = self.canonical_root / skill_id / SKILL_FILE
        if dest.exists() and not overwrite:
            raise FileExistsError(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return parse_skill(dest, self.canonical_root)

def default_skill_roots(repo_root: Path, home: Path | None = None) -> list[Path]:
    repo_root = repo_root.resolve()
    home = (home or Path.home()).expanduser().resolve()
    return [
        repo_root / ".agents" / "skills",
        repo_root / ".opencode" / "skills",
        repo_root / ".claude" / "skills",
        home / ".agents" / "skills",
        home / ".config" / "opencode" / "skills",
        home / ".claude" / "skills",
    ]


def _lenient_frontmatter(raw: str) -> dict[str, Any]:
    """Запасной разбор YAML-фронтматтера: берём простые `key: value` верхнего
    уровня как строки. Спасает discovery от одного скилла с двоеточием в значении."""
    out: dict[str, Any] = {}
    for line in raw.splitlines():
        if line[:1] in (" ", "\t", "#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key and key.isidentifier():
            out[key] = value.strip()
    return out
