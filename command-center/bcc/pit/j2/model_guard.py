"""Jeff 2.0 module ``model_guard`` (order 20): Model Health Guard for the local model.

Seen live: after a long uptime Ollama started to answer "Или Или Или" and CJK noise until it was restarted. This module
notices that within one turn and keeps a local answer of that kind away from the participant:

* :func:`assess` classifies a piece of model text: empty, repetition loops, foreign-script noise (CJK, Hangul, Arabic,
  Thai, ...), replacement characters, symbol noise, leaked chat-template tokens;
* a canary probe sends a short prompt with a known answer ("столица Франции" -> "париж") through an injected chat
  callable and records the result;
* a small state machine (healthy / suspect / degraded / recovering) with hysteresis: garbage degrades at once, weak signs
  need two in ten minutes, and two good observations are needed to heal;
* a rate-limited recovery hook: the default action only UNLOADS the model (``keep_alive: 0`` through an injected
  callable); a restart action exists only when the owner injects it AND enables it, and this module never starts,
  stops or kills processes itself;
* ``post_reply`` replaces a garbage answer with a short honest apology, ``augment`` adds a routing-hint note while the
  model is not healthy, and :meth:`ModelGuardModule.routing_hint` tells the router to prefer another model.

It never blocks a turn and never stores model or participant text: only problem codes, timings and counters.

Status keys: ``available``, ``model``, ``state``, ``since``, ``last_canary`` (ok, reason, latency_ms, at, id),
``canaries_run``, ``canary_failures``, ``garbage_replies``, ``recoveries`` (attempted, suppressed, last_action,
last_at, last_result), ``needs_owner``, ``hint``.

Privacy: no participant data is read except the text of the reply being checked, which is inspected in memory and
dropped. Switch off with ``BOSSMAN_JEFF_J2_MODEL_GUARD=off``.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Awaitable, Callable

from .contract import Advice, BaseModule, TurnContext

MODULE_ENV = "BOSSMAN_JEFF_J2_MODEL_GUARD"

SUSPECT_WINDOW_S = 600.0
SUSPECTS_TO_DEGRADE = 2
GOOD_TO_HEAL = 2
CANARY_INTERVAL_S = 300.0
CANARY_DEGRADED_INTERVAL_S = 30.0
CANARY_TIMEOUT_S = 20.0
ACTION_TIMEOUT_S = 15.0
RECOVERY_MIN_INTERVAL_S = 120.0
RECOVERY_MAX_PER_HOUR = 3

APOLOGY = ("Кажется, я на секунду сбился и ответил бессвязно. Напишите, пожалуйста, ещё раз: уже разобрался.")
HINT_NOTE = ("Локальная модель сейчас работает нестабильно: пиши короткими простыми предложениями на одном языке, "
             "без длинных списков и повторов.")


class State(StrEnum):
    HEALTHY = "healthy"
    SUSPECT = "suspect"
    DEGRADED = "degraded"
    RECOVERING = "recovering"


# -- text assessment ---------------------------------------------------------------------------------
_FOREIGN_RANGES = (
    (0x2E80, 0x9FFF),      # CJK radicals, kana, unified ideographs
    (0xAC00, 0xD7AF),      # Hangul
    (0x0600, 0x06FF),      # Arabic
    (0x0E00, 0x0E7F),      # Thai
    (0x0900, 0x097F),      # Devanagari
    (0x0590, 0x05FF),      # Hebrew
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),      # full-width forms
)
_TEMPLATE = re.compile(r"<\|[^|>]{1,40}\|>|</?think>|\[/?INST\]|<<\s*/?SYS\s*>>", re.I)
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)      # digits are part of a word: "шаг1 шаг2" is not a loop
# Interjections a person may legitimately repeat ("Ха ха ха", "Нет, нет, нет!"): repetition alone is not garbage for them.
_REPEAT_OK = frozenset("ха хаха да нет ну ага ура бла ох ах эх ой ух ням мяу гав ку так вау ха-ха ой-ой".split())
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_RANK = {"ok": 0, "suspect": 1, "garbage": 2}
_CYR = re.compile(r"[а-яё]", re.I)
_LAT = re.compile(r"[a-z]", re.I)


@dataclass(frozen=True, slots=True)
class Assessment:
    problems: tuple[str, ...] = ()
    severity: str = "ok"                # ok | suspect | garbage

    @property
    def ok(self) -> bool:
        return self.severity == "ok"

    @property
    def garbage(self) -> bool:
        return self.severity == "garbage"


def _is_foreign(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _FOREIGN_RANGES)


def _repeating_ngram(words: list[str]) -> int:
    """Longest run of an n-gram (n = 1..6) repeated back-to-back; returns the repeat count of the worst loop."""
    best = 1
    total = len(words)
    for n in range(1, 7):
        if total < n * 3:
            break
        run, i = 1, 0
        while i + 2 * n <= total:
            if words[i:i + n] == words[i + n:i + 2 * n]:
                run += 1
                i += n
                best = max(best, run)
            else:
                run = 1
                i += 1
    return best


def assess(text: str, *, prompt: str = "", expect_cyrillic: bool | None = None) -> Assessment:
    """Judge one model answer. ``prompt`` is the user text: foreign script the user wrote is not noise."""
    value = unicodedata.normalize("NFKC", str(text or ""))
    stripped = value.strip()
    if not stripped:
        return Assessment(("empty",), "garbage")
    problems: list[str] = []
    severity = "ok"

    def flag(code: str, level: str) -> None:
        nonlocal severity
        problems.append(code)
        if _RANK[level] > _RANK[severity]:
            severity = level

    letters = [c for c in stripped if c.isalpha()]
    prompt_foreign = any(_is_foreign(c) for c in str(prompt or ""))
    if letters and not prompt_foreign:
        foreign = sum(1 for c in letters if _is_foreign(c))
        ratio = foreign / len(letters)
        if ratio >= 0.15 or foreign >= 6:
            flag("foreign_script", "garbage")
        elif foreign:
            flag("foreign_script_trace", "suspect")
    if "�" in stripped:
        flag("replacement_char", "garbage" if stripped.count("�") >= 3 else "suspect")
    control = sum(1 for c in stripped if unicodedata.category(c) == "Cc" and c not in "\n\t\r")
    if control:
        flag("control_chars", "garbage" if control >= 3 else "suspect")
    solid = [c for c in stripped if not c.isspace()]
    if len(stripped) >= 20 and solid and sum(1 for c in solid if not c.isalnum()) / len(solid) > 0.6 \
            and "```" not in stripped:
        flag("symbol_noise", "garbage")
    if re.search(r"(.)\1{19,}", stripped, re.S) and not re.search(r"^[\s=\-*_#~.]+$", stripped):
        flag("char_run", "garbage")
    words = [w.casefold() for w in _WORD.findall(stripped)]
    if len(words) >= 3:
        unique = len(set(words))
        if unique == 1:
            if words[0] not in _REPEAT_OK or len(words) >= 7:
                flag("repetition", "garbage")                   # "Или Или Или"
        elif len(words) >= 8 and unique / len(words) < 0.25:
            flag("repetition", "garbage")
        elif _repeating_ngram(words) >= (4 if len(words) >= 12 else 3) and len(set(words)) <= max(2, len(words) // 3):
            flag("repetition", "garbage")
        elif _repeating_ngram(words) >= 5:
            flag("repetition_loop", "suspect")
    if _TEMPLATE.search(stripped):
        flag("template_leak", "garbage" if len(_TEMPLATE.findall(stripped)) >= 3 else "suspect")
    cyr_expected = expect_cyrillic if expect_cyrillic is not None else bool(_CYR.search(str(prompt or "")))
    if cyr_expected and len(words) >= 6:
        cyr = sum(1 for w in words if _CYR.search(w))
        mixed = sum(1 for w in words if _CYR.search(w) and _LAT.search(w))
        if mixed / len(words) > 0.3:
            flag("mixed_script_words", "garbage")
        prose = _URL.sub(" ", stripped)
        if cyr == 0 and not prompt_foreign and len(_WORD.findall(prose)) >= 8 and len(_LAT.findall(prose)) >= 30:
            # the user wrote Russian and got a long answer with no Russian at all: suspect, may be a translation
            flag("language_switch", "suspect")
    return Assessment(tuple(dict.fromkeys(problems)), severity)


# -- canary ------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Canary:
    id: str
    prompt: str
    expected: tuple[str, ...]           # any of these substrings (casefolded) proves a sane answer


CANARIES: tuple[Canary, ...] = (
    Canary("capital", "Ответь одним словом: какая столица Франции?", ("париж", "paris")),
    Canary("sum", "Сколько будет 2+3? Ответь только числом.", ("5", "пять")),
    Canary("sky", "Ответь одним словом: какого цвета небо в ясный день?", ("голуб", "син", "blue")),
)


@dataclass(frozen=True, slots=True)
class CanaryResult:
    id: str
    ok: bool
    reason: str
    latency_ms: int
    at: float

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "ok": self.ok, "reason": self.reason, "latency_ms": self.latency_ms,
                "at": round(self.at, 3)}


ChatFn = Callable[[list[dict]], Awaitable[Any]]
Action = Callable[[], Awaitable[Any] | Any]


@dataclass(slots=True)
class _Recovery:
    attempted: int = 0
    suppressed: int = 0
    last_action: str = ""
    last_at: float | None = None
    last_result: str = ""
    history: deque = field(default_factory=lambda: deque(maxlen=16))       # monotonic times of attempts


def _text_of(result: Any) -> str:
    if isinstance(result, str):
        return result
    return str(getattr(result, "text", "") or "")


class ModelGuardModule(BaseModule):
    name = "model_guard"
    version = "1"
    order = 20

    def __init__(self, *, chat: ChatFn | None = None, model: str = "", unload: Action | None = None,
                 restart: Action | None = None, allow_restart: bool = False,
                 notify: Callable[[dict[str, Any]], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic, wall_clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 canary_interval: float = CANARY_INTERVAL_S, degraded_interval: float = CANARY_DEGRADED_INTERVAL_S,
                 canary_timeout: float = CANARY_TIMEOUT_S, action_timeout: float = ACTION_TIMEOUT_S,
                 recovery_min_interval: float = RECOVERY_MIN_INTERVAL_S,
                 recovery_max_per_hour: int = RECOVERY_MAX_PER_HOUR) -> None:
        self._chat, self._model = chat, model
        self._actions: list[tuple[str, Action]] = []
        if unload is not None:
            self._actions.append(("unload", unload))
        self._restart = restart if (restart is not None and allow_restart) else None
        self._notify = notify
        self._clock, self._wall, self._sleep = clock, wall_clock, sleep
        self._intervals = (canary_interval, degraded_interval)
        self._canary_timeout, self._action_timeout = canary_timeout, action_timeout
        self._recovery_min, self._recovery_max = recovery_min_interval, recovery_max_per_hour
        self.state = State.HEALTHY
        self._since: float | None = None
        self._suspects: deque = deque(maxlen=16)
        self._good_streak = 0
        self._canary_no = 0
        self._canaries_run = 0
        self._canary_failures = 0
        self._last_canary: CanaryResult | None = None
        self._garbage_replies = 0
        self._recovery = _Recovery()
        self._needs_owner = False
        self._owner_notified = False
        self._probe_lock = asyncio.Lock()
        self._bg: set[asyncio.Task] = set()
        self._loop_task: asyncio.Task | None = None
        self._last_probe_at = -1e9

    # -- state machine ---------------------------------------------------------------------------
    def _disabled(self) -> bool:
        return os.environ.get(MODULE_ENV, "").strip().lower() in {"off", "0", "false", "no"}

    def _enter(self, state: State) -> None:
        if state is not self.state:
            self.state = state
            self._since = self._wall()

    def observe(self, assessment: Assessment, *, source: str = "turn") -> State:
        """Feed one judged answer (turn reply or canary) into the state machine."""
        now = self._clock()
        if assessment.ok:
            if self.state is State.HEALTHY:
                return self.state
            self._good_streak += 1
            if self.state is State.SUSPECT and not self._recent_suspects(now):
                self._good_streak = 0
                self._enter(State.HEALTHY)
            elif self._good_streak >= GOOD_TO_HEAL:
                self._good_streak = 0
                self._suspects.clear()
                self._needs_owner = False
                self._owner_notified = False
                self._enter(State.HEALTHY)
            return self.state
        self._good_streak = 0
        if assessment.garbage:
            self._garbage_replies += 1 if source == "turn" else 0
            self._suspects.clear()
            if self.state not in (State.DEGRADED,):
                self._enter(State.DEGRADED)
            return self.state
        self._suspects.append(now)
        if len(self._recent_suspects(now)) >= SUSPECTS_TO_DEGRADE:
            self._enter(State.DEGRADED)
        elif self.state is State.HEALTHY:
            self._enter(State.SUSPECT)
        return self.state

    def _recent_suspects(self, now: float) -> list[float]:
        return [t for t in self._suspects if now - t <= SUSPECT_WINDOW_S]

    @property
    def healthy(self) -> bool:
        return self.state in (State.HEALTHY, State.SUSPECT)

    def routing_hint(self) -> dict[str, Any]:
        """For the router: prefer another model while the local one is degraded. Never forces anything."""
        bad = self.state in (State.DEGRADED, State.RECOVERING)
        return {"avoid_local": bad, "state": self.state.value, "reason": "model_guard" if bad else "",
                "needs_owner": self._needs_owner}

    # -- canary ----------------------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._chat is not None

    async def probe(self) -> CanaryResult | None:
        """Run one canary; the result also feeds the state machine. None when no local model is configured."""
        if self._chat is None or self._disabled():
            return None
        async with self._probe_lock:
            canary = CANARIES[self._canary_no % len(CANARIES)]
            self._canary_no += 1
            started = self._clock()
            reason, level = "ok", "ok"
            try:
                result = await asyncio.wait_for(
                    self._chat([{"role": "user", "content": canary.prompt}]), timeout=self._canary_timeout)
                text = _text_of(result)
                verdict = assess(text, prompt=canary.prompt, expect_cyrillic=True)
                if not verdict.ok:
                    reason, level = "garbage:" + "+".join(verdict.problems), verdict.severity
                elif not any(token in text.casefold() for token in canary.expected):
                    reason, level = "mismatch", "suspect"
            except asyncio.TimeoutError:
                reason, level = "timeout", "suspect"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - an unreachable model is a signal, not a crash
                reason, level = "error:" + type(exc).__name__, "suspect"
            latency = int((self._clock() - started) * 1000)
            outcome = CanaryResult(canary.id, level == "ok", reason, latency, self._wall())
            self._last_canary = outcome
            self._canaries_run += 1
            self._last_probe_at = self._clock()
            if not outcome.ok:
                self._canary_failures += 1
            previous = self.state
            self.observe(Assessment((reason,) if level != "ok" else (), level), source="canary")
            if self.state is State.DEGRADED and previous is not State.DEGRADED:
                await self.recover(reason="canary:" + reason)
            elif self.state is State.DEGRADED:
                await self.recover(reason="canary_still_bad")
            return outcome

    # -- recovery --------------------------------------------------------------------------------
    async def recover(self, *, reason: str = "") -> str:
        """Run the next recovery action if the rate limits allow. Returns 'done', 'suppressed' or 'none'."""
        now = self._clock()
        rec = self._recovery
        recent = [t for t in rec.history if now - t <= 3600.0]
        too_soon = bool(rec.history) and now - rec.history[-1] < self._recovery_min
        if len(recent) >= self._recovery_max or too_soon:
            rec.suppressed += 1
            if len(recent) >= self._recovery_max:
                self._needs_owner = True
                await self._tell_owner("model_guard.stuck")
            return "suppressed"
        action = self._pick_action(len(recent))
        if action is None:
            return "none"
        name, call = action
        rec.history.append(now)
        rec.attempted += 1
        rec.last_action, rec.last_at = name, self._wall()
        try:
            outcome = call()
            if hasattr(outcome, "__await__"):
                await asyncio.wait_for(outcome, timeout=self._action_timeout)
            rec.last_result = "ok"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            rec.last_result = "error:" + type(exc).__name__
        self._enter(State.RECOVERING)
        self._good_streak = 0
        return "done"

    def _pick_action(self, attempts_this_hour: int) -> tuple[str, Action] | None:
        """Unload first; the injected restart only from the second attempt and only when explicitly allowed."""
        if attempts_this_hour >= 1 and self._restart is not None:
            return ("restart", self._restart)
        if self._actions:
            return self._actions[0]
        return None

    async def _tell_owner(self, kind: str) -> None:
        if self._owner_notified or self._notify is None:
            return
        self._owner_notified = True
        try:
            result = self._notify({"kind": kind, "module": self.name, "state": self.state.value,
                                   "model": self._model, "recoveries": self._recovery.attempted})
            if hasattr(result, "__await__"):
                await asyncio.wait_for(result, timeout=0.2)
        except Exception:  # noqa: BLE001
            pass

    # -- background loop -------------------------------------------------------------------------
    async def start(self) -> None:
        if self._loop_task is None and self._chat is not None and not self._disabled():
            self._loop_task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        task, self._loop_task = self._loop_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        for pending in list(self._bg):
            pending.cancel()

    async def _run(self) -> None:
        while True:
            calm, degraded = self._intervals
            await self._sleep(calm if self.healthy else degraded)
            try:
                await self.probe()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                continue

    async def wait_idle(self) -> None:
        """Wait for background recovery started by ``post_reply`` (used by tests and shutdown)."""
        while self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.get_running_loop().create_task(coro)          # type: ignore[arg-type]
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    # -- hooks -----------------------------------------------------------------------------------
    async def augment(self, ctx: TurnContext) -> Advice | None:
        if self._disabled() or self.healthy:
            return None
        return Advice(notes=(HINT_NOTE,), tags=("model_guard:" + self.state.value,))

    async def post_reply(self, ctx: TurnContext, reply: str) -> str | None:
        if self._disabled():
            return None
        served = str(ctx.extra.get("served_by", "local"))
        if served not in ("local", ""):
            return None                                        # a cloud answer says nothing about the local model
        verdict = assess(reply, prompt=ctx.text)
        previous = self.state
        self.observe(verdict, source="turn")
        if self.state is State.DEGRADED and previous is not State.DEGRADED:
            self._spawn(self.recover(reason="turn:" + "+".join(verdict.problems)))
        if verdict.garbage:
            return APOLOGY
        return None

    def status(self) -> dict[str, Any]:
        rec = self._recovery
        return {"available": self.available, "model": self._model, "state": self.state.value, "since": self._since,
                "last_canary": self._last_canary.as_dict() if self._last_canary else None,
                "canaries_run": self._canaries_run, "canary_failures": self._canary_failures,
                "garbage_replies": self._garbage_replies,
                "recoveries": {"attempted": rec.attempted, "suppressed": rec.suppressed,
                               "last_action": rec.last_action, "last_at": rec.last_at,
                               "last_result": rec.last_result},
                "needs_owner": self._needs_owner, "hint": self.routing_hint()}


# -- factory -----------------------------------------------------------------------------------------
def create(runtime: Any) -> ModelGuardModule:
    """Wire the guard to the runtime's local adapter when there is one; otherwise it is a passive text checker."""
    adapter = getattr(runtime, "local_adapter", None)
    settings = getattr(runtime, "settings", None)
    models = tuple(getattr(settings, "local_models", ()) or ())
    model = models[0] if models else ""
    chat = unload = None
    if adapter is not None and model and hasattr(adapter, "chat"):
        async def chat(messages: list[dict]) -> Any:                 # noqa: F811 - closure over adapter and model
            return await adapter.chat(model, messages, max_tokens=24, temperature=0.0)
    if adapter is not None and model and hasattr(adapter, "unload"):
        async def unload() -> Any:                                    # noqa: F811
            return await adapter.unload(model)
    notify = getattr(runtime, "j2_escalate", None)
    return ModelGuardModule(chat=chat, model=model, unload=unload, notify=notify if callable(notify) else None)
