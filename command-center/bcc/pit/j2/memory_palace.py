"""Jeff 2.0 module 4: Memory Palace - retrieval over ONE participant's consented passport layers.

Layers read (never mixed with any other person's data, one ``person_key`` per call):

* ``facts``  - ``facts.jsonl`` through :func:`bcc.pit.passport.facts_layer` (envelope guaranteed);
* ``events`` - ``raw/events.jsonl`` (only while ``raw_history_enabled``);
* ``style``  - ``style.json`` (an inference, always labelled as a guess).

Ranking = relevance x memory strength:

* relevance: BM25 (implemented here, Russian stemming-lite) optionally blended with cosine similarity of
  local embeddings from an INJECTED ``embed`` callable (never a cloud call; failure falls back to lexical);
* strength: ``0.5 + 0.3 * retention + 0.2 * importance`` where ``retention = exp(-age / stability)`` is a
  forgetting curve (stability grows with importance and with participant confirmation).

Every turn re-reads consent and the files (the index cache is keyed by file signature), so ``/correct``,
``/forget``, ``/pause_memory`` and ``/revoke_consent`` take effect on the very next message. Retrieval is
bounded (top-k, characters, index size). Facts contradicting each other are surfaced, never silently merged.
"""
from __future__ import annotations

import asyncio
import calendar
import inspect
import json
import math
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Sequence

from .. import passport
from ..secret_filter import redact_secrets
from .contract import Advice, BaseModule, TurnContext

Embed = Callable[[list[str]], "Sequence[Sequence[float]] | Awaitable[Sequence[Sequence[float]]]"]

TOP_K = 6
NOTE_CHAR_BUDGET = 900
ITEM_CHARS = 300
MAX_FACTS = 1000
MAX_EVENTS = 300
MAX_LISTED = 12
VIEW_CHAR_BUDGET = 1800
EMBED_TIMEOUT_S = 0.25
MAX_CACHED_PEOPLE = 32
MAX_CACHED_VECTORS = 1500
BM25_K1 = 1.5
BM25_B = 0.75

_WORD = re.compile(r"[\w]+", re.UNICODE)
_NEGATIONS = frozenset({"не", "нет", "никогда", "ни", "not", "no", "never", "dont", "cant", "без"})
_STOP = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня
еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам сказал
ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего
раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы
нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас
про них какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более
всегда конечно всю между это ты ты знаешь помнишь думаешь
the a an and or of to in is are was were be do does did you i me my about what why how that this it
""".split())
_SUFFIXES = tuple(sorted((
    "ившись", "ываясь", "иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "ешь", "ишь", "ете",
    "ите", "ают", "яют", "уют", "ают", "ает", "яет", "ует", "ать", "ять", "еть", "ить", "ыть", "уть",
    "ая", "яя", "ое", "ее", "ые", "ие", "ой", "ей", "ий", "ый", "ую", "юю", "ом", "ем", "ах", "ях", "ов",
    "ев", "ам", "ям", "ла", "ло", "ли", "ет", "ут", "ют", "ит", "ат", "ят", "ешь", "ся", "сь",
    "а", "я", "ы", "и", "о", "е", "у", "ю", "ь", "й",
    "ing", "ed", "es", "s"), key=len, reverse=True))

_KNOW_RE = re.compile(
    r"(?:что\s+ты\s+(?:уже\s+)?(?:обо\s+мне|про\s+меня)\s+(?:знаешь|помнишь)|"
    r"что\s+ты\s+(?:уже\s+)?(?:знаешь|помнишь)\s+(?:обо\s+мне|про\s+меня)|что\s+ты\s+(?:уже\s+)?помнишь\s*[?!.]*\s*$|"
    r"что\s+тебе\s+обо\s+мне\s+известно|what\s+do\s+you\s+(?:know|remember)\s+about\s+me|"
    r"what\s+have\s+you\s+(?:got|stored)\s+on\s+me)", re.I)
_SELF_REF = re.compile(r"\b(?:я|мне|меня|мной|мой|моя|моё|мое|мои|мою|моего|моей|my|me|i)\b", re.I)
_WHY_RE = re.compile(
    r"(?:почему\s+ты\s+(?:так\s+)?(?:думаешь|считаешь|решил|решила|это\s+знаешь)|откуда\s+ты\s+(?:это\s+|про\s+)?"
    r"(?:знаешь|взял|взяла)|на\s+чём\s+(?:это\s+)?основан|why\s+do\s+you\s+think|how\s+do\s+you\s+know)", re.I)
_TRIGGER_WORDS = frozenset("что ты знаешь помнишь обо мне про меня тебе известно почему так думаешь считаешь решил "
                           "откуда это взял на чём основано основан why do you think how know remember about me "
                           "what have got stored on".split())

_SOURCE_RU = {"message": "твоё сообщение", "voice": "голосовое", "command": "твоя команда",
              "master_parser": "разбор переписки (предположение)", "legacy": "старая запись",
              "event": "твоя переписка", "style": "разбор стиля (предположение, не факт)"}
_FRESH_RU = {"fresh": "актуален", "stale": "мог устареть", "expired": "истёк", "unknown": "дата неизвестна"}
_SINGLE_VALUED = ("name", "имя", "зовут", "city", "город", "age", "возраст", "birth", "location", "employer",
                  "job_title", "profession", "профес", "должност", "relation_label")
_IDENTITY_CATEGORIES = ("identity", "relationship", "relation", "personal", "profile", "family")


# ------------------------------------------------------------------------------- text analysis
def normalise(text: str) -> str:
    return str(text or "").lower().replace("ё", "е")


def stem(word: str) -> str:
    """Stemming-lite: strip one inflectional suffix, keeping at least a 3-letter stem."""
    for _ in range(2):
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[: -len(suffix)]
                break
        else:
            break
    return word


def tokenize(text: str, *, keep_negations: bool = False) -> list[str]:
    out: list[str] = []
    for match in _WORD.finditer(normalise(text)):
        word = match.group(0).strip("_")
        if not word or word.isdigit() and len(word) < 2:
            continue
        if word in _NEGATIONS:
            if keep_negations:
                out.append(word)
            continue
        if word in _STOP or len(word) < 2:
            continue
        out.append(stem(word))
    return out


class BM25:
    """Okapi BM25 over a fixed small corpus (one participant's memory)."""

    def __init__(self, docs: Sequence[Sequence[str]], *, k1: float = BM25_K1, b: float = BM25_B) -> None:
        self.k1, self.b = k1, b
        self.docs = [list(doc) for doc in docs]
        self.n = len(self.docs)
        self.avgdl = (sum(len(d) for d in self.docs) / self.n) if self.n else 0.0
        self.df: dict[str, int] = {}
        self.tf: list[dict[str, int]] = []
        for doc in self.docs:
            counts: dict[str, int] = {}
            for term in doc:
                counts[term] = counts.get(term, 0) + 1
            self.tf.append(counts)
            for term in counts:
                self.df[term] = self.df.get(term, 0) + 1

    def idf(self, term: str) -> float:
        n_t = self.df.get(term, 0)
        return math.log(1.0 + (self.n - n_t + 0.5) / (n_t + 0.5))

    def score(self, query: Sequence[str], index: int) -> float:
        counts, length = self.tf[index], len(self.docs[index])
        total = 0.0
        for term in set(query):
            freq = counts.get(term, 0)
            if not freq:
                continue
            denom = freq + self.k1 * (1 - self.b + self.b * (length / self.avgdl if self.avgdl else 1.0))
            total += self.idf(term) * freq * (self.k1 + 1) / denom
        return total

    def scores(self, query: Sequence[str]) -> list[float]:
        return [self.score(query, i) for i in range(self.n)]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _parse_time(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value > 0 else None
    stamp = str(value or "")[:19]
    try:
        return float(calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        try:
            return float(calendar.timegm(time.strptime(stamp[:10], "%Y-%m-%d")))
        except ValueError:
            return None


# ------------------------------------------------------------------------------- data model
@dataclass(slots=True)
class MemoryItem:
    id: str
    layer: str                       # facts | events | style
    text: str
    key: str = ""
    category: str = ""
    value: str = ""
    at: float | None = None          # when last seen / said (UTC epoch)
    importance: float = 0.3
    confirmed: bool = False
    sensitive: bool = False
    source_kind: str = "legacy"
    source_date: str = ""
    confidence: float = 0.0
    evidence_kind: str = ""
    version: int = 1
    corrections: int = 0
    freshness: str = "unknown"
    tokens: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Hit:
    item: MemoryItem
    score: float
    relevance: float
    retention: float


@dataclass(frozen=True, slots=True)
class Contradiction:
    first: MemoryItem
    second: MemoryItem
    reason: str


def _fact_item(row: dict[str, Any]) -> MemoryItem | None:
    value = row.get("value")
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    value = " ".join(str(value if value is not None else "").split())[:ITEM_CHARS]
    if not value:
        return None
    env = row.get("passport") if isinstance(row.get("passport"), dict) else {}
    source = env.get("source") or {}
    fresh = env.get("freshness") or {}
    history = env.get("history") or []
    evidence = str(row.get("evidence_kind") or env.get("evidence_kind") or "")
    confidence = float(row.get("confidence", env.get("confidence", 0.0)) or 0.0)
    category, key = str(row.get("category", "")), str(row.get("key", ""))
    corrections = max(0, len(history) - 1)
    confirmed = evidence.lower() == "confirmed"
    evidence_weight = 1.0 if confirmed else 0.7 if evidence.lower() == "explicit" else 0.4
    category_weight = 1.0 if any(c in category.lower() for c in _IDENTITY_CATEGORIES) else 0.5
    importance = min(1.0, 0.5 * confidence + 0.3 * evidence_weight + 0.2 * category_weight
                     + min(corrections, 3) * 0.05)
    at = _parse_time(fresh.get("last_seen")) or _parse_time(fresh.get("observed_at")) \
        or _parse_time(source.get("date")) or _parse_time(row.get("last_seen")) \
        or _parse_time(row.get("observed_at")) or _parse_time(row.get("ingested_at"))
    text = f"{key.replace('_', ' ')}: {value}" if key else value
    return MemoryItem(
        id=str(row.get("id", "")), layer="facts", text=text, key=key, category=category, value=value, at=at,
        importance=importance, confirmed=confirmed, sensitive=str(row.get("sensitivity", "")) == "sensitive"
        or env.get("scope") == "own_chat_local_only", source_kind=str(source.get("kind") or "legacy"),
        source_date=str(source.get("date") or ""), confidence=confidence, evidence_kind=evidence,
        version=int(env.get("version", 1) or 1), corrections=corrections,
        freshness=passport.freshness_status(row),
        tokens=tokenize(f"{key.replace('_', ' ')} {category} {value}"))


def _event_items(lines: Iterable[str]) -> list[MemoryItem]:
    items: list[MemoryItem] = []
    for n, line in enumerate(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or str(row.get("role", "user")) not in {"user", "participant", ""}:
            continue
        text = " ".join(str(row.get("text", "")).split())[:ITEM_CHARS]
        if len(text) < 4:
            continue
        stamp = row.get("at") or row.get("ts") or row.get("observed_at") or row.get("created_at")
        items.append(MemoryItem(
            id=str(row.get("id") or f"event:{n}"), layer="events", text=text, at=_parse_time(stamp),
            importance=0.2, source_kind="event", source_date=str(stamp or "")[:19],
            freshness="unknown", tokens=tokenize(text)))
    return items


def _tail(path, count: int) -> list[str]:
    try:
        return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()][-count:]
    except OSError:
        return []


def _stat(path) -> tuple[int, int]:
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return (0, 0)


# ------------------------------------------------------------------------------- contradictions
def _polarity(text: str) -> bool:
    return any(w in _NEGATIONS for w in _WORD.findall(normalise(text)))


def detect_contradictions(items: Iterable[MemoryItem]) -> list[Contradiction]:
    """Pairs of facts that cannot both be true: same single-valued key with different values, or the same
    statement with opposite polarity («люблю кофе» vs «не люблю кофе»). Never resolves them."""
    facts = [i for i in items if i.layer == "facts"]
    found: list[Contradiction] = []
    seen: set[tuple[str, str]] = set()
    for a_index, a in enumerate(facts):
        for b in facts[a_index + 1:]:
            pair = (a.id, b.id)
            if pair in seen:
                continue
            reason = ""
            same_key = a.key and a.key == b.key and a.category == b.category
            if same_key and any(h in (a.key + a.category).lower() for h in _SINGLE_VALUED) \
                    and normalise(a.value) != normalise(b.value) and not _polarity(a.value + b.value):
                reason = "разные значения одного и того же"
            else:
                ta, tb = set(tokenize(a.value)), set(tokenize(b.value))
                union = ta | tb
                if union and len(ta & tb) / len(union) >= 0.6 and _polarity(a.value) != _polarity(b.value):
                    reason = "утверждение и его отрицание"
            if reason:
                seen.add(pair)
                found.append(Contradiction(a, b, reason))
    return found


# ------------------------------------------------------------------------------- the engine
@dataclass(slots=True)
class _Index:
    signature: tuple
    items: list[MemoryItem]
    bm25: BM25


class MemoryPalace:
    """Retrieval engine bound to a vault; every public method takes the ``person_key`` explicitly."""

    def __init__(self, vault: Any, *, embed: Embed | None = None, clock: Callable[[], float] = time.time):
        self.vault = vault
        self.embed = embed
        self.clock = clock
        self._indexes: OrderedDict[str, _Index] = OrderedDict()
        self._vectors: dict[str, OrderedDict[str, list[float]]] = {}
        self._last_shown: dict[str, tuple[str, ...]] = {}
        self.counters = {"retrievals": 0, "embed_used": 0, "embed_failed": 0, "views": 0, "why": 0,
                         "denied": 0, "contradictions": 0}

    def has_shown(self, person_key: str) -> bool:
        """True while this participant has memory items on screen that a bare «почему?» can refer to."""
        return bool(self._last_shown.get(person_key))

    # -- consent gates: read LIVE on every call ------------------------------------------------
    def consent(self, person_key: str):
        return self.vault.consent(person_key)

    def _allowed_layers(self, consent: Any) -> tuple[str, ...]:
        if not consent.memory_enabled:
            return ()
        layers = ["facts", "style"]
        if consent.raw_history_enabled:
            layers.append("events")
        return tuple(layers)

    # -- index ---------------------------------------------------------------------------------
    def _load(self, person_key: str, layers: tuple[str, ...]) -> _Index:
        base = self.vault.person_dir(person_key)
        signature = (layers, _stat(base / "facts.jsonl"), _stat(base / "raw" / "events.jsonl"),
                     _stat(base / passport.STYLE_FILE))
        cached = self._indexes.get(person_key)
        if cached is not None and cached.signature == signature:
            self._indexes.move_to_end(person_key)
            return cached
        items: list[MemoryItem] = []
        if "facts" in layers:
            for row in passport.facts_layer(self.vault, person_key)[-MAX_FACTS:]:
                item = _fact_item(row)
                if item is not None and item.freshness != "expired":
                    items.append(item)
        if "events" in layers:
            items.extend(_event_items(_tail(base / "raw" / "events.jsonl", MAX_EVENTS)))
        if "style" in layers:
            style = passport.read_style(self.vault, person_key)
            for n, paragraph in enumerate((style or {}).get("paragraphs") or []):
                text = " ".join(str(paragraph).split())[:ITEM_CHARS]
                items.append(MemoryItem(
                    id=f"style:{n}", layer="style", text=text, at=_parse_time(style.get("updated_at")),
                    importance=0.5, source_kind="style", source_date=str(style.get("updated_at", ""))[:19],
                    freshness="unknown", tokens=tokenize(text)))
        index = _Index(signature, items, BM25([i.tokens for i in items]))
        self._indexes[person_key] = index
        self._indexes.move_to_end(person_key)
        while len(self._indexes) > MAX_CACHED_PEOPLE:
            evicted, _ = self._indexes.popitem(last=False)
            self._vectors.pop(evicted, None)
        # vectors of anything no longer stored are dropped: forgetting removes derived data too
        live = {i.text for i in items}
        vectors = self._vectors.get(person_key)
        if vectors:
            for text in [t for t in vectors if t not in live]:
                del vectors[text]
        return index

    def items(self, person_key: str) -> list[MemoryItem]:
        layers = self._allowed_layers(self.consent(person_key))
        return list(self._load(person_key, layers).items) if layers else []

    # -- scoring -------------------------------------------------------------------------------
    def retention(self, item: MemoryItem, now: float | None = None) -> float:
        """Forgetting curve ``exp(-age / stability)``; confirmed and important memories fade slower."""
        now = self.clock() if now is None else now
        if item.at is None:
            return 0.5
        base = 14.0 if item.layer == "events" else 45.0
        stability = base * (1.0 + 3.0 * item.importance) * (2.0 if item.confirmed else 1.0)
        age_days = max(0.0, (now - item.at) / 86400.0)
        return math.exp(-age_days / stability)

    async def _embed_scores(self, person_key: str, query: str, items: list[MemoryItem]) -> list[float] | None:
        if self.embed is None or not items:
            return None
        cache = self._vectors.setdefault(person_key, OrderedDict())
        missing = [i.text for i in items if i.text not in cache]
        try:
            async def call(batch: list[str]):
                result = self.embed(batch)
                return await result if inspect.isawaitable(result) else result
            vectors = await asyncio.wait_for(call([query] + missing), timeout=EMBED_TIMEOUT_S)
            if len(vectors) != len(missing) + 1:
                raise ValueError("embedding count mismatch")
        except Exception:  # noqa: BLE001 - lexical retrieval is always the fallback
            self.counters["embed_failed"] += 1
            return None
        for text, vec in zip(missing, vectors[1:]):
            cache[text] = [float(x) for x in vec]
        while len(cache) > MAX_CACHED_VECTORS:
            cache.popitem(last=False)
        self.counters["embed_used"] += 1
        qvec = [float(x) for x in vectors[0]]
        return [max(0.0, cosine(qvec, cache.get(i.text, []))) for i in items]

    async def retrieve(self, person_key: str, query: str, *, k: int = TOP_K, require_match: bool = True,
                       include_sensitive: bool = False, now: float | None = None) -> list[Hit]:
        consent = self.consent(person_key)
        layers = self._allowed_layers(consent)
        if not layers:
            self.counters["denied"] += 1
            return []
        index = self._load(person_key, layers)
        allow_sensitive = include_sensitive or bool(getattr(consent, "sensitive_memory_enabled", False))
        pool = [(n, item) for n, item in enumerate(index.items) if allow_sensitive or not item.sensitive]
        query_tokens = tokenize(query)
        raw = index.bm25.scores(query_tokens) if query_tokens else [0.0] * index.bm25.n
        top = max((raw[n] for n, _ in pool), default=0.0)
        emb = await self._embed_scores(person_key, query, [item for _, item in pool]) if query_tokens else None
        now = self.clock() if now is None else now
        hits: list[Hit] = []
        for position, (n, item) in enumerate(pool):
            lexical = raw[n] / top if top > 0 else 0.0
            relevance = lexical if emb is None else 0.6 * lexical + 0.4 * emb[position]
            if require_match and relevance <= 0.0:
                continue
            if not require_match and not query_tokens:
                relevance = 1.0
            retention = self.retention(item, now)
            strength = 0.5 + 0.3 * retention + 0.2 * item.importance
            hits.append(Hit(item, relevance * strength, relevance, retention))
        hits.sort(key=lambda h: (-h.score, h.item.id))
        self.counters["retrievals"] += 1
        return hits[: max(1, min(int(k), MAX_LISTED))]

    def contradictions(self, hits: Iterable[Hit]) -> list[Contradiction]:
        found = detect_contradictions(h.item for h in hits)
        self.counters["contradictions"] += len(found)
        return found

    # -- prompt notes --------------------------------------------------------------------------
    async def notes_for(self, person_key: str, query: str, *, remote_route: bool = False) -> tuple[str, ...]:
        consent = self.consent(person_key)
        if not consent.memory_enabled or not getattr(consent, "personalization_enabled", True):
            return ()
        if remote_route and not getattr(consent, "remote_personalization_enabled", False):
            return ()
        hits = await self.retrieve(person_key, query)
        if not hits:
            return ()
        notes: list[str] = []
        used = 0
        for hit in hits:
            text, _ = redact_secrets(hit.item.text)
            line = f"Память этого собеседника [{hit.item.layer}]: {text}"
            if hit.item.layer == "style":
                line += " (предположение, не факт)"
            if used + len(line) > NOTE_CHAR_BUDGET:
                break
            notes.append(line)
            used += len(line)
        for clash in self.contradictions(hits):
            line = (f"Память противоречива: «{clash.first.value[:80]}» и «{clash.second.value[:80]}» "
                    f"({clash.reason}); не выбирай сам, мягко уточни у собеседника.")
            if used + len(line) > NOTE_CHAR_BUDGET:
                break
            notes.append(line)
            used += len(line)
        self._last_shown[person_key] = tuple(h.item.id for h in hits)
        self.vault.audit(person_key, "read", actor="jeff", fact_ids=[h.item.id for h in hits],
                         categories=[h.item.category or h.item.layer for h in hits])
        return tuple(notes)

    # -- participant-facing answers ------------------------------------------------------------
    def _provenance(self, item: MemoryItem) -> str:
        source = _SOURCE_RU.get(item.source_kind, "источник")
        date = f" {item.source_date[:10]}" if item.source_date else ""
        parts = [f"{source}{date}"]
        if item.layer == "facts":
            parts.append(f"уверенность {item.confidence:.2f}")
            if item.freshness in _FRESH_RU:
                parts.append(_FRESH_RU[item.freshness])
            if item.confirmed:
                parts.append("подтверждено тобой")
            if item.corrections:
                parts.append(f"исправлений: {item.corrections}")
        if item.sensitive:
            parts.append("чувствительное, только локально")
        return ", ".join(parts)

    async def what_do_i_know(self, person_key: str, text: str, *, surface: str = "telegram") -> str:
        consent = self.consent(person_key)
        if not consent.memory_enabled:
            return ("Память выключена, поэтому я ничего не использую и не показываю. "
                    "Включить — /resume_memory; что разрешено — /privacy.")
        self.counters["views"] += 1
        topic = " ".join(w for w in _WORD.findall(normalise(_KNOW_RE.sub(" ", text)))
                         if w not in _TRIGGER_WORDS)
        hits = await self.retrieve(person_key, topic, k=MAX_LISTED, require_match=bool(tokenize(topic)),
                                   include_sensitive=True)
        if not hits:
            return ("Про это я пока ничего не помню." if tokenize(topic)
                    else "Паспорт пока пуст: я ничего о тебе не записал.")
        lines = [f"Вот что я помню{' по теме' if tokenize(topic) else ' о тебе'} ({len(hits)}):"]
        used = len(lines[0])
        for hit in hits:
            value, _ = redact_secrets(hit.item.text)
            line = f"• {value} — {self._provenance(hit.item)}"
            if used + len(line) > VIEW_CHAR_BUDGET:
                lines.append("…есть и другое, спроси точнее.")
                break
            lines.append(line)
            used += len(line)
        for clash in self.contradictions(hits):
            lines.append(f"⚠ Не сходится: «{clash.first.value[:80]}» и «{clash.second.value[:80]}» — "
                         f"{clash.reason}. Скажи, что верно (/correct было => стало).")
        if not consent.personalization_enabled:
            lines.append("Персонализация выключена: в ответах я эти факты не использую.")
        lines.append("Исправить — /correct было => стало; забыть — /forget что; пауза — /pause_memory.")
        self._last_shown[person_key] = tuple(h.item.id for h in hits)
        self.vault.audit(person_key, "view", actor="participant", surface=surface[:20],
                         fact_ids=[h.item.id for h in hits], categories=[h.item.category or h.item.layer for h in hits])
        return "\n".join(lines)

    async def why_do_i_think(self, person_key: str, text: str, *, surface: str = "telegram") -> str:
        consent = self.consent(person_key)
        if not consent.memory_enabled:
            return "Память выключена: ничего не храню и не использую, объяснять нечего."
        self.counters["why"] += 1
        clause = " ".join(w for w in _WORD.findall(normalise(_WHY_RE.sub(" ", text)))
                          if w not in _TRIGGER_WORDS)
        hits: list[Hit] = []
        if tokenize(clause):
            hits = (await self.retrieve(person_key, clause, k=2, include_sensitive=True))
        if not hits:
            shown = set(self._last_shown.get(person_key, ()))
            pool = [i for i in self.items(person_key) if i.id in shown]   # re-resolved: forgotten ids vanish
            hits = [Hit(i, 1.0, 1.0, self.retention(i)) for i in pool[:2]]
        if not hits:
            return ("Не нахожу в своей памяти, на чём это основано. Возможно, я просто предположил по ходу "
                    "разговора — можно считать это неподтверждённым.")
        lines = []
        for hit in hits:
            item = hit.item
            value, _ = redact_secrets(item.text)
            lines.append(f"Я помню: «{value}». Основание: {self._provenance(item)}.")
            if item.layer == "style" or item.source_kind in {"master_parser"}:
                lines.append("Это моё предположение по переписке, а не факт — поправь, если неверно.")
        lines.append("Если это неправда — /correct было => стало, или /forget что.")
        self.vault.audit(person_key, "view", actor="participant", surface=surface[:20],
                         fact_ids=[h.item.id for h in hits], categories=["why"])
        return "\n".join(lines)


class MemoryPalaceModule(BaseModule):
    name = "memory_palace"
    version = "1"
    order = 40

    def __init__(self, palace: MemoryPalace) -> None:
        self.palace = palace

    async def pre_route(self, ctx: TurnContext) -> Advice | None:
        text = ctx.text.strip()
        if not text or text.startswith("/") or len(text) > 400:
            return None
        if _WHY_RE.search(text) and (_SELF_REF.search(text) or self.palace.has_shown(ctx.person_key)):
            return Advice(reply=await self.palace.why_do_i_think(ctx.person_key, text, surface=ctx.surface),
                          tags=("memory_why",))
        if _KNOW_RE.search(text):
            return Advice(reply=await self.palace.what_do_i_know(ctx.person_key, text, surface=ctx.surface),
                          tags=("memory_view",))
        return None

    async def augment(self, ctx: TurnContext) -> Advice | None:
        notes = await self.palace.notes_for(ctx.person_key, ctx.text,
                                            remote_route=bool(ctx.extra.get("route_remote", False)))
        return Advice(notes=notes, tags=("memory",)) if notes else None

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "embeddings": self.palace.embed is not None,
                "counters": dict(self.palace.counters), "cached_people": len(self.palace._indexes)}


def create(runtime: Any, *, embed: Embed | None = None) -> MemoryPalaceModule:
    return MemoryPalaceModule(MemoryPalace(runtime.vault, embed=embed or getattr(runtime, "j2_embed", None)))
