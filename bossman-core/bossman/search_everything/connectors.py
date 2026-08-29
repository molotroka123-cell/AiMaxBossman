"""Коннекторы источников + правила allow/deny и метки sensitivity.

Коннектор — это адаптер источника (файловая система, память, история агента/
tool_calls), который выдаёт SearchDocument. КЛЮЧЕВОЕ правило этапа: секрет НИКОГДА
не индексируется молча. Каждый коннектор:
  * пропускает файл через SecretPolicy (deny по имени/пути/суффиксу и по
    содержимому) — секрет-подобный файл отклоняется ДО попадания в индекс;
  * проставляет sensitivity в metadata, чтобы query-путь (allow-list
    HybridRetriever) не выдал чувствительный чанк вызывающему без права.

Обнаружение секретов в содержимом переиспользует редактор bossman.obs (тот же
слой, что вычищает секреты из логов), чтобы политика индексации и политика логов
не расходились.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from .. import obs
from ..obs import get_logger

log = get_logger("bossman.search.connectors")

# Текстовые расширения, которые вообще имеет смысл индексировать как код/доки.
TEXT_SUFFIXES: frozenset[str] = frozenset({
    ".md", ".markdown", ".txt", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".css", ".sql",
    ".sh", ".rs", ".go", ".java", ".kt", ".swift", ".c", ".h", ".cpp", ".hpp",
})

# Каталоги, которые не индексируем никогда (мусор + чувствительные зоны).
_DENY_PATH_PARTS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", ".ssh", ".gnupg", "node_modules", "vendor", "dist",
    "build", "__pycache__", ".venv", "venv", ".secrets", "secrets",
})

# Полные имена файлов, которые всегда секрет.
_DENY_NAMES: frozenset[str] = frozenset({
    ".env", ".env.local", ".env.production", ".env.prod", ".env.dev",
    ".netrc", "_netrc", ".pgpass", ".htpasswd", "credentials", "credentials.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".npmrc", ".pypirc",
    "service-account.json", "serviceaccount.json", "secrets.yaml", "secrets.yml",
})

# Суффиксы ключей/сертификатов/хранилищ.
_DENY_SUFFIXES: frozenset[str] = frozenset({
    ".pem", ".key", ".pfx", ".p12", ".p8", ".keystore", ".jks", ".asc", ".gpg",
    ".ppk", ".crt", ".cer", ".der",
})

# Подстроки в имени файла, выдающие секрет.
_DENY_NAME_SUBSTR: tuple[str, ...] = (
    "secret", "password", "passwd", "credential", "apikey", "api_key",
    "private_key", "privatekey", "id_rsa", ".env",
)

# Дополнительные сигнатуры секрета в содержимом (в дополнение к obs.redact):
# приватные ключи и облачные access-key id, которые obs.redact не ловит.
import re as _re

_CONTENT_SIGNATURES = (
    _re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----"),
    _re.compile(r"\bAKIA[0-9A-Z]{16}\b"),           # AWS access key id
    _re.compile(r"\bASIA[0-9A-Z]{16}\b"),           # AWS temp access key id
    _re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),     # Google API key
)


@dataclass(slots=True)
class SecretPolicy:
    """Реальный гейт, а не комментарий: решает, отклонить ли файл/текст как секрет.

    deny по пути/имени/суффиксу и по содержимому. Используется и коннекторами,
    и путём индексации (defense-in-depth): даже вручную собранный SearchDocument
    с секретом будет отклонён при upsert.
    """

    deny_parts: frozenset[str] = _DENY_PATH_PARTS
    deny_names: frozenset[str] = _DENY_NAMES
    deny_suffixes: frozenset[str] = _DENY_SUFFIXES
    deny_name_substrings: tuple[str, ...] = _DENY_NAME_SUBSTR

    def is_secret_path(self, path: str | Path, *, root: str | Path | None = None) -> bool:
        p = Path(path)
        parts = {part.lower() for part in p.parts}
        if parts & {x.lower() for x in self.deny_parts}:
            return True
        name = p.name.lower()
        if name in {x.lower() for x in self.deny_names}:
            return True
        if p.suffix.lower() in self.deny_suffixes:
            return True
        if any(sub in name for sub in self.deny_name_substrings):
            return True
        return False

    def is_secret_content(self, text: str | None) -> bool:
        if not text:
            return False
        # Переиспользуем редактор логов: если redact что-то заменил — это секрет
        # (Bearer/Basic, key=value для чувствительных ключей, sk-/ghp_/xox-токены).
        if obs.redact(text) != text:
            return True
        return any(rx.search(text) for rx in _CONTENT_SIGNATURES)

    def is_secret(self, *, path: str | Path | None = None, text: str | None = None,
                  source: str = "") -> bool:
        if path is not None and self.is_secret_path(path):
            return True
        if self.is_secret_content(text):
            return True
        return False


# --- SearchDocument импортируется из engine (форма результата) ----------------
# Импорт отложенный, чтобы избежать цикла engine<->connectors на уровне модуля.
def _search_document(*args: Any, **kwargs: Any):
    from .engine import SearchDocument
    return SearchDocument(*args, **kwargs)


def filesystem_documents(root: str | Path, *, project: str | None = None,
                         policy: SecretPolicy | None = None,
                         max_bytes: int = 2_000_000,
                         suffixes: Iterable[str] | None = None) -> Iterator[Any]:
    """Обход файлов (код/доки) с deny-фильтром секретов.

    Секрет-подобный файл (по пути/имени/суффиксу или по содержимому) НЕ выдаётся —
    он даже не читается целиком в индекс. Крупные файлы режутся уже Ingestor'ом
    (chunking), поэтому здесь только лимит на размер исходника.
    """
    policy = policy or SecretPolicy()
    allowed = frozenset(s.lower() for s in (suffixes or TEXT_SUFFIXES))
    base = Path(root).resolve()
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        # deny по пути/имени — до чтения содержимого.
        if policy.is_secret_path(p):
            log.info("search: пропущен секрет-подобный файл %s", p.name)
            continue
        if p.suffix.lower() not in allowed:
            continue
        try:
            if p.stat().st_size > max_bytes:
                continue
            text = p.read_text("utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        # deny по содержимому — секрет внутри разрешённого по имени файла.
        if policy.is_secret_content(text):
            log.info("search: пропущен файл с секретом в содержимом %s", p.name)
            continue
        rel = str(p.relative_to(base))
        yield _search_document(rel, text, "filesystem", project,
                               {"path": str(p), "sensitivity": "normal"})


def memory_documents(records: Iterable[Any], *, project: str | None = None) -> Iterator[Any]:
    """Коннектор долговременной памяти: MemoryRecord (или dict) → SearchDocument.

    Память уже лежит в едином сторе context_engine; коннектор лишь даёт ей форму
    документа, чтобы единый поиск покрывал и память. sensitivity наследуется из
    metadata записи (по умолчанию normal); секрет в тексте отклоняется на ingest.
    """
    for m in records:
        if isinstance(m, dict):
            text = str(m.get("text", "")).strip()
            mid = str(m.get("memory_id", "") or m.get("id", ""))
            kind = str(m.get("kind", "memory"))
            meta = dict(m.get("metadata", {}) or {})
        else:
            text = str(getattr(m, "text", "") or "").strip()
            mid = str(getattr(m, "memory_id", "") or "")
            kind_val = getattr(m, "kind", "memory")
            kind = getattr(kind_val, "value", str(kind_val))
            meta = dict(getattr(m, "metadata", {}) or {})
        if not text:
            continue
        meta.setdefault("sensitivity", "normal")
        meta["kind"] = kind
        yield _search_document(f"memory://{mid or kind}", text, "memory", project, meta)


def history_documents(entries: Iterable[Any], *, project: str | None = None) -> Iterator[Any]:
    """Коннектор истории агента/tool_calls: запись истории → SearchDocument.

    tool_call может содержать секрет (ключ в аргументах/ответе), поэтому политика
    содержимого применяется на ingest и такой фрагмент не попадёт в индекс.
    """
    for i, e in enumerate(entries):
        if isinstance(e, dict):
            text = str(e.get("text") or e.get("content") or "").strip()
            ref = str(e.get("id") or e.get("ref") or e.get("run_id") or i)
            source = str(e.get("source") or "tool_call")
            meta = dict(e.get("metadata", {}) or {})
        else:
            text = str(getattr(e, "text", "") or getattr(e, "content", "") or "").strip()
            ref = str(getattr(e, "id", "") or i)
            source = str(getattr(e, "source", "tool_call"))
            meta = dict(getattr(e, "metadata", {}) or {})
        if not text:
            continue
        meta.setdefault("sensitivity", "normal")
        yield _search_document(f"history://{ref}", text, source, project, meta)
