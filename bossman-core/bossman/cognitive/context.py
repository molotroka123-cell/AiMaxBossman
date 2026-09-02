"""Контекст 7/10 → 10/10: Context Compiler, P0-P4, ledger, сжатие, fallback, firewall.

Память хранит знания, контекст решает ЧТО дать модели СЕЙЧАС.
Запрещено "вставлять последние сообщения до заполнения окна".

Порядок сборки (фиксированный):
    System invariants → User goal → Current working state → Critical constraints
    → Verified evidence → Relevant memory → Required code/interfaces
    → Recent tool results → Unresolved questions → Current action

Приоритеты: P0 (безопасность/цель/approval) и P1 (ограничения/verified facts)
запрещено удалять при сжатии. P2 — план/интерфейсы/последние результаты,
P3 — вспомогательная история, P4 — предположения/документация.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence

from .memory import contains_prompt_injection
from .storage import CognitiveStore, json_dumps, sha256_text, stable_id, utcnow_iso


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


PRIORITY_RANK = {Priority.P0: 0, Priority.P1: 1, Priority.P2: 2, Priority.P3: 3, Priority.P4: 4}

# Источники, чей контент всегда UNTRUSTED_DATA.
UNTRUSTED_SOURCES = {
    "website", "web", "ui", "readme", "git_issue", "issue", "log",
    "video", "ocr", "email", "tool_result", "tool", "upload", "external",
}


class TrustTag(str, Enum):
    TRUSTED = "TRUSTED"
    UNTRUSTED_DATA = "UNTRUSTED_DATA"


@dataclass(slots=True)
class ContextItem:
    section: str  # одна из 10 фиксированных секций компилятора
    text: str
    priority: Priority = Priority.P3
    source: str = ""
    source_type: str = "internal"
    trust: TrustTag = TrustTag.TRUSTED
    tokens: int = 0
    must_preserve: bool = False
    refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = estimate_tokens(self.text)
        if self.source_type in UNTRUSTED_SOURCES:
            self.trust = TrustTag.UNTRUSTED_DATA


def estimate_tokens(text: str) -> int:
    # Консервативная оценка без токенизатора: ~3.5 символа на токен (RU+EN).
    return max(1, int(len(text) / 3.5))


COMPILER_ORDER = (
    "System invariants",
    "User goal",
    "Current working state",
    "Critical constraints",
    "Verified evidence",
    "Relevant memory",
    "Required code/interfaces",
    "Recent tool results",
    "Unresolved questions",
    "Current action",
)

# Какая секция — какой приоритет по умолчанию (переопределяемо на item-уровне).
SECTION_PRIORITY: dict[str, Priority] = {
    "System invariants": Priority.P0,
    "User goal": Priority.P0,
    "Current working state": Priority.P1,
    "Critical constraints": Priority.P1,
    "Verified evidence": Priority.P1,
    "Relevant memory": Priority.P2,
    "Required code/interfaces": Priority.P2,
    "Recent tool results": Priority.P2,
    "Unresolved questions": Priority.P3,
    "Current action": Priority.P0,  # действие тоже P0: approval/безопасность
}


@dataclass(slots=True)
class CriticalFact:
    fact_id: str
    normalized_fact: str
    importance: float = 0.8
    source: str = ""
    verification: str = ""
    scope: str = ""
    expires_at: str = ""
    must_preserve: bool = True


class CriticalFactLedger:
    """Ledger критических фактов: сохраняется ДО compression, сверяется ПОСЛЕ."""

    def __init__(self, store: CognitiveStore) -> None:
        self.store = store

    def record(self, fact: CriticalFact) -> CriticalFact:
        if not fact.fact_id:
            fact.fact_id = stable_id("fact", fact.normalized_fact, fact.scope)
        self.store.execute(
            """INSERT INTO ledger_facts VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fact_id) DO UPDATE SET normalized_fact=excluded.normalized_fact,
            importance=excluded.importance,source=excluded.source,
            verification=excluded.verification,scope=excluded.scope,
            expires_at=excluded.expires_at,must_preserve=excluded.must_preserve""",
            (
                fact.fact_id, fact.normalized_fact, fact.importance, fact.source,
                fact.verification, fact.scope, fact.expires_at,
                1 if fact.must_preserve else 0, utcnow_iso(),
            ),
        )
        self.store.commit()
        return fact

    def must_preserve_facts(self, scope: str = "") -> list[CriticalFact]:
        sql = "SELECT * FROM ledger_facts WHERE must_preserve=1"
        params: tuple = ()
        if scope:
            sql += " AND scope=?"
            params = (scope,)
        out = []
        for r in self.store.execute(sql, params).fetchall():
            out.append(CriticalFact(
                fact_id=r["fact_id"], normalized_fact=r["normalized_fact"],
                importance=r["importance"], source=r["source"],
                verification=r["verification"], scope=r["scope"],
                expires_at=r["expires_at"], must_preserve=bool(r["must_preserve"]),
            ))
        return out

    @staticmethod
    def verify_roundtrip(facts_before: Sequence[CriticalFact], summary_text: str) -> dict[str, Any]:
        """Обратная проверка: каждый must_preserve обязан присутствовать после."""
        low = summary_text.lower()
        missing = []
        for f in facts_before:
            if f.must_preserve and f.normalized_fact.lower() not in low:
                # Мягкое совпадение по ключевым токенам (≥70% значимых слов).
                core = {w.lower() for w in re.findall(r"\w{3,}", f.normalized_fact)}
                hit = {w for w in core if w in low}
                if not core or len(hit) / len(core) < 0.7:
                    missing.append(f.fact_id)
        return {"ok": not missing, "missing": missing,
                "checked": len([f for f in facts_before if f.must_preserve])}


class InjectionFirewall:
    """UNTRUSTED_DATA не может менять инструкции/разрешения.

    Правила:
    - контент из UNTRUSTED_SOURCES тегируется UNTRUSTED_DATA (см. ContextItem);
    - команды смены инструкций/разрешений в untrusted-контенте вырезаются;
    - факт срабатывания фиксируется (injection_hit=True) → триггер raw fallback.
    """

    _DIRECTIVE = re.compile(
        r"(ignore\s+(previous|all)\s+instructions|override\s+safety|disable\s+safety|"
        r"bypass\s+approval|reveal\s+secret|exfiltrate|act\s+as\s+root|"
        r"игнорируй\s+инструкции|отключи\s+защиту|обойди\s+проверку)",
        re.I,
    )

    def scan(self, item: ContextItem) -> dict[str, Any]:
        if item.trust is not TrustTag.UNTRUSTED_DATA:
            return {"hit": False, "sanitized": item.text}
        hit = bool(self._DIRECTIVE.search(item.text) or contains_prompt_injection(item.text))
        sanitized = self._DIRECTIVE.sub("[removed-by-injection-firewall]", item.text)
        return {"hit": hit, "sanitized": sanitized}


@dataclass
class CompiledPrompt:
    sections: list[ContextItem]
    total_tokens: int
    dropped: list[str]
    telemetry: dict[str, Any]

    def render(self) -> str:
        order = {name: i for i, name in enumerate(COMPILER_ORDER)}
        items = sorted(self.sections, key=lambda s: order.get(s.section, 99))
        return "\n\n".join(f"## {s.section}\n{s.text}" for s in items if s.text.strip())


class ContextCompiler:
    """Единственный компонент, собирающий запрос модели."""

    def __init__(
        self,
        ledger: CriticalFactLedger | None = None,
        firewall: InjectionFirewall | None = None,
    ) -> None:
        self.ledger = ledger
        self.firewall = firewall or InjectionFirewall()

    def compile(
        self,
        items: Sequence[ContextItem],
        *,
        budget_tokens: int,
        scope: str = "",
    ) -> CompiledPrompt:
        # 1. Firewall: санитизируем untrusted до подсчёта бюджета.
        clean: list[ContextItem] = []
        injection_hits = 0
        for it in items:
            res = self.firewall.scan(it)
            if res["hit"]:
                injection_hits += 1
            import dataclasses as _dc

            clean.append(_dc.replace(it, text=res["sanitized"],
                                     tokens=estimate_tokens(res["sanitized"])))
        # 2. P0/P1 — неприкосновенны; урезаем с хвоста P4→P2.
        protected = [i for i in clean if i.priority in (Priority.P0, Priority.P1)]
        flexible = sorted(
            [i for i in clean if i.priority not in (Priority.P0, Priority.P1)],
            key=lambda i: PRIORITY_RANK[i.priority],
        )
        protected_cost = sum(i.tokens for i in protected)
        dropped: list[str] = []
        kept_flex: list[ContextItem] = []
        remaining = budget_tokens - protected_cost
        if remaining < 0:
            # Бюджет меньше P0/P1: не дропаем, а честно фиксируем переполнение.
            # Вызывающий код обязан поднять бюджет или перейти на raw fallback.
            remaining = 0
        for it in flexible:
            if it.tokens <= remaining:
                kept_flex.append(it)
                remaining -= it.tokens
            elif it.must_preserve:
                # must_preserve вне P0/P1 — тоже не дропаем (нарушение — в telemetry).
                kept_flex.append(it)
                remaining -= it.tokens
            else:
                dropped.append(f"{it.section}:{it.source or '?'}")
        sections = protected + kept_flex
        total = sum(i.tokens for i in sections)
        overflow = total > budget_tokens
        telemetry = {
            "budget_tokens": budget_tokens,
            "used_tokens": total,
            "overflow_protected": overflow,
            "dropped": dropped,
            "injection_hits": injection_hits,
            "p0_p1_preserved": all(s in [x.section for x in sections]
                                   for s in ("System invariants", "User goal")
                                   if any(y.section == s for y in clean)),
            "waste_rate": round(len(dropped) / max(1, len(clean)), 4),
        }
        return CompiledPrompt(sections, total, dropped, telemetry)


# ---------------------------------------------------------------------------
# Иерархическое сжатие (не один пересказываемый summary)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SummaryNode:
    level: str  # step | episode | module | project
    summary_id: str
    text: str
    source_refs: list[str]
    fact_ids: list[str]


class HierarchicalCompressor:
    """Каждый summary ссылается на исходные факты (fact_ids + source_refs).

    `summarize_fn` — инъекция модели/детерминированного суммаризатора.
    По умолчанию — экстрактивная выжимка (без выдуманных формулировок):
    берёт первые значимые предложения + дословно сохраняет must_preserve факты.
    """

    def __init__(self, summarize_fn: Callable[[str], str] | None = None) -> None:
        self.summarize_fn = summarize_fn or self._extractive

    @staticmethod
    def _extractive(text: str, max_chars: int = 1200) -> str:
        sents = re.split(r"(?<=[.!?\n])\s+", text.strip())
        out, budget = [], max_chars
        for s in sents:
            if budget <= 0:
                break
            out.append(s[:budget])
            budget -= len(s)
        return " ".join(out).strip()

    def compress(
        self,
        *,
        level: str,
        texts: Sequence[str],
        refs: Sequence[str],
        ledger_facts: Sequence[CriticalFact],
        summary_id: str = "",
    ) -> tuple[SummaryNode, dict[str, Any]]:
        raw = "\n".join(texts)
        body = self.summarize_fn(raw)
        # must_preserve факты дописываются дословно — потеря отменяет summary.
        must = [f for f in ledger_facts if f.must_preserve]
        quoted = "\n".join(f"PRESERVED FACT [{f.fact_id}]: {f.normalized_fact}" for f in must)
        full = (body + ("\n" + quoted if quoted else "")).strip()
        check = CriticalFactLedger.verify_roundtrip(must, full)
        node = SummaryNode(
            level=level,
            summary_id=summary_id or stable_id("sum", level, sha256_text(raw)[:12]),
            text=full,
            source_refs=list(refs),
            fact_ids=[f.fact_id for f in ledger_facts],
        )
        # Потеря must_preserve → summary ОТМЕНЁН (возвращаем check.ok=False,
        # вызывающий код обязан использовать raw fallback).
        return node, {"ok": check["ok"], "missing": check["missing"]}


# ---------------------------------------------------------------------------
# Raw-context fallback
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FallbackSignals:
    retrieval_confidence: float = 1.0
    sources_conflict: bool = False
    verifier_disagrees: bool = False
    action_irreversible: bool = False
    summary_lost_fact: bool = False
    needs_exact_quote: bool = False
    injection_found: bool = False


def should_use_raw(signals: FallbackSignals, threshold: float = 0.35) -> dict[str, Any]:
    """Модель возвращается к оригиналу, если выполнено ХОТЯ БЫ одно условие."""
    reasons = []
    if signals.retrieval_confidence < threshold:
        reasons.append("low_retrieval_confidence")
    if signals.sources_conflict:
        reasons.append("sources_conflict")
    if signals.verifier_disagrees:
        reasons.append("verifier_disagrees")
    if signals.action_irreversible:
        reasons.append("irreversible_action")
    if signals.summary_lost_fact:
        reasons.append("summary_lost_fact")
    if signals.needs_exact_quote:
        reasons.append("needs_exact_quote")
    if signals.injection_found:
        reasons.append("injection_found")
    return {"use_raw": bool(reasons), "reasons": reasons}
