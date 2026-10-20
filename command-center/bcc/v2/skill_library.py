from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Any

import yaml

SKILL_FILE = "SKILL.md"


class _NoAliasSafeLoader(yaml.SafeLoader):
    """SafeLoader без YAML-ссылок (``&anchor`` / ``*alias``).

    SafeLoader уже не исполняет код (``!!python/...`` — ошибка), но ссылки
    разворачивает: десяток строк «alias bomb» дают дерево из миллионов узлов,
    которое ``/api/skills/{id}`` честно сериализует в JSON. В манифесте навыка
    ссылки не нужны, поэтому они просто запрещены."""

    def compose_node(self, parent, index):  # noqa: D401 — pyyaml hook
        if self.check_event(yaml.events.AliasEvent):
            event = self.peek_event()
            raise yaml.composer.ComposerError(
                None, None, "ссылки YAML (*alias) во фронтматтере навыка запрещены",
                event.start_mark)
        return super().compose_node(parent, index)


def _load_yaml(raw: str) -> Any:
    return yaml.load(raw, Loader=_NoAliasSafeLoader)  # noqa: S506 — SafeLoader subclass

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

#: Самый большой навык в поставке — ~33 КБ. Всё, что больше 256 КБ, — не
#: методика, а мусор или атака на память: такой файл не читается целиком.
MAX_SKILL_BYTES = 256 * 1024


class SkillLoadError(ValueError):
    """Навык нельзя загрузить; сообщение — по-русски и без абсолютных путей."""


def _too_big(skill_id: str, size: int, limit: int = MAX_SKILL_BYTES) -> str:
    return (f"Навык «{skill_id}»: SKILL.md занимает {size} байт, допустимо не больше {limit}. "
            "Сократите текст или вынесите справку в отдельный файл.")


def parse_skill(path: Path, source_root: Path, *, max_bytes: int = MAX_SKILL_BYTES) -> Skill:
    size = path.stat().st_size
    if size > max_bytes:
        raise SkillLoadError(_too_big(path.parent.name, size, max_bytes))
    with path.open("rb") as fh:
        raw = fh.read(max_bytes + 1)          # файл мог вырасти между stat и read
    if len(raw) > max_bytes:
        raise SkillLoadError(_too_big(path.parent.name, len(raw), max_bytes))
    # utf-8-sig: Блокнот Windows сохраняет UTF-8 с BOM, и тогда файл начинался
    # с U+FEFF перед "---" — фронтматтер не распознавался, name/description/инструменты
    # молча терялись, а весь YAML уходил в тело процесса. Переводы строк
    # нормализуются так же, как это делал read_text (universal newlines).
    text = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    return parse_skill_text(text, path, source_root)


def parse_skill_text(text: str, path: Path, source_root: Path) -> Skill:
    """Разбор уже прочитанного текста (create/import проверяют его ДО записи)."""
    fm: dict[str, Any] = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0:
            # устойчивость: битый YAML (напр. двоеточие в description) не должен
            # ронять discovery — извлекаем хотя бы name/description построчно
            try:
                fm = _load_yaml(text[4:end]) or {}
            except yaml.YAMLError:
                # Двоеточие внутри значения ломает YAML. Сначала пробуем
                # починить кавычками и разобрать НОРМАЛЬНО — иначе теряются
                # вложенные поля (metadata.version), и версия скилла
                # становится невидимой.
                fm = _quoted_retry(text[4:end]) or _lenient_frontmatter(text[4:end])
            if not isinstance(fm, dict):
                # фронтматтер-список или строка: fm.get() ронял весь discovery
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
        self._cache: list[Skill] | None = None
        self._cache_key: tuple | None = None
        #: навыки, которые не загрузились при последнем discover():
        #: [{"skill": id, "root": имя корня, "message": по-русски}]
        self.errors: list[dict[str, str]] = []

    def _scan_key(self) -> tuple:
        """Дешёвый ключ состояния ФС: (путь, mtime) каждого SKILL.md.

        Glob дёшев; дорогой parse_skill (чтение файла + yaml) — нет. Ключ по
        mtime позволяет пропустить именно повторный parse, когда на диске
        ничего не изменилось (FABLE5 perf: discover/by_id re-парсили ФС на
        каждый вызов). Изменение/добавление/удаление SKILL.md меняет ключ."""
        entries = []
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob(f"*/{SKILL_FILE}")):
                try:
                    mt = path.stat().st_mtime_ns
                except OSError:
                    mt = 0
                entries.append((str(root), str(path.resolve()), mt))
        return tuple(entries)

    def discover(self) -> list[Skill]:
        key = self._scan_key()
        if self._cache is not None and self._cache_key == key:
            return list(self._cache)          # копия: кэш не мутируем наружу
        found: list[Skill] = []
        errors: list[dict[str, str]] = []
        seen_paths: set[Path] = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob(f"*/{SKILL_FILE}")):
                rp = path.resolve()
                # Ссылка/junction из папки навыков наружу подсовывала бы как
                # «навык» любой файл машины (в т.ч. с required_tools/permissions).
                # Ссылки внутри того же корня допустимы.
                if not rp.is_relative_to(root):
                    errors.append({"skill": path.parent.name, "root": root.name,
                                   "message": f"Навык «{path.parent.name}» — ссылка на файл вне папки "
                                              "навыков; такой навык не загружается. Скопируйте его "
                                              "внутрь папки вместо ссылки."})
                    continue
                if rp in seen_paths:
                    continue
                seen_paths.add(rp)
                # один битый/огромный/нечитаемый SKILL.md не должен обнулять
                # весь список навыков владельца
                try:
                    found.append(parse_skill(rp, root))
                except SkillLoadError as exc:
                    errors.append({"skill": path.parent.name, "root": root.name,
                                   "message": str(exc)})
                except OSError as exc:
                    errors.append({"skill": path.parent.name, "root": root.name,
                                   "message": f"Навык «{path.parent.name}»: SKILL.md не читается "
                                              f"({exc.__class__.__name__})."})
        self._cache = found
        self._cache_key = key
        self.errors = errors
        return list(found)

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
            raise ValueError("Имя навыка: 2–63 символа, только строчные латинские буквы, цифры и дефис "
                             "(например, my-skill). Русские буквы и пробелы не подходят.")
        size = len(content.encode("utf-8"))
        if size > MAX_SKILL_BYTES:
            raise SkillLoadError(_too_big(skill_id, size))
        dest = self.canonical_root / skill_id / SKILL_FILE
        # Манифест проверяется ДО записи: навык с битой input_schema падал бы
        # на каждом запуске, а с чужим upstream без лицензии — не должен попасть
        # в поставку вовсе. Предупреждения (нет name и т.п.) запись не блокируют.
        draft = parse_skill_text(content.lstrip(chr(0xFEFF)).replace("\r\n", "\n").replace("\r", "\n"),
                                 dest, self.canonical_root)
        problems = validate_manifest(draft)["errors"]
        if problems:
            raise SkillManifestError(f"Навык «{skill_id}» не сохранён: " + "; ".join(problems))
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


# ---------------------------------------------------------------- манифест скилла

class SkillManifestError(ValueError):
    """Манифест SKILL.md сломан так, что навык нельзя сохранять/запускать."""


_STR_LIST = {"anyOf": [{"type": "array", "items": {"type": "string", "minLength": 1}},
                       {"type": "string"}]}

#: JSON Schema фронтматтера SKILL.md (поля, которые читает рантайм Bossman).
#: Неизвестные поля разрешены: у OpenCode/Claude/upstream свои расширения.
MANIFEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "pattern": r"^[a-z0-9][a-z0-9-]{1,62}$"},
        "description": {"type": "string", "minLength": 1, "maxLength": 1024},
        "version": {"type": ["string", "number"]},
        "license": {"type": "string", "minLength": 1},
        "compatibility": {"type": "string"},
        "metadata": {"type": "object", "properties": {
            "version": {"type": ["string", "number"]},
            "license": {"type": "string", "minLength": 1},
            "upstream": {"type": "string", "minLength": 1}}},
        "required_tools": _STR_LIST,
        "permissions": _STR_LIST,
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    },
}


def ru_schema_error(err) -> str:
    """Одна ошибка jsonschema → понятная владельцу фраза по-русски."""
    where = ".".join(str(p) for p in err.absolute_path) or "вход"
    kind, val = err.validator, err.validator_value
    if kind == "required":
        missing = err.message.split("'")[1] if err.message.count("'") >= 2 else err.message
        return f"нет обязательного поля {missing}" if where == "вход" else \
            f"{where}: нет обязательного поля {missing}"
    if kind == "type":
        return f"{where} должно быть типа {val}"
    if kind == "additionalProperties":
        known = set((err.schema.get("properties") or {}))
        extra = sorted(k for k in (err.instance or {}) if k not in known)
        return f"{where}: лишние поля {', '.join(extra)} — навык их не принимает"
    if kind == "enum":
        return f"{where}: допустимо только {', '.join(map(str, val))}"
    if kind in ("maxLength", "maxItems", "maxProperties"):
        return f"{where}: слишком длинно, не больше {val}"
    if kind in ("minLength", "minItems", "minProperties"):
        return f"{where}: слишком коротко, не меньше {val}"
    if kind in ("maximum", "exclusiveMaximum"):
        return f"{where}: значение больше допустимого {val}"
    if kind in ("minimum", "exclusiveMinimum"):
        return f"{where}: значение меньше допустимого {val}"
    if kind == "pattern":
        return f"{where}: не подходит под шаблон {val}"
    if kind == "oneOf":
        return f"{where}: нужно заполнить ровно один из вариантов входа"
    if kind == "anyOf":
        return f"{where}: неверный формат"
    if kind == "const":
        return f"{where}: должно быть {val!r}"
    return f"{where}: {err.message}"


_MANIFEST_HINTS = {
    "name": "name — латиница в нижнем регистре, цифры и дефис (2–63 символа)",
    "description": "description — непустой текст до 1024 символов: по нему навык находят",
    "required_tools": "required_tools — список имён инструментов (строки)",
    "permissions": "permissions — список прав (строки)",
    "input_schema": "input_schema — объект JSON Schema",
    "output_schema": "output_schema — объект JSON Schema",
}


def validate_manifest(skill: Skill) -> dict[str, list[str]]:
    """Проверить фронтматтер навыка. ``errors`` блокируют сохранение и запуск,
    ``warnings`` — только показываются. Сообщения — по-русски."""
    from jsonschema import Draft202012Validator, exceptions, validators

    fm = skill.frontmatter or {}
    errors: list[str] = []
    warnings: list[str] = []
    for err in sorted(Draft202012Validator(MANIFEST_SCHEMA).iter_errors(fm),
                      key=lambda e: [str(p) for p in e.absolute_path]):
        top = str(err.absolute_path[0]) if err.absolute_path else ""
        hint = _MANIFEST_HINTS.get(top)
        msg = ru_schema_error(err).replace("вход", "фронтматтер", 1) if not top else ru_schema_error(err)
        errors.append(f"{msg} ({hint})" if hint else msg)
    for key in ("input_schema", "output_schema"):
        sch = fm.get(key)
        if isinstance(sch, dict) and sch:
            cls = validators.validator_for(sch, default=Draft202012Validator)
            try:
                cls.check_schema(sch)
            except exceptions.SchemaError as exc:
                errors.append(f"{key} — некорректная JSON Schema: {exc.message}")
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    if meta.get("upstream") and not (fm.get("license") or meta.get("license")):
        errors.append(f"навык взят из {meta['upstream']}, но лицензия не указана — добавьте "
                      "license (SPDX, например MIT) во фронтматтер или metadata")
    if not fm.get("name"):
        warnings.append(f"нет name — используется имя папки «{skill.id}»")
    elif isinstance(fm.get("name"), str) and fm["name"] != skill.id:
        warnings.append(f"name «{fm['name']}» не совпадает с папкой «{skill.id}»; "
                        f"навык вызывается по имени папки")
    if not fm.get("description"):
        warnings.append("нет description — навык не найдут поиском и по задаче")
    return {"errors": errors, "warnings": warnings}


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
        # Built-in skills shipped with the wheel: no repository required.
        # Keep explicit owner/workspace roots ahead of this read-only fallback.
        Path(__file__).resolve().parents[1] / "_skills",
    ]


def _quoted_retry(raw: str) -> dict[str, Any] | None:
    """Заключить незакавыченные скалярные значения в кавычки и разобрать снова.

    Так `description: Audit a repository: map architecture` перестаёт быть
    битым YAML, и при этом сохраняется вложенная структура (`metadata.version`),
    которую построчный разбор теряет. Отступ сохраняем — иначе развалится
    вложенность.
    """
    fixed: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("- "):
            fixed.append(line)
            continue
        key, sep, value = line.partition(":")
        value = value.strip()
        # чиним только простые скаляры: не списки, не блоки, не уже закавыченное
        if (sep and value and ":" in value
                and not value.startswith(("'", '"', "[", "{", "|", ">", "&", "*"))):
            fixed.append(f"{key}: \"{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(39))}\"")
        else:
            fixed.append(line)
    try:
        data = _load_yaml("\n".join(fixed))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


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
