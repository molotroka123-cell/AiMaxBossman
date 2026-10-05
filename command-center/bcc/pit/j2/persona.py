"""Jeff 2.0 module ``persona`` (order 50): Persona and Style Engine.

Adapts HOW Jeff talks to each participant, never WHAT he may do or know:

* **inputs** (all per participant): the passport style layer written by Master Parser 2.0 (an inference, read through
  ``bcc.pit.passport.read_style`` and only with memory consent), a light in-memory read of the participant's current
  manner (ты/вы, message length, humour, emoji), the owner's configured behaviour scales, and the owner overlay
  (``bcc.pit.jeff_settings``);
* **traits**: register (ты/вы), length, humour, directness, warmth, depth, emoji. The style layer is turned into
  bounded scale deltas (at most +-2 from the owner's base value) through a fixed keyword vocabulary; its text is never
  copied into a prompt, so an inference cannot smuggle instructions;
* **owner overlay wins**: any scale the overlay sets (defaults or per participant) is locked: no persona note is
  produced for it and no delta is applied. The overlay's own text is already in the system prompt and is not repeated;
* **prompt assembly** within a token budget (about 110 tokens): parts are ranked, the lowest ranks are dropped first,
  and every note is checked by a validator that refuses permission, access, command or fact language;
* **safe A/B**: a few phrasings of the SAME style content (control, compact, explicit) are assigned by deterministic
  per-participant bucketing (HMAC of the participant key and the experiment id) and every turn is logged to
  ``<data_dir>/pit-v1.7/j2/persona-ab.jsonl`` with labels and counters only (no text). A variant can change wording and
  length, never permissions, facts, memory or tools.

The module only adds notes; ``post_reply`` records the reply length for the A/B log and never edits the reply.

Status keys: ``turns``, ``personalised``, ``skipped_no_consent``, ``variants`` (turns per variant), ``experiment``,
``ab_enabled``, ``locked_dimensions`` (last seen count), ``tracked_participants``, ``log_events``.

Privacy: no personalisation without ``ctx.personalization_enabled``; the saved style layer is read only with
``ctx.memory_enabled``; the persisted state (``<person_dir>/j2/persona.json``: numeric signals only) exists only with
memory consent and is removed when it is off; the log has no participant text.
``BOSSMAN_JEFF_J2_PERSONA=off`` disables the module, ``BOSSMAN_JEFF_J2_PERSONA_AB=off`` forces the control variant.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import tempfile
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .contract import Advice, BaseModule, TurnContext

MODULE_ENV = "BOSSMAN_JEFF_J2_PERSONA"
AB_ENV = "BOSSMAN_JEFF_J2_PERSONA_AB"
SCHEMA = "jeff.j2.persona/1"
BUDGET_TOKENS = 110
MAX_TRACKED = 500
MAX_DELTA = 2
SCALE_MIN, SCALE_MAX = 1, 10
EMA_ALPHA = 0.3
MIN_LIVE_TURNS = 2
PERSIST_EVERY = 4
LOG_MAX_BYTES = 1_000_000
DIMENSIONS = ("brevity", "depth", "humor", "directness", "warmth")


def est_tokens(text: str) -> int:
    """Cheap token estimate for Russian-heavy text (about three characters per token)."""
    return math.ceil(len(text) / 3.0)


# -- style layer -> deltas -----------------------------------------------------------------------------
_NEGATION = re.compile(r"(?:не|без|избегает|нет|ни)\s+(?:\w+\s+){0,3}$")
LEXICON: dict[str, re.Pattern[str]] = {name: re.compile(pattern, re.I) for name, pattern in {
    "informal": r"неформальн\w*|на\s+[«\"]?ты\b|разговорн\w*|сленг\w*|по-дружески|дружеск\w*|простым\s+языком|запросто",
    "formal": r"(?<!не)формальн\w*|деловой|деловом|на\s+[«\"]?вы\b|вежлив\w*|официальн\w*|сдержанн\w*",
    "brief": r"коротк\w*|лаконичн\w*|кратк\w*|немногослов\w*|без\s+воды|сжат\w*|по\s+делу",
    "detailed": r"подробн\w*|развёрнут\w*|развернут\w*|обстоятельн\w*|детал\w+|глубок\w*",
    "humour": r"юмор\w*|шут\w+|ирони\w*|сарказм\w*|смешн\w*|шутлив\w*",
    "direct": r"прям\w*|конкретн\w*|без\s+обиняков|резк\w*|чётк\w*|четк\w*|решительн\w*",
    "soft": r"мягк\w*|деликатн\w*|осторожн\w*|тактичн\w*|бережн\w*",
    "warm": r"тепл\w*|эмпати\w*|поддержк\w*|чувствительн\w*|эмоциональн\w*",
    "emoji": r"эмодзи|смайл\w*|стикер\w*",
}.items()}
OPPOSITE = {"humour": "serious", "brief": "detailed", "detailed": "brief", "direct": "soft", "soft": "direct",
            "warm": "cool", "informal": "formal", "formal": "informal", "emoji": "no_emoji"}


@dataclass(slots=True)
class StyleSignals:
    """What the style layer says, as votes in [-1, 1] per trait plus the register."""
    register: str | None = None          # "ты" | "вы" | None
    votes: dict[str, int] = field(default_factory=dict)
    emoji: bool | None = None


def read_style_signals(paragraphs: list[str] | tuple[str, ...]) -> StyleSignals:
    """Fixed vocabulary only: the paragraphs are scanned, never quoted."""
    counts: Counter[str] = Counter()
    for paragraph in paragraphs:
        text = str(paragraph or "").casefold().replace("ё", "е")
        for trait, pattern in LEXICON.items():
            for match in pattern.finditer(text):
                negated = bool(_NEGATION.search(text[max(0, match.start() - 24):match.start()]))
                counts[OPPOSITE[trait] if negated else trait] += 1
    signals = StyleSignals()
    if counts["informal"] > counts["formal"]:
        signals.register = "ты"
    elif counts["formal"] > counts["informal"]:
        signals.register = "вы"
    if counts["emoji"] > 0:
        signals.emoji = True
    elif counts["no_emoji"] > 0:
        signals.emoji = False
    pairs = {"brevity": ("brief", "detailed"), "depth": ("detailed", "brief"), "humor": ("humour", "serious"),
             "directness": ("direct", "soft"), "warmth": ("warm", "cool")}
    for dim, (up, down) in pairs.items():
        vote = (1 if counts[up] > counts[down] else -1 if counts[down] > counts[up] else 0)
        if vote:
            signals.votes[dim] = vote
    return signals


# -- live signals --------------------------------------------------------------------------------------
_FORMAL_LIVE = re.compile(r"\b(?:вы|вас|вам|вами|ваш\w*|здравствуйте|уважаем\w+|подскажите|скажите|помогите|объясните|"
                          r"пожалуйста|благодарю)\b", re.I)
_INFORMAL_LIVE = re.compile(r"\b(?:ты|тебя|тебе|тобой|твой\w*|привет|хах\w*|ахах\w*|лол|кек|че|чё|норм|збс|блин|короче|типа|"
                            r"слушай|подскажи|скажи|помоги|объясни|спс|плиз)\b", re.I)
_HUMOUR_LIVE = re.compile(r"(?:\bх[аеи]х\w*|\bахах\w*|\bлол\b|\bкек\b|\bржу\b|😂|🤣|😄|😆|\)\)+)", re.I)
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
_WORDS = re.compile(r"[a-zа-яё0-9]+", re.I)


@dataclass(slots=True)
class LiveState:
    turns: int = 0
    formal: float = 0.0
    informal: float = 0.0
    words: float = 0.0
    humour: float = 0.0
    emoji: float = 0.0
    dirty: int = 0

    def observe(self, text: str) -> None:
        value = str(text or "")
        n_words = len(_WORDS.findall(value))
        sample = {
            "formal": min(2.0, float(len(_FORMAL_LIVE.findall(value)))),
            "informal": min(2.0, float(len(_INFORMAL_LIVE.findall(value)))),
            "words": float(min(n_words, 120)),
            "humour": 1.0 if _HUMOUR_LIVE.search(value) else 0.0,
            "emoji": 1.0 if _EMOJI.search(value) else 0.0,
        }
        for name, x in sample.items():
            current = getattr(self, name)
            setattr(self, name, x if self.turns == 0 else (1 - EMA_ALPHA) * current + EMA_ALPHA * x)
        self.turns += 1
        self.dirty += 1

    def register(self) -> str | None:
        if self.turns < MIN_LIVE_TURNS or abs(self.formal - self.informal) < 0.3:
            return None
        return "вы" if self.formal > self.informal else "ты"

    def votes(self) -> dict[str, int]:
        out: dict[str, int] = {}
        if self.turns >= MIN_LIVE_TURNS:
            if self.words <= 5:
                out["brevity"] = 1
            elif self.words >= 40:
                out["brevity"], out["depth"] = -1, 1
            if self.humour >= 0.4:
                out["humor"] = 1
        return out

    def as_json(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "turns": self.turns, "formal": round(self.formal, 3), "informal": round(self.informal, 3),
                "words": round(self.words, 2), "humour": round(self.humour, 3), "emoji": round(self.emoji, 3)}

    @classmethod
    def from_json(cls, data: Any) -> "LiveState | None":
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            return None
        try:
            return cls(turns=int(data.get("turns", 0)), formal=float(data.get("formal", 0)),
                       informal=float(data.get("informal", 0)), words=float(data.get("words", 0)),
                       humour=float(data.get("humour", 0)), emoji=float(data.get("emoji", 0)))
        except (TypeError, ValueError):
            return None


# -- traits --------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Traits:
    register: str | None
    scales: dict[str, int]              # final value per dimension
    deltas: dict[str, int]              # applied deltas (0 for locked or unchanged)
    locked: tuple[str, ...]
    emoji: bool | None = None

    def labels(self) -> dict[str, Any]:
        return {"register": self.register, "deltas": {k: v for k, v in self.deltas.items() if v},
                "locked": list(self.locked), "emoji": self.emoji}


def derive_traits(signals: StyleSignals, live: LiveState | None, base: dict[str, int],
                  locked_scales: dict[str, int]) -> Traits:
    """Bounded deltas around the owner's base scales. Locked (overlay) dimensions keep the owner's value untouched."""
    votes: dict[str, int] = {}
    for source in (signals.votes, live.votes() if live else {}):
        for dim, vote in source.items():
            votes[dim] = votes.get(dim, 0) + vote
    scales: dict[str, int] = {}
    deltas: dict[str, int] = {}
    locked: list[str] = []
    for dim in DIMENSIONS:
        start = int(base.get(dim, 5))
        if dim in locked_scales:
            scales[dim], deltas[dim] = int(locked_scales[dim]), 0
            locked.append(dim)
            continue
        delta = max(-MAX_DELTA, min(MAX_DELTA, votes.get(dim, 0) * (2 if dim in signals.votes else 1)))
        final = max(SCALE_MIN, min(SCALE_MAX, start + delta))
        scales[dim], deltas[dim] = final, final - start
    register = signals.register or (live.register() if live else None)
    emoji = signals.emoji if signals.emoji is not None else (True if live and live.turns >= MIN_LIVE_TURNS
                                                             and live.emoji >= 0.5 else None)
    return Traits(register, scales, deltas, tuple(locked), emoji)


# -- phrasing variants ---------------------------------------------------------------------------------
# Every variant expresses the same components; only wording and length differ.
COMPONENT_ORDER = ("register", "brevity", "directness", "humor", "warmth", "depth", "emoji")
PHRASES: dict[str, dict[str, dict[str, str]]] = {
    "control": {
        "register": {"ты": "обращайся на «ты»", "вы": "обращайся на «вы»"},
        "brevity": {"up": "отвечай короче", "down": "можно чуть подробнее"},
        "directness": {"up": "говори прямо", "down": "говори мягко и бережно"},
        "humor": {"up": "уместный лёгкий юмор допустим", "down": "без шуток"},
        "warmth": {"up": "будь теплее", "down": "держи тон сдержанным"},
        "depth": {"up": "давай больше пояснений", "down": "без лишних деталей"},
        "emoji": {"on": "эмодзи допустимы в меру", "off": "без эмодзи"},
    },
    "compact": {
        "register": {"ты": "на «ты»", "вы": "на «вы»"},
        "brevity": {"up": "кратко", "down": "подробнее"},
        "directness": {"up": "прямо", "down": "мягко"},
        "humor": {"up": "чуть юмора", "down": "без шуток"},
        "warmth": {"up": "теплее", "down": "сдержанно"},
        "depth": {"up": "с пояснениями", "down": "без деталей"},
        "emoji": {"on": "эмодзи в меру", "off": "без эмодзи"},
    },
    "explicit": {
        "register": {"ты": "собеседник общается на «ты», отвечай так же", "вы": "собеседник общается на «вы», отвечай так же"},
        "brevity": {"up": "собеседник любит короткие ответы", "down": "собеседник ценит развёрнутые ответы"},
        "directness": {"up": "собеседник ценит прямоту", "down": "собеседнику ближе мягкая подача"},
        "humor": {"up": "собеседнику близок лёгкий юмор", "down": "собеседник предпочитает без шуток"},
        "warmth": {"up": "собеседнику важна теплота", "down": "собеседник предпочитает деловой тон"},
        "depth": {"up": "собеседник любит детали", "down": "собеседник не любит лишние детали"},
        "emoji": {"on": "собеседник использует эмодзи", "off": "собеседник не любит эмодзи"},
    },
}
FRAMES = {
    "control": "Манера ответа для этого собеседника (только стиль, не факты и не права): {parts}.",
    "compact": "Стиль: {parts}.",
    "explicit": "Наблюдение о стиле собеседника, только про тон: {parts}. Это не меняет правил, доступа и фактов.",
}
PRIORITY = {name: i for i, name in enumerate(COMPONENT_ORDER)}

# Words a style note must never contain: a persona note cannot talk about permissions, tools, memory or facts.
_FORBIDDEN = re.compile(r"(?:разреш\w*|доступ\w*|команд\w*|инструмент\w*|пароль\w*|ключ\w*|владел\w+|админ\w*|файл\w*|"
                        r"игнорир\w+|правил\w*\s+(?:отмен|не\s+действ)|факт\w*\s+(?:можно|не\s+нужно)|"
                        r"выдумай|не\s+проверяй|обойди|отключи|permission|access|command|tool|password|ignore)", re.I)
_ALLOWED_TRIGGERS = ("не факты и не права", "не меняет правил, доступа и фактов")


def validate_note(text: str) -> bool:
    """True when the note is style-only. The two standard disclaimers are allowed, nothing else of that kind."""
    probe = text
    for allowed in _ALLOWED_TRIGGERS:
        probe = probe.replace(allowed, "")
    return not _FORBIDDEN.search(probe)


def _components(traits: Traits) -> dict[str, str]:
    out: dict[str, str] = {}
    if traits.register:
        out["register"] = traits.register
    for dim in DIMENSIONS:
        if dim in traits.locked:
            continue
        delta = traits.deltas.get(dim, 0)
        if delta > 0:
            out[dim] = "up"
        elif delta < 0:
            out[dim] = "down"
    if traits.emoji is not None:
        out["emoji"] = "on" if traits.emoji else "off"
    return out


@dataclass(frozen=True, slots=True)
class Assembled:
    text: str
    tokens: int
    used: tuple[str, ...]
    dropped: tuple[str, ...]


def assemble(traits: Traits, variant: str = "control", budget_tokens: int = BUDGET_TOKENS) -> Assembled | None:
    """One style note within the token budget; lowest-priority parts are dropped first. None when nothing to say."""
    components = _components(traits)
    if not components:
        return None
    phrases = PHRASES[variant]
    order = sorted(components, key=lambda name: PRIORITY[name])
    chosen = list(order)
    dropped: list[str] = []

    def render(names: list[str]) -> str:
        parts = [phrases[name][components[name]] for name in names]
        return FRAMES[variant].format(parts="; ".join(parts))

    text = render(chosen)
    while est_tokens(text) > budget_tokens and len(chosen) > 1:
        dropped.append(chosen.pop())
        text = render(chosen)
    if est_tokens(text) > budget_tokens or not validate_note(text):
        return None
    return Assembled(text, est_tokens(text), tuple(chosen), tuple(dropped))


# -- A/B -----------------------------------------------------------------------------------------------
DEFAULT_EXPERIMENT = "style_phrasing_v1"
DEFAULT_ALLOCATION = (("control", 50), ("compact", 25), ("explicit", 25))


def bucket(person_key: str, experiment: str, salt: bytes = b"jeff-j2-persona") -> int:
    """Stable 0..99 bucket per participant and experiment (HMAC, so keys cannot be recovered from the log)."""
    digest = hmac.new(salt, f"{experiment}:{person_key}".encode("utf-8"), hashlib.sha256).digest()
    return int.from_bytes(digest[:4], "big") % 100


def pick_variant(person_key: str, experiment: str = DEFAULT_EXPERIMENT,
                 allocation: tuple[tuple[str, int], ...] = DEFAULT_ALLOCATION, salt: bytes = b"jeff-j2-persona") -> str:
    total = sum(weight for _, weight in allocation)
    point = bucket(person_key, experiment, salt) * total // 100
    running = 0
    for name, weight in allocation:
        running += weight
        if point < running:
            return name
    return allocation[0][0]


# -- module --------------------------------------------------------------------------------------------
StyleReader = Callable[[str], "list[str] | tuple[str, ...] | None"]
OverlayReader = Callable[[str], Any]


class PersonaModule(BaseModule):
    name = "persona"
    version = "1"
    order = 50

    def __init__(self, *, style_reader: StyleReader | None = None, overlay_reader: OverlayReader | None = None,
                 base_scales: Callable[[], dict[str, int]] | dict[str, int] | None = None, vault: Any | None = None,
                 log_path: Path | str | None = None, salt: bytes = b"jeff-j2-persona",
                 experiment: str = DEFAULT_EXPERIMENT, allocation: tuple[tuple[str, int], ...] = DEFAULT_ALLOCATION,
                 budget_tokens: int = BUDGET_TOKENS, clock: Callable[[], float] = time.time,
                 max_tracked: int = MAX_TRACKED) -> None:
        self._style_reader, self._overlay_reader = style_reader, overlay_reader
        self._base_scales, self._vault = base_scales, vault
        self._log_path = Path(log_path) if log_path else None
        self._salt, self._experiment, self._allocation = salt, experiment, allocation
        self._budget, self._clock, self._max_tracked = budget_tokens, clock, max_tracked
        self._live: OrderedDict[str, LiveState] = OrderedDict()
        self._pending: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
        self._counts: Counter[str] = Counter()
        self._variants: Counter[str] = Counter()
        self._last_locked = 0
        self._recent: list[dict[str, Any]] = []

    # -- switches ------------------------------------------------------------------------------------
    @staticmethod
    def _flag(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in {"off", "0", "false", "no"}

    def ab_enabled(self) -> bool:
        return not self._flag(AB_ENV) and len(self._allocation) > 1

    def variant_for(self, person_key: str) -> str:
        if not self.ab_enabled():
            return "control"
        return pick_variant(person_key, self._experiment, self._allocation, self._salt)

    # -- inputs --------------------------------------------------------------------------------------
    def _scales(self) -> dict[str, int]:
        raw = self._base_scales() if callable(self._base_scales) else self._base_scales
        base = {d: 5 for d in DIMENSIONS}
        if isinstance(raw, dict):
            base.update({k: int(v) for k, v in raw.items() if k in DIMENSIONS and isinstance(v, (int, float))})
        return base

    def _locked(self, person_key: str) -> dict[str, int]:
        if self._overlay_reader is None:
            return {}
        try:
            style = self._overlay_reader(person_key)
            scales = getattr(style, "scales", None) or {}
            return {k: int(v) for k, v in dict(scales).items() if k in DIMENSIONS}
        except Exception:  # noqa: BLE001 - the overlay can never break a reply
            return {}

    def _person_file(self, person_key: str) -> Path | None:
        if self._vault is None:
            return None
        try:
            return Path(self._vault.person_dir(person_key)) / "j2" / "persona.json"
        except (ValueError, OSError, AttributeError):
            return None

    def _state(self, ctx: TurnContext) -> LiveState:
        state = self._live.get(ctx.person_key)
        if state is None:
            state = None
            if ctx.memory_enabled:
                path = self._person_file(ctx.person_key)
                if path is not None:
                    try:
                        state = LiveState.from_json(json.loads(path.read_text(encoding="utf-8")))
                    except (OSError, ValueError):
                        state = None
            state = state or LiveState()
            self._live[ctx.person_key] = state
            while len(self._live) > self._max_tracked:
                self._live.popitem(last=False)
        else:
            self._live.move_to_end(ctx.person_key)
        return state

    def _persist(self, ctx: TurnContext, state: LiveState) -> None:
        path = self._person_file(ctx.person_key)
        if path is None:
            return
        if not ctx.memory_enabled:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        if state.dirty < PERSIST_EVERY and state.turns > 1:
            return
        state.dirty = 0
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".persona.", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(state.as_json(), handle, sort_keys=True)
                    handle.write("\n")
                os.replace(tmp, path)
            finally:
                Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass

    def _style_signals(self, ctx: TurnContext) -> StyleSignals:
        if not ctx.memory_enabled or self._style_reader is None:
            return StyleSignals()
        try:
            paragraphs = self._style_reader(ctx.person_key) or ()
        except Exception:  # noqa: BLE001
            return StyleSignals()
        return read_style_signals(list(paragraphs)[:4])

    # -- logging -------------------------------------------------------------------------------------
    def _who(self, person_key: str) -> str:
        return hmac.new(self._salt, b"who:" + person_key.encode("utf-8"), hashlib.sha256).hexdigest()[:12]

    def _log(self, event: dict[str, Any]) -> None:
        event = {"ts": round(self._clock(), 3), **event}
        self._recent.append(event)
        del self._recent[:-50]
        self._counts["log_events"] += 1
        if self._log_path is None:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            if self._log_path.exists() and self._log_path.stat().st_size > LOG_MAX_BYTES:
                os.replace(self._log_path, self._log_path.with_suffix(".jsonl.1"))
            with self._log_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            pass

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._recent[-max(0, limit):]

    # -- hooks ---------------------------------------------------------------------------------------
    async def augment(self, ctx: TurnContext) -> Advice | None:
        if self._flag(MODULE_ENV):
            return None
        self._counts["turns"] += 1
        if not ctx.personalization_enabled:
            self._counts["skipped_no_consent"] += 1
            return None
        state = self._state(ctx)
        state.observe(ctx.text)                                    # the current message counts toward the mirror
        self._persist(ctx, state)
        locked = self._locked(ctx.person_key)
        self._last_locked = len(locked)
        traits = derive_traits(self._style_signals(ctx), state, self._scales(), locked)
        variant = self.variant_for(ctx.person_key)
        note = assemble(traits, variant, self._budget)
        entry = {"kind": "turn", "who": self._who(ctx.person_key), "message": str(ctx.message_id)[:32],
                 "experiment": self._experiment if self.ab_enabled() else "off", "variant": variant,
                 "traits": traits.labels(), "note_chars": len(note.text) if note else 0,
                 "note_tokens": note.tokens if note else 0, "dropped": list(note.dropped) if note else []}
        self._log(entry)
        if note is None:
            return None
        self._counts["personalised"] += 1
        self._variants[variant] += 1
        self._pending[(ctx.person_key, str(ctx.message_id))] = {"variant": variant, "words": len(_WORDS.findall(ctx.text))}
        while len(self._pending) > 256:
            self._pending.popitem(last=False)
        return Advice(notes=(note.text,), tags=(f"persona:{variant}",))

    async def post_reply(self, ctx: TurnContext, reply: str) -> str | None:
        if self._flag(MODULE_ENV):
            return None
        info = self._pending.pop((ctx.person_key, str(ctx.message_id)), None)
        if info is not None:
            self._log({"kind": "outcome", "who": self._who(ctx.person_key), "message": str(ctx.message_id)[:32],
                       "variant": info["variant"], "reply_chars": len(reply or ""),
                       "reply_words": len(_WORDS.findall(reply or "")), "user_words": info["words"]})
        return None                                                # persona never edits a reply

    def status(self) -> dict[str, Any]:
        return {"turns": self._counts["turns"], "personalised": self._counts["personalised"],
                "skipped_no_consent": self._counts["skipped_no_consent"], "variants": dict(self._variants),
                "experiment": self._experiment, "ab_enabled": self.ab_enabled(), "locked_dimensions": self._last_locked,
                "tracked_participants": len(self._live), "log_events": self._counts["log_events"]}


# -- factory -------------------------------------------------------------------------------------------
def create(runtime: Any) -> PersonaModule:
    settings = getattr(runtime, "settings", None)
    vault = getattr(runtime, "vault", None)
    data_dir = getattr(settings, "data_dir", None) or getattr(vault, "data_dir", None)
    style_reader = overlay_reader = None
    if vault is not None:
        def style_reader(person_key: str):                         # noqa: F811 - closure over the vault
            from .. import passport
            style = passport.read_style(vault, person_key)
            return tuple(style["paragraphs"]) if style else None
    if data_dir:
        def overlay_reader(person_key: str):                       # noqa: F811
            from .. import jeff_settings
            return jeff_settings.style_for(data_dir, person_key)
    salt = getattr(vault, "identity_salt", None)
    log = Path(data_dir) / "pit-v1.7" / "j2" / "persona-ab.jsonl" if data_dir else None
    return PersonaModule(style_reader=style_reader, overlay_reader=overlay_reader, vault=vault, log_path=log,
                         base_scales=lambda: dict(getattr(settings, "behavior_scales", {}) or {}),
                         salt=bytes(salt) if isinstance(salt, (bytes, bytearray)) and len(salt) >= 16 else b"jeff-j2-persona")
