"""V2.6 модуль I — детерминированное ядро Deep Research Engine.

Оркестрация/бухгалтерия БЕЗ LLM и БЕЗ сети: IO делает инжектированный
async fetcher (production передаст существующие http/browser-обработчики
toolkit; тесты — фейки). Каждый шаг пайплайна — чистая функция над текстом:
извлечение evidence по токен-пересечению с вопросом, dedup по нормализованному
excerpt'у, детекция противоречий по общей ключевой фразе с разной полярностью
отрицания, взвешивание confidence через trust источника. Синтез экстрактивный:
текст claim = лучший подтверждённый excerpt, никакой генерации.

VOI-граница: раунд, не добавивший НОВОЙ evidence (прирост информации 0),
останавливает цикл досрочно — «бесконечный research» невозможен по построению.
Полученный извне текст проходит канонический cybersec.guards.ingest_guard,
когда тот доступен (тот же контракт, что runner._cybersec_inspect_external);
сбой инспекции не роняет прогон — текст и так обрабатывается как данные.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Awaitable, Callable

from .models import QUICK, Claim, Evidence, ResearchMode, ResearchReport, Source

# --- детерминированная лексика -------------------------------------------------

_WORD_RE = re.compile(r"\w+", re.UNICODE)
#: Маркеры отрицания (полярность утверждения). Не входят в content-токены,
#: чтобы противоречащая пара делила одну и ту же ключевую фразу.
_NEGATION = frozenset({"не", "нет", "no", "not", "never"})
#: Служебные слова, не несущие содержания, — вне ключевых фраз.
_STOPWORDS = frozenset({
    "и", "в", "на", "о", "у", "а", "с", "по", "за", "из", "от", "до", "ли",
    "же", "это", "что", "как", "какая", "какой", "какие", "для", "или",
    "the", "a", "an", "of", "in", "to", "is", "are", "and", "or", "for",
})
_NEG_RE = re.compile(r"(?i)(?<!\w)(не|нет|no|not|never)(?!\w)")

#: Минимум общих content-токенов: excerpt↔вопрос (релевантность) и
#: excerpt↔excerpt (общая ключевая фраза для противоречия).
MIN_SHARED_TOKENS = 2
MAX_EXCERPTS_PER_FETCH = 40
MAX_CLAIM_TEXT = 240


def _content_tokens(text: str) -> frozenset[str]:
    return frozenset(
        t for t in (w.lower() for w in _WORD_RE.findall(text))
        if t not in _STOPWORDS and t not in _NEGATION
    )


def _has_negation(text: str) -> bool:
    return _NEG_RE.search(text) is not None


def _norm_key(excerpt: str) -> str:
    """Ключ dedup: sha256 нормализованного excerpt'а (регистр/пробелы не в счёт)."""
    norm = " ".join(excerpt.lower().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _extract_excerpts(text: str, q_tokens: frozenset[str]) -> list[str]:
    """Экстрактивное извлечение: предложения/абзацы с достаточным
    токен-пересечением с вопросом. Детерминированно, порядок исходный."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        for raw in re.split(r"(?<=[.!?…])\s+|\n", para):
            sent = raw.strip()
            if len(sent) < 3:
                continue
            shared = _content_tokens(sent) & q_tokens
            if len(shared) >= MIN_SHARED_TOKENS or (q_tokens and shared >= q_tokens):
                out.append(sent)
                if len(out) >= MAX_EXCERPTS_PER_FETCH:
                    return out
    return out


def _detect_contradictions(
        evs: tuple[Evidence, ...]) -> tuple[tuple[str, ...], frozenset[str]]:
    """Пары evidence с общей ключевой фразой (≥ MIN_SHARED_TOKENS общих
    content-токенов), где ровно одна сторона содержит маркер отрицания."""
    records: list[str] = []
    flagged: set[str] = set()
    for i in range(len(evs)):
        for j in range(i + 1, len(evs)):
            a, b = evs[i], evs[j]
            shared = _content_tokens(a.excerpt) & _content_tokens(b.excerpt)
            if len(shared) < MIN_SHARED_TOKENS:
                continue
            if _has_negation(a.excerpt) != _has_negation(b.excerpt):
                records.append(
                    f"противоречие: «{a.excerpt[:100]}» [{a.source.url_or_ref}] "
                    f"vs «{b.excerpt[:100]}» [{b.source.url_or_ref}]")
                flagged.add(a.content_hash)
                flagged.add(b.content_hash)
    return tuple(records), frozenset(flagged)


def _build_claims(evs: tuple[Evidence, ...], q_tokens: frozenset[str],
                  flagged: frozenset[str]) -> tuple[Claim, ...]:
    """Граф claim/evidence: группировка по общей с вопросом ключевой фразе.
    Текст claim экстрактивный — лучший (по trust) excerpt группы, обрезанный.
    confidence = avg_trust × f(число подтверждений); противоречие режет вдвое."""
    groups: dict[tuple[str, ...], list[Evidence]] = {}
    for ev in evs:
        key = tuple(sorted(_content_tokens(ev.excerpt) & q_tokens))
        groups.setdefault(key, []).append(ev)

    claims: list[Claim] = []
    for group in groups.values():
        best = max(group, key=lambda e: (e.source.trust, -len(e.excerpt)))
        avg_trust = sum(e.source.trust for e in group) / len(group)
        contradicted = any(e.content_hash in flagged for e in group)
        conf = min(1.0, avg_trust * (0.5 + 0.5 * min(len(group), 4) / 4))
        if contradicted:
            conf *= 0.5     # противоречие — прямой удар по уверенности
        claims.append(Claim(text=best.excerpt[:MAX_CLAIM_TEXT],
                            evidence=tuple(group),
                            confidence=round(conf, 3),
                            contradicted=contradicted))
    claims.sort(key=lambda c: (-c.confidence, c.text))
    return tuple(claims)


class ResearchEngine:
    """Оркестратор пайплайна. Сам не ходит в сеть: fetcher(Source) -> str."""

    def __init__(self, fetcher: Callable[[Source], Awaitable[str]], *,
                 now: Callable[[], float] = time.time):
        self._fetcher = fetcher
        self._now = now

    def _ingest(self, text: str) -> str:
        """Канонический ingest_guard на границе внешних данных (контракт
        runner._cybersec_inspect_external): доступен — используем verdict.text,
        недоступен/упал — тихая деградация, текст и так помечен данными."""
        try:
            from ..cybersec import guards
            return guards.ingest_guard(text).text
        except Exception:  # noqa: BLE001 — firewall вторичен, research не падает
            return text

    async def run(self, question: str, sources: list[Source],
                  mode: ResearchMode = QUICK) -> ResearchReport:
        """Полный прогон. По умолчанию QUICK; DEEP — только явно."""
        q_tokens = _content_tokens(question)
        evidence: dict[str, Evidence] = {}      # dedup-ключ -> Evidence
        ok_sources: list[Source] = []
        fetch_errors: list[str] = []
        attempted: set[int] = set()
        rounds_used = 0

        for _ in range(mode.max_rounds):
            batch = [(i, s) for i, s in enumerate(sources)
                     if i not in attempted][:mode.max_sources]
            if not batch:
                break               # источники исчерпаны — продолжать нечем
            rounds_used += 1
            gained = False
            for i, src in batch:
                attempted.add(i)
                try:
                    raw = await self._fetcher(src)
                except Exception as exc:  # noqa: BLE001 — ошибка fetch записана, не фатальна
                    fetch_errors.append(f"{src.url_or_ref}: {exc}")
                    continue
                ts = self._now()
                src.retrieved_at = ts
                ok_sources.append(src)
                text = self._ingest(raw)       # внешний текст — данные
                for excerpt in _extract_excerpts(text, q_tokens):
                    key = _norm_key(excerpt)
                    if key in evidence:
                        continue               # dedup по нормализованному excerpt
                    evidence[key] = Evidence(
                        source=src, excerpt=excerpt, retrieved_at=ts,
                        content_hash=hashlib.sha256(
                            excerpt.encode("utf-8")).hexdigest())
                    gained = True
            if not gained:
                break   # VOI-стоп: прирост информации 0 → ожидаемая ценность < цены

        evs = tuple(evidence.values())
        contradictions, flagged = _detect_contradictions(evs)
        claims = _build_claims(evs, q_tokens, flagged)
        # Утверждение без evidence не существует как claim — вопрос уходит в
        # unanswered, а не превращается в неподкреплённый «ответ».
        unanswered = () if claims else (question,)
        return ResearchReport(
            question=question, mode=mode, claims=claims,
            sources=tuple(ok_sources), contradictions=contradictions,
            unanswered=unanswered, rounds_used=rounds_used,
            fetch_errors=tuple(fetch_errors))

    @staticmethod
    def citations(report: ResearchReport) -> list[dict]:
        return citations(report)


def citations(report: ResearchReport) -> list[dict]:
    """Карта цитирования: у КАЖДОГО claim ≥ 1 источник с timestamp получения
    (гарантировано построением — claim без evidence не создаётся)."""
    out: list[dict] = []
    for claim in report.claims:
        refs = [(ev.source.url_or_ref, ev.retrieved_at) for ev in claim.evidence]
        out.append({"claim": claim.text, "sources": refs})
    return out
