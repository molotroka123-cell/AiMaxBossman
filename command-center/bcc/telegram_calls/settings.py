"""Paths and owner-tunable settings of the calls module.

Storage reuses Bossman's own data directory and its existing Fernet Vault (``bcc.secrets.Vault``, the one that protects
provider keys) and keeps everything of this module in the ``telegram-calls/`` subdirectory. Not the Telegram companion's
directory, not Jeff's: the calls module is a surface of Bossman, not of either of them. ``config.json`` holds NO secrets: api_id/api_hash
and the session live only in ``credentials.enc`` (see ``account.credentials``).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .types import MAX_ALLOWED_PEERS, PeerRef

CONFIG_NAME = "config.json"
HOME_ENV = "BOSSMAN_TELEGRAM_CALLS_HOME"
_MAX_CONFIG_BYTES = 65536


def data_dir() -> Path:
    """Bossman's own data directory (``BCC_DATA_DIR``): the SAME directory and Vault the backend uses — no separate store."""
    from ..config import Settings
    return Path(Settings().data_dir)


def calls_home() -> Path:
    override = os.environ.get(HOME_ENV)
    return Path(override) if override else data_dir() / "telegram-calls"


def secret_home() -> Path:
    """Directory whose ``secret.key`` (the existing Fernet Vault) encrypts credentials: Bossman's data dir."""
    override = os.environ.get(HOME_ENV)
    return Path(override).parent if override else data_dir()


#: the spoken line of the answering machine when nothing else is configured: honest about WHO answers, never "I am the owner"
DEFAULT_ANSWER_GREETING = ("Здравствуйте! Это Джефф, ИИ-ассистент владельца. Он сейчас не может ответить. "
                           "Я приму сообщение: скажите, кто вы и что ему передать?")
MAX_ANSWER_LIST = 200
_ASSISTANT_WORDS = re.compile(r"(?i)ассистент|автоответчик|помощник|секретар|искусственн|\bии\b|\bai\b|\bбот\b|нейросет")
_HUMAN_CLAIM = re.compile(r"(?i)\bя\s+(?:сам|человек|живой|владелец|хозяин)\b|\bэто\s+сам\b|\bговорит\s+(?:сам|владелец|хозяин)\b"
                          r"|\bi\s+am\s+(?:a\s+)?(?:human|the\s+owner)(?!['’])\b|\bthis\s+is\s+the\s+owner(?!['’])\b")


def check_answer_greeting(text: str) -> str | None:
    """None when the answering-machine greeting is honest, else a short reason: it must say that an assistant (not the owner,
    not a person) is speaking, and must never claim to be the human owner."""
    if not isinstance(text, str) or not text.strip():
        return "greeting is empty"
    if len(text) > 240:
        return "greeting too long"
    if _HUMAN_CLAIM.search(text):
        return "greeting must not claim to be the owner or a human"
    if not _ASSISTANT_WORDS.search(text):
        return "greeting must say that an assistant answers"
    return None


@dataclass(frozen=True)
class CallSettings:
    enabled: bool = False                      # calls are OFF until the owner switches them on
    peer_user_id: int | None = None            # the ONE allowed test interlocutor
    peer_label: str = ""
    max_call_s: int = 600
    ring_timeout_s: int = 45
    idle_prompt_s: int = 25
    idle_hangup_s: int = 60
    barge_in: bool = True
    echo_mode: str = "guard"                   # guard | half_duplex
    stt_model_path: str = ""                   # override of Jeff's Whisper model dir; empty = BOSSMAN_WHISPER_MODEL_PATH (as Jeff)
    greeting: str = "Привет! Это Джефф, ИИ-ассистент. Ты меня слышишь?"
    record_audio: bool = False                 # OFF by default; needs the owner's explicit choice
    auto_save_to_bossman_memory: bool = False  # OFF: the owner saves the summary / drafts tasks with one click (Jeff must not write owner data)
    vad: str = "auto"                          # auto | silero | energy
    language: str = "ru"                       # ru | en | auto: what the other person speaks (STT language, fixed phrases, voice); "auto" = Whisper decides
    # ---- answering machine (incoming calls). OFF by default; only the owner can change these (the API/CLI are owner-authenticated)
    answering_machine: bool = False            # Jeff takes an incoming call when the owner does not
    answer_ring_delay_s: int = 12              # the owner gets this long to pick up himself; if he does, Jeff never joins
    answer_max_call_s: int = 180               # hard cut-off of an answered call
    answer_allow_ids: list = field(default_factory=list)   # callers allowed to be answered; empty = any caller
    answer_deny_ids: list = field(default_factory=list)    # callers never answered (the owner's phone keeps ringing)
    answer_allow_unknown: bool = True          # a caller the engine cannot identify may be answered
    answer_greeting: str = DEFAULT_ANSWER_GREETING
    extra: dict = field(default_factory=dict)  # forward-compatible, ignored keys are kept, never executed

    def __post_init__(self):
        def bad(msg):
            raise ValueError(msg)
        for name in ("enabled", "barge_in", "record_audio", "auto_save_to_bossman_memory"):
            if type(getattr(self, name)) is not bool:
                bad(f"{name} must be a boolean")
        if self.peer_user_id is not None:
            PeerRef(self.peer_user_id)         # validates the integer
        if not isinstance(self.peer_label, str) or len(self.peer_label) > 120:
            bad("peer_label too long")
        for name, lo, hi in (("max_call_s", 30, 3600), ("ring_timeout_s", 10, 120),
                             ("idle_prompt_s", 5, 300), ("idle_hangup_s", 10, 900)):
            v = getattr(self, name)
            if type(v) is not int or not lo <= v <= hi:
                bad(f"{name} must be an integer {lo}..{hi}")
        if self.idle_hangup_s <= self.idle_prompt_s:
            bad("idle_hangup_s must exceed idle_prompt_s")
        if self.echo_mode not in {"guard", "half_duplex"} or self.vad not in {"auto", "silero", "energy"}:
            bad("invalid enum value")
        if self.language not in {"ru", "en", "auto"}:
            bad("language must be ru, en or auto")
        v = self.stt_model_path
        if not isinstance(v, str) or len(v) > 500 or "\x00" in v:
            bad("stt_model_path invalid")
        if not isinstance(self.greeting, str) or len(self.greeting) > 200:
            bad("greeting too long")
        if not isinstance(self.extra, dict):
            bad("extra must be an object")
        if type(self.answering_machine) is not bool or type(self.answer_allow_unknown) is not bool:
            bad("answering_machine and answer_allow_unknown must be booleans")
        for name, lo, hi in (("answer_ring_delay_s", 0, 60), ("answer_max_call_s", 30, 900)):
            v = getattr(self, name)
            if type(v) is not int or not lo <= v <= hi:
                bad(f"{name} must be an integer {lo}..{hi}")
        for name in ("answer_allow_ids", "answer_deny_ids"):
            v = getattr(self, name)
            if (not isinstance(v, list) or len(v) > MAX_ANSWER_LIST or len(set(v)) != len(v)
                    or any(type(i) is not int or not 0 < i < 2 ** 52 for i in v)):
                bad(f"{name} must be a list of up to {MAX_ANSWER_LIST} distinct Telegram user ids")
        reason = check_answer_greeting(self.answer_greeting)
        if reason:
            bad(reason)

    @property
    def peer(self) -> PeerRef | None:
        return PeerRef(self.peer_user_id, self.peer_label) if self.peer_user_id else None

    def to_json(self) -> dict:
        return asdict(self)


def load_settings(home: Path | None = None) -> CallSettings:
    """Missing file -> defaults (calls disabled). A corrupt file is an error, never silently 'enabled'."""
    path = (home or calls_home()) / CONFIG_NAME
    if not path.is_file():
        return CallSettings()
    raw = path.read_bytes()
    if len(raw) > _MAX_CONFIG_BYTES:
        raise ValueError("calls configuration too large")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("calls configuration must be an object")
    known = {k: v for k, v in data.items() if k in CallSettings.__dataclass_fields__}
    return CallSettings(**known)


def save_settings(settings: CallSettings, home: Path | None = None) -> Path:
    from .hardening import restrict_to_owner
    home = home or calls_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    restrict_to_owner(home)        # the directory variant: what is already inside stays readable (see hardening)
    path = home / CONFIG_NAME
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(json.dumps(settings.to_json(), ensure_ascii=False, indent=2) + "\n")
        out.flush()
        os.fsync(out.fileno())
    restrict_to_owner(tmp)
    os.replace(tmp, path)
    return path


assert MAX_ALLOWED_PEERS == 1   # the settings model above carries exactly one peer
