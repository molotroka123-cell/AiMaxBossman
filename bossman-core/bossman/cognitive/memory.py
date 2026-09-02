"""Память 7/10 → 10/10: пять уровней, фильтр записи, R-формула, конфликты, забывание.

Уровни (независимые tier-namespaces, не один JSON):
- WORKING    — состояние текущей задачи (короткий TTL, scope=task/run).
- EPISODIC   — проверенные прошлые выполнения (перенос fix на похожие задачи).
- SEMANTIC   — устойчивые факты (архитектурные ограничения проекта).
- PROCEDURAL — проверенные навыки (безопасная миграция SQLite).
- QUARANTINE — сомнительное содержимое (непроверенный совет, injection).

Замкнутый контур: наблюдение (candidate) → проверка (write filter +
независимый verifier) → сохранение (durable + tombstones) → использование
(R-скоринг со scope-изоляцией) → измерение (verify.py: precision/recall/
transfer/leakage/poison/deletion).
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable, Sequence

from .storage import (
    CognitiveStore,
    FixedClock,
    SystemClock,
    json_dumps,
    parse_ts,
    sha256_text,
    stable_id,
    utcnow_iso,
)

SCHEMA_VERSION = 1

# Эвристики injection: контент помечается QUARANTINE, никогда не исполняется.
_INJECTION_PATTERNS = (
    "ignore previous instructions",
    "ignore all instructions",
    "system prompt",
    "override safety",
    "disable safety",
    "exfiltrate",
    "send credentials",
    "reveal secret",
    "bypass approval",
    "act as root",
    "sudo ",
    "игнорируй инструкции",
    "отключи защиту",
    "обойди проверку",
    "покажи секрет",
)


class Tier(str, Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    QUARANTINE = "quarantine"


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    INDEPENDENTLY_VERIFIED = "independently_verified"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    REVOKED = "revoked"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    SECRET = "secret"


@dataclass(slots=True)
class MemoryRecord10:
    """Все поля из ТЗ. Без них запись нельзя использовать для критических решений."""

    memory_id: str
    text: str
    tier: Tier = Tier.SEMANTIC
    owner_id: str = ""
    principal_id: str = ""
    source_type: str = ""
    source_id: str = ""
    task_id: str = ""
    run_id: str = ""
    session_id: str = ""
    project_id: str = ""
    corpus_id: str = ""
    domain_id: str = ""
    head_sha: str = ""
    environment_digest: str = ""
    created_at: str = ""
    collected_at: str = ""
    expires_at: str = ""
    confidence: float = 0.5
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    verifier_id: str = ""
    sensitivity: Sensitivity = Sensitivity.NORMAL
    allowed_consumers: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    content_hash: str = ""
    # Служебные переносимые сигналы для R-формулы (не часть ТЗ-полей, но хранятся
    # в extra чтобы не раздувать схему; критические решения их не требуют).
    transfer_wins: int = 0  # P: успешный перенос на другие задачи
    transfer_uses: int = 0

    def __post_init__(self) -> None:
        if not self.content_hash and self.text:
            self.content_hash = sha256_text(self.text)
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def to_row(self) -> tuple:
        return (
            self.memory_id, self.tier.value, self.text,
            self.owner_id, self.principal_id, self.source_type, self.source_id,
            self.task_id, self.run_id, self.session_id, self.project_id,
            self.corpus_id, self.domain_id, self.head_sha, self.environment_digest,
            self.created_at, self.collected_at, self.expires_at,
            self.confidence, self.verification_status.value, self.verifier_id,
            self.sensitivity.value, json_dumps(self.allowed_consumers),
            json_dumps(self.contradictions), json_dumps(self.supersedes),
            self.schema_version, self.content_hash,
            json_dumps({"transfer_wins": self.transfer_wins,
                        "transfer_uses": self.transfer_uses}),
        )

    @staticmethod
    def from_row(r: Any) -> "MemoryRecord10":
        import json as _json

        def _jl(s: str) -> list:
            try:
                v = _json.loads(s or "[]")
                return v if isinstance(v, list) else []
            except Exception:
                return []

        try:
            extra = _json.loads(r["extra"] or "{}")
        except Exception:
            extra = {}
        return MemoryRecord10(
            memory_id=r["memory_id"], text=r["text"], tier=Tier(r["tier"]),
            owner_id=r["owner_id"], principal_id=r["principal_id"],
            source_type=r["source_type"], source_id=r["source_id"],
            task_id=r["task_id"], run_id=r["run_id"], session_id=r["session_id"],
            project_id=r["project_id"], corpus_id=r["corpus_id"],
            domain_id=r["domain_id"], head_sha=r["head_sha"],
            environment_digest=r["environment_digest"],
            created_at=r["created_at"], collected_at=r["collected_at"],
            expires_at=r["expires_at"], confidence=float(r["confidence"]),
            verification_status=VerificationStatus(r["verification_status"]),
            verifier_id=r["verifier_id"],
            sensitivity=Sensitivity(r["sensitivity"] or "normal"),
            allowed_consumers=_jl(r["allowed_consumers"]),
            contradictions=_jl(r["contradictions"]),
            supersedes=_jl(r["supersedes"]),
            schema_version=int(r["schema_version"] or 1),
            content_hash=r["content_hash"],
            transfer_wins=int(extra.get("transfer_wins", 0)),
            transfer_uses=int(extra.get("transfer_uses", 0)),
        )


# ---------------------------------------------------------------------------
# Фильтр записи (точный порядок из ТЗ)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WriteEvidence:
    independently_verified: bool = False
    verifier_id: str = ""
    executor_id: str = ""
    collected_at: str = ""  # когда evidence собрано
    protected_tests_passed: bool = True
    security_worsened: bool = False


@dataclass(frozen=True)
class WriteDecision:
    action: str  # ACCEPT | QUARANTINE | REJECT
    reason: str


def contains_prompt_injection(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in _INJECTION_PATTERNS)


def is_stale_or_future(collected_at: str, now_ts: float, max_age_s: float = 30 * 86400) -> str:
    """'' — свежее; иначе причина REJECT ('stale' / 'from_future' / 'bad_timestamp')."""
    ts = parse_ts(collected_at)
    if ts <= 0:
        return "bad_timestamp"
    if ts > now_ts + 300:  # +5 мин допуск на скос часов
        return "from_future"
    if now_ts - ts > max_age_s:
        return "stale"
    return ""


class WriteFilter:
    """Порядок проверок — ровно как в ТЗ. fail-closed: сомнение → QUARANTINE/REJECT."""

    def __init__(self, max_age_s: float = 30 * 86400) -> None:
        self.max_age_s = max_age_s

    def decide(
        self,
        text: str,
        evidence: WriteEvidence,
        *,
        now_ts: float | None = None,
    ) -> WriteDecision:
        now = time_now() if now_ts is None else now_ts
        if not evidence.independently_verified:
            return WriteDecision("QUARANTINE", "not_independently_verified")
        freshness = is_stale_or_future(evidence.collected_at, now, self.max_age_s)
        if freshness:
            return WriteDecision("REJECT", freshness)
        if evidence.verifier_id and evidence.verifier_id == evidence.executor_id:
            return WriteDecision("REJECT", "verifier_same_as_executor")
        if contains_prompt_injection(text):
            return WriteDecision("QUARANTINE", "prompt_injection")
        if not evidence.protected_tests_passed:
            return WriteDecision("REJECT", "protected_tests_failed")
        if evidence.security_worsened:
            return WriteDecision("REJECT", "security_worsened")
        return WriteDecision("ACCEPT", "ok")


def time_now() -> float:
    import time as _t

    return _t.time()


# ---------------------------------------------------------------------------
# R-формула умного поиска
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetrievalWeights:
    """Веса R(m,q). Подобрать на dev, ЗАФИКСИРОВАТЬ перед holdout (см. freeze())."""

    w_s: float = 0.30  # semantic similarity
    w_t: float = 0.15  # task fit
    w_v: float = 0.20  # verification quality
    w_r: float = 0.10  # freshness (recency)
    w_p: float = 0.10  # transfer wins
    w_a: float = 0.10  # age penalty
    w_c: float = 0.25  # contradiction penalty
    w_x: float = 0.15  # risk + context cost penalty
    frozen: bool = False

    def freeze(self) -> "RetrievalWeights":
        import dataclasses as _dc

        return _dc.replace(self, frozen=True)


DEFAULT_WEIGHTS = RetrievalWeights().freeze()

# Порог уверенности retrieval: ниже — raw-context fallback (см. context.py).
RETRIEVAL_CONFIDENCE_THRESHOLD = 0.35


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"\w{2,}", text, flags=re.UNICODE)}


def semantic_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / math.sqrt(len(ta) * len(tb))


def verification_quality(m: MemoryRecord10) -> float:
    base = {
        VerificationStatus.INDEPENDENTLY_VERIFIED: 1.0,
        VerificationStatus.UNVERIFIED: 0.2,
        VerificationStatus.QUARANTINED: 0.0,
        VerificationStatus.REJECTED: 0.0,
        VerificationStatus.SUPERSEDED: 0.1,
        VerificationStatus.EXPIRED: 0.0,
        VerificationStatus.REVOKED: 0.0,
    }[m.verification_status]
    return max(0.0, min(1.0, base * 0.7 + m.confidence * 0.3))


@dataclass(frozen=True)
class ScoredMemory:
    record: MemoryRecord10
    score: float
    parts: dict[str, float]


def score_memory(
    m: MemoryRecord10,
    query: str,
    *,
    query_task_id: str = "",
    query_domain_id: str = "",
    now_ts: float | None = None,
    weights: RetrievalWeights = DEFAULT_WEIGHTS,
) -> ScoredMemory:
    """R(m,q) = ws*S + wt*T + wv*V + wr*R + wp*P − wa*A − wc*C − wx*X."""
    now = time_now() if now_ts is None else now_ts
    s = semantic_similarity(m.text, query)
    t = 0.0
    if query_task_id and m.task_id == query_task_id:
        t = 1.0
    elif query_domain_id and m.domain_id == query_domain_id:
        t = 0.6
    elif _tokens(query) & _tokens(m.task_id + " " + m.domain_id):
        t = 0.3
    v = verification_quality(m)
    age_days = 0.0
    if m.collected_at:
        age_days = max(0.0, (now - parse_ts(m.collected_at)) / 86400.0)
    r = math.exp(-age_days / 30.0)  # свежесть: затухание 30 дней
    p = (m.transfer_wins / m.transfer_uses) if m.transfer_uses else 0.0
    a = min(1.0, age_days / 180.0)  # устаревание: полное за ~полгода
    c = min(1.0, len(m.contradictions) / 3.0)
    x = 0.0
    if m.sensitivity in (Sensitivity.SENSITIVE, Sensitivity.SECRET):
        x += 0.4
    x += min(0.6, len(m.text) / 4000.0)  # стоимость контекста
    if m.tier is Tier.QUARANTINE:
        x += 1.0
    score = (
        weights.w_s * s
        + weights.w_t * t
        + weights.w_v * v
        + weights.w_r * r
        + weights.w_p * p
        - weights.w_a * a
        - weights.w_c * c
        - weights.w_x * x
    )
    return ScoredMemory(m, score, {"S": s, "T": t, "V": v, "R": r, "P": p, "A": a, "C": c, "X": x})


def calibrate_weights(
    dev_pairs: Sequence[tuple[MemoryRecord10, str, float]],
    *,
    base: RetrievalWeights = DEFAULT_WEIGHTS,
    steps: int = 40,
) -> RetrievalWeights:
    """Подбор весов на DEV-наборе (record, query, graded_relevance 0..1).

    Простой coordinate ascent по MSE hinge: детерминирован (seed фиксирован),
    результат НУЖНО freeze() перед holdout. Возвращает НЕзамороженные веса —
    freeze делает вызывающий код явно (защита от случайного holdout-подгона).
    """
    import random as _random

    rng = _random.Random(20260902)
    best = base
    if best.frozen:
        import dataclasses as _dc

        best = _dc.replace(best, frozen=False)

    def loss(w: RetrievalWeights) -> float:
        err = 0.0
        for m, q, y in dev_pairs:
            pred = score_memory(m, q, weights=w).score
            # hinge: релевантное (y>0.5) должно быть > 0.35, нерелевантное — ниже
            target = 0.6 if y > 0.5 else 0.1
            err += (pred - target) ** 2
        # L2-регуляризация к base: не даём весам разлететься на малом dev.
        for k in ("w_s", "w_t", "w_v", "w_r", "w_p", "w_a", "w_c", "w_x"):
            err += 0.01 * (getattr(w, k) - getattr(base, k)) ** 2
        return err

    import dataclasses as _dc

    fields = ("w_s", "w_t", "w_v", "w_r", "w_p", "w_a", "w_c", "w_x")
    cur_loss = loss(best)
    for _ in range(steps):
        k = fields[rng.randrange(len(fields))]
        delta = rng.uniform(-0.05, 0.05)
        cand = _dc.replace(best, **{k: max(0.0, min(1.0, getattr(best, k) + delta))})
        cand_loss = loss(cand)
        if cand_loss < cur_loss:
            best, cur_loss = cand, cand_loss
    return best


# ---------------------------------------------------------------------------
# Хранилище памяти (durable) + scope-изоляция + забывание
# ---------------------------------------------------------------------------

_NEG = re.compile(
    r"\b(no|not|never|disable|disabled|false|without|can't|cannot|won't|нет|не|никогда|"
    r"отключ|запрещ|нельзя)\b",
    re.I,
)


def _conflict_key(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"\w{3,}", text, flags=re.UNICODE)}


class MemoryStore:
    """Durable-фасад над CognitiveStore для memories10."""

    # WORKING живёт коротко: дефолтный TTL 24ч, остальные — 180 дней.
    DEFAULT_TTL_S: dict[Tier, float] = {
        Tier.WORKING: 86400.0,
        Tier.EPISODIC: 180 * 86400.0,
        Tier.SEMANTIC: 180 * 86400.0,
        Tier.PROCEDURAL: 180 * 86400.0,
        Tier.QUARANTINE: 30 * 86400.0,
    }

    def __init__(
        self,
        store: CognitiveStore,
        write_filter: WriteFilter | None = None,
        weights: RetrievalWeights = DEFAULT_WEIGHTS,
    ) -> None:
        self.store = store
        self.filter = write_filter or WriteFilter()
        self.weights = weights

    # -- write ----------------------------------------------------------
    def propose(
        self,
        text: str,
        *,
        tier: Tier,
        owner_id: str,
        principal_id: str = "",
        source_type: str = "",
        source_id: str = "",
        task_id: str = "",
        run_id: str = "",
        session_id: str = "",
        project_id: str = "",
        corpus_id: str = "",
        domain_id: str = "",
        head_sha: str = "",
        environment_digest: str = "",
        confidence: float = 0.5,
        sensitivity: Sensitivity = Sensitivity.NORMAL,
        allowed_consumers: Sequence[str] = (),
        evidence: WriteEvidence,
        collected_at_override: str = "",
        ttl_s: float | None = None,
        memory_id: str = "",
    ) -> tuple[MemoryRecord10, WriteDecision]:
        now_iso = self.store.clock.now_iso()
        now_ts = self.store.clock.now_ts()
        collected = collected_at_override or evidence.collected_at or now_iso
        decision = self.filter.decide(text, evidence, now_ts=now_ts)
        if decision.action == "REJECT":
            return self._rejected_stub(text, owner_id, project_id, tier), decision
        final_tier = Tier.QUARANTINE if decision.action == "QUARANTINE" else tier
        status = (
            VerificationStatus.QUARANTINED
            if decision.action == "QUARANTINE"
            else (
                VerificationStatus.INDEPENDENTLY_VERIFIED
                if evidence.independently_verified
                else VerificationStatus.UNVERIFIED
            )
        )
        ttl = self.DEFAULT_TTL_S[final_tier] if ttl_s is None else ttl_s
        import datetime as _dt

        try:
            base_ts = parse_ts(collected) or now_ts
        except Exception:
            base_ts = now_ts
        expires = _dt.datetime.fromtimestamp(
            base_ts + ttl, tz=_dt.timezone.utc
        ).isoformat()
        rec = MemoryRecord10(
            memory_id=memory_id
            or stable_id(
                "mem", owner_id, project_id, final_tier.value,
                sha256_text(text)[:12], collected,
            ),
            text=text.strip(),
            tier=final_tier,
            owner_id=owner_id,
            principal_id=principal_id,
            source_type=source_type,
            source_id=source_id,
            task_id=task_id,
            run_id=run_id,
            session_id=session_id,
            project_id=project_id,
            corpus_id=corpus_id,
            domain_id=domain_id,
            head_sha=head_sha,
            environment_digest=environment_digest,
            created_at=now_iso,
            collected_at=collected,
            expires_at=expires,
            confidence=confidence,
            verification_status=status,
            verifier_id=evidence.verifier_id,
            sensitivity=sensitivity,
            allowed_consumers=list(allowed_consumers),
        )
        if self.store.is_tombstoned(rec.memory_id):
            # Воскрешение удалённого запрещено: новый ID вместо перезаписи.
            rec.memory_id = stable_id(
                "mem", owner_id, project_id, final_tier.value,
                sha256_text(text)[:12], now_iso,
            )
        self._upsert(rec)
        self._detect_conflicts(rec)
        self.store.invalidate_cache()
        return rec, decision

    def _rejected_stub(
        self, text: str, owner_id: str, project_id: str, tier: Tier
    ) -> MemoryRecord10:
        return MemoryRecord10(
            memory_id="rejected",
            text=text[:500],
            tier=tier,
            owner_id=owner_id,
            project_id=project_id,
            verification_status=VerificationStatus.REJECTED,
            created_at=self.store.clock.now_iso(),
            collected_at=self.store.clock.now_iso(),
        )

    def _upsert(self, rec: MemoryRecord10) -> None:
        self.store.execute(
            """INSERT INTO memories10 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(memory_id) DO UPDATE SET tier=excluded.tier,text=excluded.text,
            owner_id=excluded.owner_id,principal_id=excluded.principal_id,
            source_type=excluded.source_type,source_id=excluded.source_id,
            task_id=excluded.task_id,run_id=excluded.run_id,session_id=excluded.session_id,
            project_id=excluded.project_id,corpus_id=excluded.corpus_id,domain_id=excluded.domain_id,
            head_sha=excluded.head_sha,environment_digest=excluded.environment_digest,
            created_at=excluded.created_at,collected_at=excluded.collected_at,expires_at=excluded.expires_at,
            confidence=excluded.confidence,verification_status=excluded.verification_status,
            verifier_id=excluded.verifier_id,sensitivity=excluded.sensitivity,
            allowed_consumers=excluded.allowed_consumers,contradictions=excluded.contradictions,
            supersedes=excluded.supersedes,schema_version=excluded.schema_version,
            content_hash=excluded.content_hash,extra=excluded.extra""",
            rec.to_row(),
        )
        self.store.commit()

    def get(self, memory_id: str) -> MemoryRecord10 | None:
        row = self.store.execute(
            "SELECT * FROM memories10 WHERE memory_id=?", (memory_id,)
        ).fetchone()
        if not row or self.store.is_tombstoned(memory_id):
            return None
        return MemoryRecord10.from_row(row)

    # -- read (scope-изолированный) --------------------------------------
    def search(
        self,
        query: str,
        *,
        owner_id: str,
        project_id: str = "",
        allowed_consumer: str = "",
        tiers: Sequence[Tier] | None = None,
        include_quarantine: bool = False,
        query_task_id: str = "",
        query_domain_id: str = "",
        limit: int = 12,
    ) -> list[ScoredMemory]:
        """Scope-изоляция: чужой owner/project НЕ возвращается никогда.

        QUARANTINE исключён по умолчанию (только с include_quarantine=True и
        только для инспекции, не для критических решений).
        """
        cache_key = stable_id(
            "q", owner_id, project_id, allowed_consumer, query,
            ",".join(sorted(t.value for t in tiers)) if tiers else "",
            query_task_id, query_domain_id, str(limit),
            "Q" if include_quarantine else "N",
        )
        cached = self.store.cache_get(cache_key)
        now_ts = self.store.clock.now_ts()
        rows: list[dict[str, Any]] = []
        if cached is None:
            sql = "SELECT * FROM memories10 WHERE owner_id=?"
            params: list[Any] = [owner_id]
            if project_id:
                sql += " AND project_id=?"
                params.append(project_id)
            for r in self.store.execute(sql, tuple(params)).fetchall():
                rows.append(dict(r))
            self.store.cache_put(cache_key, rows)
        else:
            rows = cached
        tombstoned_hashes = self.store.tombstone_hashes()
        out: list[ScoredMemory] = []
        for d in rows:
            if self.store.is_tombstoned(d["memory_id"]):
                continue
            if d["content_hash"] in tombstoned_hashes:
                continue
            try:
                m = MemoryRecord10.from_row(d)
            except Exception:
                continue
            if m.verification_status in (
                VerificationStatus.REJECTED,
                VerificationStatus.EXPIRED,
                VerificationStatus.REVOKED,
            ):
                continue
            if m.tier is Tier.QUARANTINE and not include_quarantine:
                continue
            if tiers and m.tier not in tiers:
                continue
            if m.expires_at and parse_ts(m.expires_at) <= now_ts:
                continue  # протухшее не отдаём; GC пометит EXPIRED позже
            if m.sensitivity in (Sensitivity.SENSITIVE.value, Sensitivity.SECRET.value):
                # чувствительное — только разрешённым потребителям
                if allowed_consumer and allowed_consumer not in m.allowed_consumers:
                    continue
                if not allowed_consumer and m.allowed_consumers:
                    continue
            out.append(
                score_memory(
                    m, query,
                    query_task_id=query_task_id,
                    query_domain_id=query_domain_id,
                    now_ts=now_ts, weights=self.weights,
                )
            )
        out.sort(key=lambda s: s.score, reverse=True)
        return out[:limit]

    # -- conflicts --------------------------------------------------------
    def _detect_conflicts(self, candidate: MemoryRecord10) -> None:
        if candidate.verification_status is VerificationStatus.REJECTED:
            return
        cwords = _conflict_key(candidate.text)
        cneg = bool(_NEG.search(candidate.text))
        rows = self.store.execute(
            """SELECT * FROM memories10 WHERE owner_id=? AND project_id=?
               AND tier=? AND verification_status NOT IN ('rejected','revoked','expired')""",
            (candidate.owner_id, candidate.project_id, candidate.tier.value),
        ).fetchall()
        for r in rows:
            if r["memory_id"] == candidate.memory_id:
                continue
            old = MemoryRecord10.from_row(r)
            if old.verification_status is VerificationStatus.SUPERSEDED:
                continue
            owords = _conflict_key(old.text)
            denom = max(1, min(len(cwords), len(owords)))
            overlap = len(cwords & owords) / denom
            if overlap >= 0.6 and bool(_NEG.search(old.text)) != cneg:
                # Не выбираем автоматически более уверенный. Фиксируем конфликт,
                # обе записи живут, решение — только через новое evidence.
                if old.memory_id not in candidate.contradictions:
                    candidate.contradictions.append(old.memory_id)
                if candidate.memory_id not in old.contradictions:
                    old.contradictions.append(candidate.memory_id)
                self._upsert(old)
                cid = stable_id("cfg", candidate.memory_id, old.memory_id)
                self.store.execute(
                    """INSERT INTO conflicts VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(conflict_id) DO NOTHING""",
                    (
                        cid, candidate.memory_id, old.memory_id,
                        self.store.clock.now_iso(), "open", "",
                        json_dumps([]),
                        json_dumps([{
                            "at": self.store.clock.now_iso(),
                            "event": "detected",
                            "overlap": round(overlap, 3),
                        }]),
                    ),
                )
        self._upsert(candidate)
        self.store.commit()

    def resolve_conflict(
        self,
        conflict_id: str,
        *,
        winner_id: str,
        new_evidence: Sequence[str],
        resolver_id: str,
    ) -> dict[str, Any]:
        """Разрешение: требуется новое evidence; проигравший → SUPERSEDED (не delete).

        История решения сохраняется в conflicts.history.
        """
        row = self.store.execute(
            "SELECT * FROM conflicts WHERE conflict_id=?", (conflict_id,)
        ).fetchone()
        if not row:
            raise KeyError(conflict_id)
        if not new_evidence:
            raise ValueError("resolution requires new evidence")
        loser = row["memory_a"] if row["memory_b"] == winner_id else row["memory_b"]
        winner = self.get(winner_id)
        loser_rec = self.get(loser)
        if not winner or not loser_rec:
            raise KeyError("conflict party missing (tombstoned?)")
        loser_rec.verification_status = VerificationStatus.SUPERSEDED
        if winner_id not in loser_rec.supersedes:
            loser_rec.supersedes.append(winner_id)
        loser_rec.contradictions = [c for c in loser_rec.contradictions if c != winner_id]
        winner.contradictions = [c for c in winner.contradictions if c != loser]
        if loser not in winner.supersedes:
            pass  # winner.supersedes хранит своих предшественников, не жертв
        self._upsert(loser_rec)
        self._upsert(winner)
        import json as _json

        history = _json.loads(row["history"] or "[]")
        history.append({
            "at": self.store.clock.now_iso(),
            "event": "resolved",
            "winner": winner_id,
            "loser": loser,
            "resolver": resolver_id,
            "evidence": list(new_evidence),
        })
        self.store.execute(
            "UPDATE conflicts SET resolution='resolved', winner_id=?, evidence=?, history=? WHERE conflict_id=?",
            (winner_id, json_dumps(list(new_evidence)), json_dumps(history), conflict_id),
        )
        self.store.commit()
        self.store.invalidate_cache()
        return {"conflict_id": conflict_id, "winner": winner_id, "loser": loser}

    def open_conflicts(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.store.execute(
                "SELECT * FROM conflicts WHERE resolution='open'"
            ).fetchall()
        ]

    # -- transfer feedback (P-компонента R) --------------------------------
    def report_transfer(self, memory_id: str, *, success: bool) -> None:
        m = self.get(memory_id)
        if not m:
            raise KeyError(memory_id)
        m.transfer_uses += 1
        if success:
            m.transfer_wins += 1
        # Negative transfer: три провала подряд без успехов → QUARANTINE на ревизию.
        if m.transfer_uses >= 3 and m.transfer_wins == 0:
            m.tier = Tier.QUARANTINE
            m.verification_status = VerificationStatus.QUARANTINED
        self._upsert(m)
        self.store.invalidate_cache()

    # -- forgetting ----------------------------------------------------------
    def delete(
        self, memory_id: str, *, reason: str = "manual",
        requester_owner: str = "",
    ) -> bool:
        """Удаление с tombstone. Scope: только свой owner (иначе False)."""
        row = self.store.execute(
            "SELECT * FROM memories10 WHERE memory_id=?", (memory_id,)
        ).fetchone()
        if not row:
            return False
        if requester_owner and row["owner_id"] != requester_owner:
            return False
        self.store.execute(
            """INSERT INTO tombstones VALUES (?,?,?,?)
            ON CONFLICT(memory_id) DO NOTHING""",
            (memory_id, row["content_hash"], self.store.clock.now_iso(), reason),
        )
        self.store.execute("DELETE FROM memories10 WHERE memory_id=?", (memory_id,))
        self.store.commit()
        self.store.invalidate_cache()
        return True

    def revoke_sensitive(self, owner_id: str, project_id: str = "") -> int:
        """Отзыв чувствительных данных владельца (GDPR-style). Возвращает число."""
        sql = """SELECT memory_id FROM memories10 WHERE owner_id=?
                 AND sensitivity IN ('sensitive','secret')"""
        params: list[Any] = [owner_id]
        if project_id:
            sql += " AND project_id=?"
            params.append(project_id)
        ids = [r[0] for r in self.store.execute(sql, tuple(params)).fetchall()]
        for mid in ids:
            self.delete(mid, reason="sensitive_revoke", requester_owner=owner_id)
        return len(ids)

    def garbage_collect(self) -> dict[str, int]:
        """TTL-expiry + удаление протухшего в tombstones. Возвращает счётчики."""
        now_ts = self.store.clock.now_ts()
        expired = 0
        for r in self.store.execute("SELECT * FROM memories10").fetchall():
            if r["expires_at"] and parse_ts(r["expires_at"]) <= now_ts:
                self.store.execute(
                    """INSERT INTO tombstones VALUES (?,?,?,?)
                    ON CONFLICT(memory_id) DO NOTHING""",
                    (r["memory_id"], r["content_hash"],
                     self.store.clock.now_iso(), "ttl_expired"),
                )
                self.store.execute(
                    "DELETE FROM memories10 WHERE memory_id=?", (r["memory_id"],)
                )
                expired += 1
        self.store.commit()
        self.store.invalidate_cache()
        return {"expired": expired}

    def assert_no_residual(self, memory_id: str, content_hash: str = "") -> dict[str, Any]:
        """Проверка, что удалённая запись нигде не возвращается.

        Проверяет: memories10, tombstone-наличие, retrieval-кэш, поиск по
        content_hash. Backup-проверка — через `extra_backup_probe` адаптера
        (см. INTEGRATION-GUIDE); здесь — локальный уровень.
        """
        row = self.store.execute(
            "SELECT 1 FROM memories10 WHERE memory_id=?", (memory_id,)
        ).fetchone()
        tomb = self.store.is_tombstoned(memory_id)
        cache_hit = any(
            any(d.get("memory_id") == memory_id for d in rows)
            for rows in self.store._cache.values()
        )
        hash_hit = False
        if content_hash:
            hash_hit = (
                self.store.execute(
                    "SELECT 1 FROM memories10 WHERE content_hash=?", (content_hash,)
                ).fetchone()
                is not None
            )
        ok = (row is None) and tomb and (not cache_hit) and (not hash_hit)
        return {
            "memory_id": memory_id, "ok": ok,
            "still_in_store": row is not None,
            "tombstoned": tomb, "in_cache": cache_hit,
            "hash_resurfaced": hash_hit,
        }

    # -- restart durability --------------------------------------------------
    def count_verified(self, owner_id: str = "") -> int:
        sql = """SELECT COUNT(*) FROM memories10
                 WHERE verification_status='independently_verified'"""
        params: tuple = ()
        if owner_id:
            sql += " AND owner_id=?"
            params = (owner_id,)
        return int(self.store.execute(sql, params).fetchone()[0])
