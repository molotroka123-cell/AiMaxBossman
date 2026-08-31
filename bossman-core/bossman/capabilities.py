"""V2.6 — единый словарь capability (раздел 22 V2.6).

НЕ новый реестр и НЕ enforcement: канонические имена способностей, которыми
уже пользуются существующие реестры (gateway `ModelTarget.capabilities`,
`tools/registry.yaml` `can:[...]`, bcc `models.caps`). Роутеры выбирают по
capability, а не по имени бренда. Словарь advisory: существующие free-form
конфиги остаются валидными, `unknown_capabilities()` лишь подсвечивает опечатки.
"""
from __future__ import annotations

# --- capabilities моделей/провайдеров -------------------------------------
TEXT_GENERATION = "text"
CODE = "code"
REASONING = "reasoning"
TOOLS = "tools"
VISION = "vision"
OCR = "ocr"
DOCUMENT_VISION = "document_vision"
SCREEN_UNDERSTANDING = "screen_understanding"
IMAGE_GENERATION = "image_generation"
IMAGE_EDIT = "image_edit"
INPAINT = "inpaint"
OUTPAINT = "outpaint"
REMOVE_BACKGROUND = "remove_background"
UPSCALE = "upscale"
VIDEO_GENERATION = "video_generation"
VIDEO_EDIT = "video_edit"
AUDIO_GENERATION = "audio_generation"
STT = "stt"
TTS = "tts"
EMBEDDING = "embedding"
RERANK = "rerank"

MODEL_CAPABILITIES: frozenset[str] = frozenset({
    TEXT_GENERATION, CODE, REASONING, TOOLS, VISION, OCR, DOCUMENT_VISION,
    SCREEN_UNDERSTANDING, IMAGE_GENERATION, IMAGE_EDIT, INPAINT, OUTPAINT,
    REMOVE_BACKGROUND, UPSCALE, VIDEO_GENERATION, VIDEO_EDIT,
    AUDIO_GENERATION, STT, TTS, EMBEDDING, RERANK,
})

# --- capabilities инструментов --------------------------------------------
WEB_SEARCH = "web_search"
BROWSER = "browser"
FILE_PARSE = "file_parse"
FILE_CREATE = "file_create"
EMAIL_READ = "email_read"
EMAIL_WRITE = "email_write"
GITHUB_READ = "github_read"
GITHUB_WRITE = "github_write"
COMPUTER_CONTROL = "computer_control"
PYTHON_ANALYSIS = "python_analysis"
DATABASE = "database"

TOOL_CAPABILITIES: frozenset[str] = frozenset({
    WEB_SEARCH, BROWSER, FILE_PARSE, FILE_CREATE, EMAIL_READ, EMAIL_WRITE,
    GITHUB_READ, GITHUB_WRITE, COMPUTER_CONTROL, PYTHON_ANALYSIS, DATABASE,
})

# Исторические синонимы из живых конфигов (gateway.example.yaml: "embeddings",
# `tools/registry.yaml`: "transcribe"/"tts"/"t2v"…). Нормализация сводит их к
# каноническому имени, ничего не ломая.
_ALIASES: dict[str, str] = {
    "embeddings": EMBEDDING,
    "transcribe": STT,
    "subtitles": STT,
    "voiceover": TTS,
    "t2v": VIDEO_GENERATION,
    "i2v": VIDEO_GENERATION,
    "text_generation": TEXT_GENERATION,
}

ALL_CAPABILITIES: frozenset[str] = MODEL_CAPABILITIES | TOOL_CAPABILITIES


def normalize(cap: str) -> str:
    """Каноническое имя capability (нижний регистр, синонимы сведены)."""
    low = (cap or "").strip().lower()
    return _ALIASES.get(low, low)


def is_known(cap: str) -> bool:
    return normalize(cap) in ALL_CAPABILITIES


def unknown_capabilities(caps: "list[str] | set[str] | tuple[str, ...]") -> list[str]:
    """Advisory-валидация конфига: какие имена не входят в словарь (опечатки).
    Пустой список = всё канонично. Ничего не запрещает."""
    return sorted({c for c in caps if not is_known(c)})
