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

# ---------------------------------------------------------------- контракт скилла

#: Патрон-заглушка для `tasks.meta.allowed_tools`, когда скилл НЕ объявил ни одного
#: инструмента. `bcc.tools.allowed_tools_for` трактует пустой список как «нет записи»
#: и падает обратно на `agents.tools` — то есть скилл без инструментов внезапно
#: получил бы весь набор агента. Имя не совпадает ни с одним зарегистрированным
#: инструментом, поэтому `REGISTRY.resolve` честно возвращает пустой список.
NO_TOOLS_SENTINEL = "skill.__no_tools__"


@dataclass(slots=True)
class SkillContract:
    """То, что скилл обещает рантайму: вход, выход, инструменты, права."""
    id: str
    name: str
    version: str
    fingerprint: str
    required_tools: list[str]
    permissions: list[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    process: str

    def allowed_tools(self) -> list[str]:
        """Ровно то, что уходит в `tasks.meta.allowed_tools`."""
        return list(self.required_tools) if self.required_tools else [NO_TOOLS_SENTINEL]


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in value.replace(",", " ").split() if v.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def skill_version(fm: dict[str, Any]) -> str:
    meta = fm.get("metadata")
    if isinstance(meta, dict) and meta.get("version") is not None:
        return str(meta["version"])
    return str(fm.get("version") or "1.0")


def skill_contract(skill: Skill) -> SkillContract:
    """Разбор фронтматтера в строгий контракт. Отсутствующие поля — пустые,
    а не «всё разрешено»: скилл без required_tools инструментов не получает."""
    fm = skill.frontmatter or {}
    return SkillContract(
        id=skill.id, name=skill.name, version=skill_version(fm),
        fingerprint=skill.fingerprint,
        required_tools=_as_list(fm.get("required_tools")),
        permissions=_as_list(fm.get("permissions")),
        input_schema=_as_dict(fm.get("input_schema")),
        output_schema=_as_dict(fm.get("output_schema")),
        process=skill.body)


def build_skill_prompt(contract: SkillContract, inputs: dict[str, Any]) -> str:
    """Процесс скилла + вход + ожидаемый выход + список выданных инструментов.
    Именно этот текст становится `tasks.prompt` — иного пути исполнения нет."""
    parts = [contract.process.strip(),
             "\n## Входные данные"]
    parts += [f"- {k}: {v}" for k, v in inputs.items()] or ["- (нет)"]
    if contract.required_tools:
        parts.append("\n## Доступные инструменты (других нет)")
        parts += [f"- {t}" for t in contract.required_tools]
    else:
        parts.append("\n## Доступные инструменты\nИнструментов нет — отвечай рассуждением.")
    if contract.output_schema:
        import json as _json
        parts.append("\n## Ожидаемый результат (схема)\n"
                     + _json.dumps(contract.output_schema, ensure_ascii=False, sort_keys=True))
    parts.append(f"\n<!-- skill={contract.id} version={contract.version} "
                 f"fingerprint={contract.fingerprint[:16]} -->")
    return "\n".join(parts)


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
