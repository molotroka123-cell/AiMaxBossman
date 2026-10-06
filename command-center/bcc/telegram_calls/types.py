"""Shared contracts of the Telegram live-calls module (single source of truth).

Every other module of ``bcc.telegram_calls`` imports its enums, error codes,
dataclasses and Protocols from here, so parallel work packages cannot drift.

Scope (owner request 2026-09-29): the owner's MAIN Telegram *user* account
(MTProto, not the Bot API) places a real 1:1 voice call to ONE explicitly chosen
SECOND account (the test peer) and talks to Bossman by voice in real time.
Nothing here grants permission to call anybody else.

Honesty rules baked into the types:
* an outcome is ``UNKNOWN`` when we cannot prove what happened; it is never
  rewritten to ``COMPLETED`` and never triggers an automatic redial;
* ``CallError.code`` values are stable, secret-free strings (no phone numbers,
  codes, hashes, session strings, URLs with tokens, transcripts);
* audio and transcripts are NOT persisted unless the owner opted in.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Protocol, runtime_checkable

# ---------------------------------------------------------------- constants

#: PCM everywhere inside the pipeline: signed 16-bit little-endian, mono.
SAMPLE_WIDTH = 2
#: Internal analysis rate for VAD / STT (Hz). Transport rate may differ.
ANALYSIS_RATE = 16000
#: Playout frame duration pushed to the transport (ms). Real-time paced.
PLAYOUT_FRAME_MS = 20
#: Test phase: exactly one allowed peer. Raising this is an owner decision.
MAX_ALLOWED_PEERS = 1


class CallState(str, Enum):
    """Coarse life cycle of one call (one worker-side ``CallSession``)."""
    IDLE = "idle"              # no call
    DIALING = "dialing"        # request_call sent, waiting for the peer device
    RINGING = "ringing"        # peer device is ringing
    ACTIVE = "active"          # media connected, conversation running
    ENDING = "ending"          # hangup issued, waiting for confirmation
    ENDED = "ended"            # terminal; see ``Outcome``


class Phase(str, Enum):
    """Conversation phase while ``CallState.ACTIVE``."""
    LISTENING = "listening"
    THINKING = "thinking"      # STT final received, LLM/TTS not yet audible
    SPEAKING = "speaking"


class Outcome(str, Enum):
    """Terminal result of a call. ``UNKNOWN`` blocks a casual manual redial."""
    COMPLETED = "completed"                  # normal end (either side hung up, or model said goodbye)
    DECLINED = "declined"                    # peer pressed decline
    BUSY = "busy"                            # peer busy / already in a call
    NO_ANSWER = "no_answer"                  # ring timeout
    CONNECTION_LOST = "connection_lost"      # media dropped and did not come back within grace
    STOPPED = "stopped"                      # owner STOP (any surface)
    MAX_DURATION = "max_duration"            # configured limit reached
    SILENCE_TIMEOUT = "silence_timeout"      # nobody spoke for too long
    FAILED = "failed"                        # provable local failure, see ``error_code``
    UNKNOWN = "unknown"                      # cannot prove what happened


#: Outcomes after which an immediate manual redial needs an explicit confirm.
UNCERTAIN_OUTCOMES = frozenset({Outcome.UNKNOWN, Outcome.CONNECTION_LOST})


class AccountState(str, Enum):
    NO_CREDENTIALS = "no_credentials"    # api_id / api_hash not saved yet
    LOGGED_OUT = "logged_out"            # credentials saved, no session
    CODE_SENT = "code_sent"              # waiting for the Telegram login code
    PASSWORD_NEEDED = "password_needed"  # waiting for the 2FA password
    READY = "ready"                      # authorised session
    ERROR = "error"


# ---------------------------------------------------------------- errors

#: code -> (Russian message shown to the owner, Russian hint what to do).
#: Codes are stable API; add, never rename. No secret may ever appear here.
ERRORS: dict[str, tuple[str, str]] = {
    "DEPENDENCIES_MISSING": ("Не установлены зависимости звонков Telegram.",
                             "Выполните: bossman call install (или pip install \"bossman-command-center[calls]\")."),
    "NOT_ENABLED": ("Звонки выключены в настройках.", "Включите «Разрешить звонки» в разделе Telegram-звонки."),
    "NO_CREDENTIALS": ("Не сохранены api_id и api_hash.", "Получите их на my.telegram.org и введите на экране подключения."),
    "NOT_LOGGED_IN": ("Аккаунт не подключён.", "Пройдите вход: номер → код Telegram → пароль 2FA (если включён)."),
    "LOGIN_PHONE_INVALID": ("Номер телефона не принят Telegram.", "Введите номер в международном формате, например +79001234567."),
    "LOGIN_CODE_INVALID": ("Код не подошёл.", "Введите новый код из приложения Telegram; не пересылайте его в чатах."),
    "LOGIN_CODE_EXPIRED": ("Код устарел.", "Запросите код заново."),
    "LOGIN_PASSWORD_INVALID": ("Пароль двухэтапной защиты не подошёл.", "Проверьте раскладку и повторите ввод."),
    "LOGIN_FLOOD_WAIT": ("Telegram просит подождать перед новой попыткой входа.", "Подождите указанное время и повторите."),
    "LOGIN_NOT_PENDING": ("Вход не начат.", "Сначала запросите код."),
    "SESSION_REVOKED": ("Сессия отозвана в Telegram.", "Войдите заново."),
    "PEER_NOT_SELECTED": ("Тестовый собеседник не выбран.", "Выберите второй аккаунт и подтвердите выбор."),
    "PEER_NOT_ALLOWED": ("Звонок разрешён только выбранному тестовому собеседнику.", "Смените собеседника в настройках — вручную."),
    "PEER_IS_SELF": ("Нельзя звонить самому себе.", "Выберите ВТОРОЙ аккаунт."),
    "PEER_INVALID": ("Этот контакт не подходит для звонка.", "Выберите обычного пользователя (не бота, не удалённый аккаунт)."),
    "PEER_NOT_FOUND": ("Собеседник не найден в контактах.", "Проверьте юзернейм/номер и что второй аккаунт есть в контактах."),
    "PEER_PRIVACY": ("Второй аккаунт запретил звонки от вас.", "Разрешите звонки в Настройки → Конфиденциальность → Звонки."),
    "CALL_IN_PROGRESS": ("Звонок уже идёт.", "Завершите текущий звонок."),
    "STOP_ACTIVE": ("Действует STOP: звонки заблокированы.", "Снимите STOP вручную («Продолжить»), затем повторите."),
    "UNCERTAIN_PREVIOUS_CALL": ("Исход предыдущего звонка неизвестен.",
                                "Проверьте второй аккаунт; повторите с подтверждением, если уверены."),
    "CALL_DECLINED": ("Собеседник отклонил звонок.", "Позвоните вручную ещё раз, когда он будет готов."),
    "CALL_BUSY": ("Собеседник занят.", "Повторите вручную позже."),
    "CALL_NO_ANSWER": ("Собеседник не ответил.", "Убедитесь, что Telegram открыт на втором аккаунте, и повторите вручную."),
    "CALL_DISCARDED": ("Звонок завершён собеседником.", ""),
    "CONNECTION_LOST": ("Связь потеряна.", "Автоматического перезвона нет — повторите вручную."),
    "MAX_DURATION": ("Достигнут лимит длительности звонка.", "Лимит задаётся в настройках."),
    "SILENCE_TIMEOUT": ("Долгая тишина — звонок завершён.", ""),
    "STT_UNAVAILABLE": ("Распознавание речи недоступно.", "Проверьте модель Whisper: bossman call doctor."),
    "TTS_UNAVAILABLE": ("Синтез речи недоступен.", "Проверьте голос TTS: bossman call doctor."),
    "BRAIN_NOT_CONFIGURED": ("Локальная модель для разговора не настроена.",
                             "Задайте модели в разделе Telegram (лучшая/быстрая) и проверьте их."),
    "BRAIN_UNAVAILABLE": ("Локальная модель не отвечает.", "Запустите сервер модели и повторите; платный fallback не используется."),
    "VAD_UNAVAILABLE": ("Детектор речи недоступен.", "Проверьте bossman call doctor."),
    "WORKER_UNAVAILABLE": ("Процесс звонков не запущен или упал.", "Нажмите «Подключить» или выполните bossman call doctor."),
    "WORKER_TIMEOUT": ("Процесс звонков не ответил вовремя.", "Повторите; при повторении — bossman call doctor."),
    "TELEGRAM_NETWORK": ("Нет связи с Telegram.", "Проверьте интернет/прокси и повторите."),
    "TELEGRAM_RPC": ("Telegram отклонил запрос.", "Повторите позже; подробности в bossman call doctor."),
    "INTERNAL": ("Внутренняя ошибка модуля звонков.", "Смотрите журнал: bossman call doctor."),
}


class CallError(Exception):
    """Stable, secret-free failure. ``str(exc)`` is the code, never free text."""

    def __init__(self, code: str, *, detail: str | None = None):
        if code not in ERRORS:
            code = "INTERNAL"
        super().__init__(code)
        self.code = code
        #: Optional short, secret-free technical detail (exception class name,
        #: seconds to wait...). Never phone numbers, codes, tokens or text.
        self.detail = detail

    @property
    def message(self) -> str:
        return ERRORS[self.code][0]

    @property
    def hint(self) -> str:
        return ERRORS[self.code][1]

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "hint": self.hint,
                **({"detail": self.detail} if self.detail else {})}


# ---------------------------------------------------------------- audio / peers

@dataclass(frozen=True)
class AudioFormat:
    """PCM16 mono at ``sample_rate`` Hz (transport side)."""
    sample_rate: int = 48000
    channels: int = 1

    def frame_bytes(self, ms: int = PLAYOUT_FRAME_MS) -> int:
        return self.sample_rate * ms // 1000 * SAMPLE_WIDTH * self.channels


@dataclass(frozen=True)
class PeerRef:
    """The one allowed test interlocutor. ``label`` is display-only."""
    user_id: int
    label: str = ""

    def __post_init__(self):
        if type(self.user_id) is not int or not 0 < self.user_id < 2**52:
            raise ValueError("peer user_id must be a positive Telegram integer")


class CancelToken:
    """Cooperative cancellation shared by LLM stream, TTS and playout."""

    def __init__(self):
        self._event = asyncio.Event()
        self.reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str = "cancelled") -> None:
        if not self._event.is_set():
            self.reason = reason
            self._event.set()

    async def wait(self) -> None:
        await self._event.wait()


# ---------------------------------------------------------------- transport

class TransportEventKind(str, Enum):
    RINGING = "ringing"
    CONNECTED = "connected"          # media path established
    DISCONNECTED = "disconnected"    # media dropped; may recover
    RECONNECTED = "reconnected"
    ENDED = "ended"                  # call is over; ``reason`` says who/why


@dataclass(frozen=True)
class TransportEvent:
    kind: TransportEventKind
    #: for ENDED: "peer_hangup" | "local_hangup" | "declined" | "busy" | "timeout" | "error"
    reason: str = ""
    at: float = field(default_factory=time.monotonic)


@runtime_checkable
class CallTransport(Protocol):
    """One private call over some engine (real: py-tgcalls; tests: loopback).

    Contract:
    * ``dial`` is called at most ONCE per call and NEVER retried by the engine;
    * ``dial`` returns only when media is connected, otherwise raises
      ``CallError`` with CALL_DECLINED / CALL_BUSY / CALL_NO_ANSWER /
      TELEGRAM_* / PEER_PRIVACY;
    * ``hangup`` is idempotent and safe in any state;
    * outgoing audio is PCM16 mono at ``audio_format.sample_rate``; ``send_audio`` receives EXACTLY
      ``frame_ms`` of audio per call (the real engine, ntgcalls, consumes only the first 10 ms of whatever
      it is given and over-reads shorter data), paced on a wall-clock deadline by the caller; the rate must be
      a multiple of 100 Hz (22050 Hz crashed the native library);
    * incoming audio is PCM16 mono at ``rx_sample_rate`` (the real engine can deliver 16 kHz directly);
    * pushed audio cannot be recalled from the engine, so the caller keeps at most ~1 frame in flight and
      ``clear_outgoing`` drops whatever the engine still buffers (barge-in / STOP), best effort.
    """

    audio_format: AudioFormat        # outgoing PCM format
    rx_sample_rate: int              # incoming PCM sample rate (Hz)
    frame_ms: int                    # outgoing frame duration, real-time pacing unit (10 for ntgcalls)
    name: str

    async def start(self) -> None: ...
    async def dial(self, peer: PeerRef, *, ring_timeout: float) -> None: ...
    def set_audio_callback(self, cb: Callable[[bytes], None]) -> None: ...
    def set_event_callback(self, cb: Callable[[TransportEvent], None]) -> None: ...
    async def send_audio(self, pcm: bytes) -> None: ...
    async def clear_outgoing(self) -> None: ...
    async def hangup(self, reason: str = "local") -> None: ...
    async def close(self) -> None: ...


# ---------------------------------------------------------------- speech engines

@dataclass
class STTResult:
    text: str
    duration_s: float = 0.0
    language: str = "ru"
    confidence: float | None = None
    decode_ms: float = 0.0


@runtime_checkable
class STTStream(Protocol):
    """One utterance. ``feed`` never blocks the event loop."""

    def feed(self, pcm16k: bytes) -> None: ...
    async def partial(self) -> str: ...
    async def finalize(self) -> STTResult: ...
    def cancel(self) -> None: ...


@runtime_checkable
class STTEngine(Protocol):
    name: str
    model: str

    def new_stream(self, *, language: str = "ru") -> STTStream: ...
    def status(self) -> dict[str, Any]: ...


@runtime_checkable
class TTSEngine(Protocol):
    name: str
    voice: str
    sample_rate: int            # of the PCM16 mono chunks yielded by ``synthesize``

    def synthesize(self, text: str, cancel: CancelToken) -> AsyncIterator[bytes]: ...
    def status(self) -> dict[str, Any]: ...


@dataclass
class Turn:
    role: str                   # "user" | "assistant"
    text: str
    interrupted: bool = False   # assistant turn cut by barge-in (text = what was actually spoken)


@runtime_checkable
class Brain(Protocol):
    """Jeff/Bossman's existing reply path (context, memory, rules, local model routes) — never a second brain."""

    route: str                  # "main" | "fast"
    model: str

    def reply(self, history: list[Turn], user_text: str, cancel: CancelToken) -> AsyncIterator[str]: ...
    async def summarize(self, turns: list[Turn]) -> "CallSummary": ...
    def status(self) -> dict[str, Any]: ...


# ---------------------------------------------------------------- results

@dataclass
class TurnMetrics:
    turn_id: int
    #: monotonic seconds; None = stage never reached
    t_speech_end: float | None = None      # VAD decided the user stopped
    t_stt_final: float | None = None
    t_llm_first_token: float | None = None
    t_tts_first_audio: float | None = None
    t_first_frame_sent: float | None = None   # first PCM frame handed to the transport
    interrupted: bool = False
    echo_suppressed: int = 0
    stt_ms: float | None = None
    user_chars: int = 0
    assistant_chars: int = 0

    @property
    def response_latency_ms(self) -> float | None:
        """End of the user's speech -> first outgoing audio frame (the number that matters)."""
        if self.t_speech_end is None or self.t_first_frame_sent is None:
            return None
        return round((self.t_first_frame_sent - self.t_speech_end) * 1000.0, 1)

    def as_dict(self) -> dict[str, Any]:
        def ms(a: float | None, b: float | None) -> float | None:
            return None if a is None or b is None else round((b - a) * 1000.0, 1)
        return {"turn": self.turn_id, "response_latency_ms": self.response_latency_ms,
                "stt_final_ms": ms(self.t_speech_end, self.t_stt_final),
                "llm_first_token_ms": ms(self.t_stt_final, self.t_llm_first_token),
                "tts_first_audio_ms": ms(self.t_llm_first_token, self.t_tts_first_audio),
                "send_ms": ms(self.t_tts_first_audio, self.t_first_frame_sent),
                "interrupted": self.interrupted, "echo_suppressed": self.echo_suppressed,
                "user_chars": self.user_chars, "assistant_chars": self.assistant_chars}


@dataclass
class CallSummary:
    """What is saved to the existing memory after a call. No transcript."""
    text: str = ""
    agreed_tasks: list[str] = field(default_factory=list)   # proposals only; never executed
    generated_by: str = "none"                              # model id, or "mechanical"


@dataclass
class CallRecord:
    call_id: str
    transport: str                      # "telegram" | "loopback" (loopback is NOT a real call)
    peer_user_id: int
    started_at: float                   # epoch seconds
    ended_at: float | None = None
    outcome: Outcome | None = None
    error_code: str | None = None
    state: CallState = CallState.IDLE
    turns: list[TurnMetrics] = field(default_factory=list)
    models: dict[str, str] = field(default_factory=dict)   # stt / llm / tts (what was actually used)
    counters: dict[str, int] = field(default_factory=dict)  # barge_ins, echo_suppressed, stt_empty, ...
    summary: CallSummary | None = None
    recorded_audio: bool = False

    def as_dict(self) -> dict[str, Any]:
        latencies = [t.response_latency_ms for t in self.turns if t.response_latency_ms is not None]
        return {"call_id": self.call_id, "transport": self.transport, "peer_user_id": self.peer_user_id,
                "started_at": self.started_at, "ended_at": self.ended_at,
                "outcome": self.outcome.value if self.outcome else None, "error_code": self.error_code,
                "state": self.state.value, "turns": [t.as_dict() for t in self.turns],
                "latency_ms": latency_stats(latencies), "models": dict(self.models),
                "counters": dict(self.counters), "recorded_audio": self.recorded_audio,
                "summary": ({"text": self.summary.text, "agreed_tasks": list(self.summary.agreed_tasks),
                             "generated_by": self.summary.generated_by} if self.summary else None)}


def latency_stats(values: list[float]) -> dict[str, Any]:
    """p50/p95/max over measured response latencies. Empty -> nulls (never 0)."""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return {"n": 0, "p50": None, "p95": None, "max": None, "last": None}

    def pct(p: float) -> float:
        k = (len(xs) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
        return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)

    return {"n": len(xs), "p50": pct(0.5), "p95": pct(0.95), "max": xs[-1], "last": values[-1] if values else None}


@dataclass(frozen=True)
class CallEvent:
    """One line of the call event log shown by UI/CLI. ``data`` is secret-free and,
    unless the owner opted in to transcripts, text-free."""
    seq: int
    kind: str          # state | phase | turn | metric | barge_in | echo | error | stop | log
    at: float          # epoch seconds
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "kind": self.kind, "at": self.at, **self.data}
