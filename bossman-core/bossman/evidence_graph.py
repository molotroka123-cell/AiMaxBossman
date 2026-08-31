"""V2.6 — Evidence Graph (модуль P): временный in-proc граф «утверждение → улики».

Назначение: сопоставление ответов и доказательств ПОВЕРХ разнородных файлов
(«сравни цифры в xlsx с пунктами договора в pdf») в рамках одной задачи.
Секции ParsedArtifact (модуль J) индексируются с provenance; запрос — простое
детерминированное лексическое скорингование (пересечение токенов), таблицы
ищутся по тексту ячеек. НЕ персистентность и НЕ второй memory-движок:
граф живёт в памяти процесса ровно столько, сколько над ним работают.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .file_intel import ParsedArtifact

_TOKEN = re.compile(r"\w+", re.UNICODE)
_MIN_TOKEN = 2                 # односимвольные токены — шум
_EXCERPT = 200                 # длина цитаты в EvidenceRef


def _tokens(text: str) -> set[str]:
    return {t for t in (m.group(0).lower() for m in _TOKEN.finditer(text or ""))
            if len(t) >= _MIN_TOKEN}


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    file: str                  # source_path артефакта
    ref: str                   # provenance-ссылка секции: "sheet=…" / "paragraph=3"
    content_hash: str          # sha256 файла — улика привязана к версии контента
    excerpt: str               # короткая цитата для человека/модели


class EvidenceGraph:
    """Улики (секции файлов) + утверждения с их привязкой к уликам."""

    def __init__(self) -> None:
        # (ref, токены секции) в порядке добавления — детерминированный tie-break
        self._entries: list[tuple[EvidenceRef, set[str]]] = []
        self._claims: list[tuple[str, list[EvidenceRef]]] = []

    def add_artifact(self, parsed: ParsedArtifact) -> int:
        """Индексирует typed-секции артефакта; возвращает число добавленных улик."""
        added = 0
        for s in parsed.sections:
            pieces = [s.ref, s.text or ""]
            for row in s.table:            # таблицы — по тексту ячеек
                pieces.extend(row)
            joined = " ".join(p for p in pieces if p).strip()
            if not joined:
                continue
            body = (s.text or "") or " | ".join(
                " | ".join(r) for r in s.table[:3])
            ref = EvidenceRef(file=parsed.source_path, ref=s.ref,
                              content_hash=parsed.content_hash,
                              excerpt=body[:_EXCERPT])
            self._entries.append((ref, _tokens(joined)))
            added += 1
        return added

    def query(self, text: str, limit: int = 5) -> list[EvidenceRef]:
        """Лексический поиск улик: пересечение токенов запроса и секции.
        Детерминированно: сортировка по (−пересечение, порядок добавления)."""
        q = _tokens(text)
        if not q:
            return []
        scored: list[tuple[int, int, EvidenceRef]] = []
        for i, (ref, toks) in enumerate(self._entries):
            overlap = len(q & toks)
            if overlap:
                scored.append((-overlap, i, ref))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [ref for _, _, ref in scored[:max(limit, 0)]]

    def support(self, claim: str, refs: list[EvidenceRef]) -> None:
        """Записать утверждение и улики, на которые оно опирается
        (пустой список — честная запись «без опоры», см. unsupported_claims)."""
        self._claims.append((claim, list(refs)))

    def claims(self) -> list[tuple[str, list[EvidenceRef]]]:
        return [(c, list(r)) for c, r in self._claims]

    def unsupported_claims(self) -> list[str]:
        """Утверждения без единой улики — кандидаты на перепроверку/отзыв."""
        return [c for c, r in self._claims if not r]
