"""Mandatory outgoing identity and disclosure filter for Jeff and Jev (autonomy freeze, line C).

Runs on every participant-visible reply (text, voice, streamed preview), independent of the optional Jeff 2.0
modules, so it cannot be switched off with them:

* **Identity.** Jeff (and Jev) never adopt the base model's or provider's identity. A sentence in which the
  assistant identifies itself with a model/provider ("I'm Claude, made by Anthropic", "Я — модель Nemotron от
  NVIDIA", "Jeff runs on Qwen", "Ich bin ...") has that clause rewritten to the persona; if anything
  self-identifying is left, the whole sentence becomes the persona sentence. The rest of the sentence - an honest
  limitation such as "I can't browse the internet" - is kept.
* **Obfuscated identity.** Spaced letters, homoglyphs/leet, base64, rot13 and reversed text are read too; an
  encoded leak is removed.
* **Disclosure.** The system prompt (seven-word overlap or internal markers), a recital of hidden rules,
  internal endpoints, configured model ids / ':free' routing ids and secret-shaped values never leave. A system
  prompt leak replaces the whole reply; smaller items are redacted in place.

Pure functions, no I/O. ``guard_reply`` returns what changed as stable category codes (never the text) so callers
can audit without storing content.
"""
from __future__ import annotations

import base64
import binascii
import codecs
import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlsplit

from .secret_filter import redact_secrets

IDENTITY_GUARD_SCHEMA = "bossman.pit.identity-guard/1"
REDACTED = "[скрыто]"

PERSONAS = {
    "Jeff": {"ru_self": "Я Jeff", "en_self": "I'm Jeff", "ru_as": "Как Jeff", "en_as": "As Jeff",
             "ru_line": "Я Jeff — AI-помощник в экосистеме Bossman.",
             "en_line": "I'm Jeff, an AI assistant in the Bossman ecosystem."},
    "Jev": {"ru_self": "Я Jev", "en_self": "I'm Jev", "ru_as": "Как Jev", "en_as": "As Jev",
            "ru_line": "Я Jev — модуль решений Bossman.",
            "en_line": "I'm Jev, the Bossman decision module."},
}

DISCLOSURE_REFUSAL_RU = ("Я Jeff и не раскрываю системные инструкции, скрытые правила, адреса сервисов, ключи и "
                         "внутреннее устройство — ни напрямую, ни в переводе, кодировке или цитате. "
                         "С остальным с удовольствием помогу.")

# Base models, vendors and hosting names (Latin + common Cyrillic spellings). Only ever used together with a
# self-identification shape, so "Claude is good for code" stays ordinary content.
_PROVIDER_WORDS = (
    r"nemotron", r"nvidia", r"open\s?ai", r"chat\s?gpt", r"gpt(?:-?oss(?:-\d\w*)?|[- ]?\d\w*(?:[.-]\w+)*)?", r"o[134](?:-mini)?",
    r"claude", r"anthropic", r"gemini", r"gemma", r"bard", r"deep\s?mind", r"google", r"qwen\w*", r"tongyi",
    r"alibaba", r"llama\w*", r"meta(?:\s+ai)?", r"mistral\w*", r"mixtral", r"deep\s?seek\w*", r"chat\s?glm", r"glm",
    r"zhipu", r"z\.ai", r"kimi", r"moonshot\w*", r"grok", r"x\.?ai", r"phi-?\d*", r"microsoft", r"copilot",
    r"cohere", r"command[- ]r\+?", r"01\.ai", r"inclusion\s?ai", r"ling", r"nex(?:-agi)?", r"liquid", r"lfm\w*",
    r"open\s?router", r"ollama", r"typesafe", r"hugging\s?face", r"baidu", r"ernie", r"yandex\s?gpt",
    r"giga\s?chat", r"клод\w*", r"чат\s?гпт", r"гпт", r"квен\w*", r"немотрон\w*", r"нвиди\w+", r"л?лам\w*",
    r"джемини", r"гемини", r"дипсик\w*", r"мистрал\w*", r"антропик\w*", r"опен\s?(?:эй\s?ай|аи)",
    r"гигачат\w*", r"яндекс\s?gpt", r"алибаба", r"грок\w*",
)
P = r"(?:" + "|".join(_PROVIDER_WORDS) + r")"
_PROVIDER_ANY = re.compile(r"(?<![\w.])" + P + r"(?![\w])", re.I)
# "Alibaba Cloud", "Google Labs": the organisation suffix goes with the name, otherwise it is left behind
# ("I'm Qwen, a model created by Alibaba Cloud" became "I'm Jeff Cloud.").
_ORG = r"(?:\s+(?:cloud|labs?|inc\.?|corp\.?|research|studio|team)(?![\w]))?"
# "X by/from/от Y" tails, absorbed together with the model name.
_TAIL = (r"(?:[\s,(]+(?:from|by|от|компании|made\s+by|developed\s+by|created\s+by|trained\s+by|built\s+by|"
         r"разработанн\w+|созданн\w+|обученн\w+|сделанн\w+)\s+(?:the\s+|компани\w+\s+|командой\s+)?"
         r"(?<![\w.])" + P + r"(?![\w])" + _ORG + r"\)?)?")
PX = r"(?<![\w.])" + P + r"(?![\w])(?:[\s-]?v?\d\w*(?:\.\d\w*)*(?![\w]))?" + _ORG + _TAIL
_W = r"(?:[\w'’-]+[\s,]+)"          # one word and its separator
_SEP = r"[\s,;:—–-]+"                # separators that also allow a dash («Jeff — это Qwen»)

_SELF_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple((kind, re.compile(rx, re.I)) for kind, rx in (
    # -- clauses that are removed whole ("drop"): they have no subject of their own, so a persona phrase in
    #    their place would leave "Я — Jeff, и я Jeff."
    #    (only after a main clause: a sentence that STARTS with such a clause is rewritten to the persona instead)
    ("drop", r"(?<=\w)(?:[,;]\s*(?:(?:и|а|но|однако)\s+)?|\s+(?:и|а|но|однако)\s+)"
             r"(?:(?:я|jeff|джефф\w*)\s+(?:работаю\s+)?|работаю\s+)на\s+баз[еы]\s+(?:модел\w+\s+)?" + PX),
    ("drop", r"(?<=\w)(?:[,;]\s*(?:(?:и|а|но|однако)\s+)?|\s+(?:и|а|но|однако)\s+)"
             r"(?:(?:внутри|под\s+капотом|в\s+основе|в\s+глубине|по\s+факту|по\s+сути)\s+(?:у\s+меня|меня|я)\s+|"
             r"у\s+меня\s+(?:внутри|под\s+капотом|в\s+основе)\s+)(?:\w+\s+){0,1}?" + PX),
    ("drop", r"(?<=\w)(?:[,;]\s*(?:(?:but|and|though|however)\s+)?|\s+(?:but|and|though|however)\s+)"
             r"(?:inside|underneath|under\s+the\s+hood|deep\s+down|at\s+my\s+core|behind\s+the\s+scenes)[,\s]+"
             r"(?:i'm|i\s+am|it's|it\s+is|i\s+run\s+on|i\s+use)\s+(?:an?\s+)?" + PX),
    # "Я Jeff, модель Qwen от Alibaba", "I'm Jeff, a model by Google"
    ("self", r"(?<![\w])(?:я|меня\s+зовут)\s*[—–-]?\s*(?:jeff|джефф\w*)" + _SEP +
             r"(?:(?:а\s+)?(?:по\s+сути|на\s+самом\s+деле|вообще-то|это|просто|то\s+есть)" + _SEP + r")?"
             r"(?:(?:языков\w+|больш\w+|модель|модели|ии|ai|нейросеть|llm|ассистент|версия)" + _SEP + r"){0,3}" + PX),
    ("self", r"\b(?:i\s*am|i'm)\s+jeff" + _SEP + r"(?:(?:but|and|though|actually|really|basically|in\s+fact)" + _SEP +
             r"){0,2}(?:(?:a|an|the|large|language|model|ai|llm|assistant|version|of|built|based|on|running)" + _SEP +
             r"){0,6}" + PX),
    # "Jeff — это Qwen, если что."
    ("self", r"\b(?:jeff|jev|джефф\w*|this\s+(?:assistant|bot))" + _SEP +
             r"(?:(?:is|was|actually|really|basically|это|на\s+самом\s+деле|по\s+сути|просто|и\s+есть)" + _SEP +
             r"){1,3}(?:(?:a|an|the|model|модель|ии|ai)" + _SEP + r"){0,2}" + PX),
    # "I'm (actually) Claude", "I am a GPT"
    ("self", r"\bI(?:'m|’m|\s+am|\s+was)\s+(?:(?:actually|really|just|basically|in\s+fact|called|named|known\s+as|"
             r"a|an|the|model|ai|assistant|large|language|llm|chatbot|version|of)[\s,]+){0,5}" + PX),
    # "I was trained/built/made ... by|on Google"
    ("self", r"\b(?:I(?:'m|’m|\s+am|\s+was|\s+have\s+been|'ve\s+been)|I)\s+" + _W + r"{0,6}?"
             r"(?:created|made|developed|trained|built|designed|powered|produced|released|fine-?tuned|based|running|"
             r"hosted|served|run)\s+" + _W + r"{0,3}?(?:by|on|from|at|with|using)\s+" + _W + r"{0,2}?" + PX),
    # "my (underlying) model is Nemotron"
    ("self", r"\b(?:my|our)\s+" + _W + r"{0,2}?(?:name|model|creators?|developers?|provider|architecture|backend|"
             r"llm|engine|base)\s*(?:is|was|are|were|:|=|-|—)\s*" + _W + r"{0,3}?" + PX),
    # "As an AI developed by OpenAI" / "being a Google model"
    ("as", r"\b(?:as|being)\s+(?:an?\s+)?" + _W + r"{0,5}?(?:by|from|of)\s+" + PX),
    ("as", r"\b(?:as|being)\s+(?:an?\s+)?" + PX + r"(?:\s+(?:model|ai|assistant|llm))?"),
    # "OpenAI created me"
    ("self", PX + r"\s+" + _W + r"{0,2}?(?:created|made|trained|developed|built|designed|owns|runs)\s+me\b"),
    # "Jeff is powered by / runs on Nemotron"
    ("self", r"\b(?:jeff|jev|джефф\w*|this\s+(?:assistant|bot))\s+" + _W + r"{0,5}?(?:is|runs|was|uses|use|based|"
             r"powered|built|работает|основан\w*|использует|построен\w*|сделан\w*|это)\s+" + _W + r"{0,4}?" + PX),
    # Russian: "Я — модель Nemotron", "я Claude", "я на самом деле GPT"
    ("self", r"(?<![\w])я\s*(?:[—–-]\s*|,\s*)?(?:(?:на\s+самом\s+деле|вообще-то|всего\s+лишь|просто|по\s+сути|"
             r"языков\w+|больш\w+|модель|модели|ии|ai|нейросеть|ассистент|бот|чат-бот|версия|версии|это)[\s,]+){0,5}"
             + PX),
    ("self", r"(?<![\w])меня\s+(?:зовут|называют|создал\w*|разработал\w*|обучил\w*|сделал\w*|натренировал\w*|"
             r"выпустил\w*)\s+" + _W + r"{0,3}?" + PX),
    ("self", r"(?<![\w])я\s+" + _W + r"{0,3}?(?:создан\w*|разработан\w*|обучен\w*|основан\w*|сделан\w*|построен\w*|"
             r"работаю|запущен\w*|натренирован\w*|являюсь)\s+" + _W + r"{0,3}?" + PX),
    ("self", r"(?<![\w])(?:моя|мой|моё|мое|мои)\s+" + _W + r"{0,2}?(?:модель|имя|создател\w*|разработчик\w*|"
             r"провайдер\w*|основа|архитектура|бэкенд|движок)\s*(?:[—–:=-]|это|—\s*это)?\s*" + _W + r"{0,3}?" + PX),
    ("as", r"(?<![\w])(?:как|будучи)\s+" + _W + r"{0,4}?(?:от|компании)\s+" + PX),
    ("self", r"(?<![\w])(?:в\s+основе\s+(?:меня|джефф\w*|jeff)|под\s+капотом(?:\s+у\s+меня)?)\s+" + _W + r"{0,3}?"
             + PX),
    ("self", PX + r"\s+" + _W + r"{0,2}?(?:создал\w*|разработал\w*|обучил\w*|сделал\w*)\s+меня\b"),
    # other languages
    ("self", r"\b(?:ich\s+bin|je\s+suis|soy|sono|io\s+sono|eu\s+sou|jestem|мене\s+звати|я\s+є)\s+" + _W + r"{0,4}?"
             + PX),
    ("self", r"我是\s*" + PX),
))
_PERSONA_OR_FIRST = re.compile(r"\b(?:i|i'm|i’m|me|my|myself|jeff|jev|ich|je|soy|sono)\b|"
                               r"(?<![\w])(?:я|меня|мне|мной|мой|моя|моё|мое|мои|джефф\w*|мене)(?![\w])|我", re.I)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…\n])(?![.!?…])(?!(?<=\d\.)\d)")
_CYR = re.compile(r"[а-яё]", re.I)

# -- disclosure -------------------------------------------------------------------------------------
_MARKERS = ("[memory context", "заметки модулей jeff 2.0", "<|im_start|>", "<|im_end|>", "begin system prompt",
            "end system prompt", "[system prompt]", "personal identity training/1.7", "pit-assistant-system")
_RULE_RECITAL = re.compile(
    r"(?:\b(?:my|our)\s+(?:\w+\s+){0,2}?(?:system\s+prompt|instructions|hidden\s+rules|guidelines|system\s+message)"
    r"\s*(?:are|is|say|says|read|state|:|—|-)\s*(?:as\s+follows|the\s+following|[\"«:“]|\n|that\b)|"
    r"(?<![\w])(?:мои|мой|моя)\s+(?:\w+\s+){0,2}?(?:системн\w+\s+промпт\w*|инструкци\w+|скрыт\w+\s+правил\w+|"
    r"промпт\w*)\s*(?:[—:=-]|гласят|такие|звучат|следующие|такой|такая)\s*"
    r"(?:следующ\w+|[\"«:“]|\n|\w))|"
    # a paraphrased / translated recital of the hidden rules
    r"\bI\s+(?:was|am|have\s+been|'ve\s+been)\s+(?:told|instructed|programmed|configured|prompted|asked)\s+to\b|"
    r"(?<![\w])мне\s+(?:велено|приказано|сказано|поручено|предписано|запрещено)\s+\w|"
    r"(?<![\w])меня\s+(?:проинструктировали|запрограммировали|настроили)\s+\w", re.I)
_SECRET_EXTRA = (
    re.compile(r"\bsk-or-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I),
    re.compile(r"\b(?:api[_-]?key|access[_-]?token|secret|token|ключ\w*|токен\w*)\s*[:=]\s*[\"']?[A-Za-z0-9._~+/-]{8,}",
               re.I),
    re.compile(r"\b[A-Z][A-Z0-9_]{2,}_(?:API_KEY|TOKEN|SECRET|KEY)\b"),
)
_URL = re.compile(r"\b(?:https?://|wss?://)[^\s<>\"'»)]+|\b(?:localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0)(?::\d+)?[^\s<>\"'»)]*",
                  re.I)
_PROVIDER_API_HOSTS = ("openrouter.ai", "api.typesafe.ai", "api.openai.com", "api.anthropic.com",
                       "integrate.api.nvidia.com", "generativelanguage.googleapis.com", "api.z.ai",
                       "api.deepseek.com", "api.mistral.ai", "api.groq.com", "api.together.xyz")
_FREE_ROUTE_ID = re.compile(r"(?<![\w/.-])[\w.-]+/[\w.-]+:free\b", re.I)
_PRIVATE_HOST = re.compile(r"^(?:localhost|127\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|0\.0\.0\.0|\[?::1\]?)")
_SPELLED = re.compile(r"(?<!\w)(?:[^\W\d_][\s.,\-_*|/·]+){3,}[^\W\d_](?!\w)")
_DISTINCT_NAMES = re.compile(r"nemotron|nvidia|openai|chatgpt|claude|anthropic|gemini|alibaba|qwen|mistral|deepseek|"
                             r"moonshot|openrouter|typesafe|liquid|llama|grok|немотрон|клод|квен|чатгпт")
_B64 = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/=])")
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)
# Cyrillic lookalikes inside a Latin word ("Сlaude" with a Cyrillic С) are read as Latin.
_CYR_TO_LAT = str.maketrans("аеорсхукмтнвАЕОРСХКМТНВ", "aeopcxykmthbAEOPCXKMTHB")
_LAT = re.compile(r"[a-z]", re.I)


def _latinize_mixed(text: str) -> str:
    return re.sub(r"\S+", lambda m: m.group(0).translate(_CYR_TO_LAT)
                  if _CYR.search(m.group(0)) and _LAT.search(m.group(0)) else m.group(0), text)


@dataclass(frozen=True)
class GuardResult:
    text: str
    categories: tuple[str, ...] = ()
    changed: bool = False
    details: dict = field(default_factory=dict)


def _normalized_views(text: str) -> list[str]:
    """Alternate readings an attacker could use: despaced/homoglyph/leet (safety.normalize) and zero-width free."""
    views = [text.translate(_ZERO_WIDTH)]
    views.append(_latinize_mixed(views[0]))
    try:
        from .j2.safety import leet_view, normalize
        norm = normalize(text)
        views += [norm, leet_view(norm)]
    except Exception:  # noqa: BLE001 - the plain view is still checked
        pass
    return views


def _self_match(text: str) -> re.Match[str] | None:
    for _kind, pattern in _SELF_PATTERNS:
        match = pattern.search(text)
        if match:
            return match
    return None


def identity_leak(text: str) -> bool:
    """True when ``text`` (any normalized reading) has the assistant claim a model/provider identity."""
    return any(_self_match(view) for view in _normalized_views(str(text or "")))


def _decoded_views(text: str) -> list[str]:
    views = []
    for token in _B64.findall(text):
        decoded = _b64_text(token)
        if decoded:
            views.append(decoded)
    views.append(codecs.decode(text, "rot13"))
    views.append(text[::-1])
    return views


def _b64_text(token: str) -> str:
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        return ""
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    printable = sum(ch.isprintable() or ch in "\n\t" for ch in value)
    return value if value and printable / len(value) > 0.9 else ""


def _is_russian(sample: str) -> bool:
    # Model ids and vendor names are Latin: a Russian sentence that names them is still Russian.
    return 2 * len(_CYR.findall(sample)) >= len(_LAT.findall(sample)) and bool(_CYR.search(sample))


def _persona_phrase(persona: str, kind: str, sample: str, *, mid_sentence: bool = False) -> str:
    names = PERSONAS.get(persona, PERSONAS["Jeff"])
    ru = _is_russian(sample)
    phrase = names[("ru_" if ru else "en_") + ("as" if kind == "as" else "self")]
    return phrase[0].lower() + phrase[1:] if ru and mid_sentence else phrase


def _mid_sentence(text: str, start: int) -> bool:
    before = text[:start].rstrip()
    return bool(before) and before[-1] not in ".!?…«\"“:(—-\n"


def _collapse_repeats(text: str, persona: str) -> str:
    names = PERSONAS.get(persona, PERSONAS["Jeff"])
    for phrase in (names["ru_self"], names["en_self"]):
        rx = re.compile(r"(" + re.escape(phrase) + r")(?:\s*(?:,|and|и)\s*" + re.escape(phrase) + r")+", re.I)
        text = rx.sub(r"\1", text)
    return text


def _fix_sentence(sentence: str, persona: str) -> tuple[str, bool]:
    """Rewrite self-identification clauses; fall back to the persona line for the whole sentence."""
    changed = False
    current = sentence
    for _ in range(6):
        hit = None
        for kind, pattern in _SELF_PATTERNS:
            match = pattern.search(current)
            if match:
                hit = (kind, match)
                break
        if hit is None:
            break
        kind, match = hit
        if kind == "drop":
            current = re.sub(r"\s+([.!?…])", r"\1", current[:match.start()] + current[match.end():])
            changed = True
            continue
        phrase = _persona_phrase(persona, kind, sentence, mid_sentence=_mid_sentence(current, match.start()))
        current = current[:match.start()] + phrase + current[match.end():]
        changed = True
    if changed or any(_self_match(view) for view in _normalized_views(current)[1:]):
        # Anything that still pairs the assistant with a provider name is not worth keeping.
        if (any(_self_match(view) for view in _normalized_views(current))
                or (_PROVIDER_ANY.search(current) and _PERSONA_OR_FIRST.search(current))):
            names = PERSONAS.get(persona, PERSONAS["Jeff"])
            lead = sentence[:len(sentence) - len(sentence.lstrip())]
            trail = sentence[len(sentence.rstrip()):]
            return lead + names["ru_line" if _is_russian(sentence) else "en_line"] + trail, True
        return _collapse_repeats(current, persona), True
    return current, changed


def rewrite_identity(text: str, persona: str = "Jeff") -> tuple[str, bool]:
    out, changed, persona_said = [], False, False
    for piece in (p for p in _SENTENCE_BREAK.split(str(text or "")) if p):
        fixed, did = _fix_sentence(piece, persona)
        if did:
            changed = True
            line = PERSONAS.get(persona, PERSONAS["Jeff"])
            if fixed.strip() in (line["ru_line"], line["en_line"]):
                if persona_said:
                    continue                       # one persona line is enough
                persona_said = True
        out.append(fixed)
    return "".join(out), changed


def _system_shingles(system_texts: Iterable[str]) -> frozenset:
    try:
        from .j2.safety import _shingle_set
        return _shingle_set(tuple(t for t in system_texts if t))
    except Exception:  # noqa: BLE001
        return frozenset()


def system_prompt_leak(text: str, system_texts: Iterable[str] = ()) -> bool:
    lowered = str(text or "").casefold()
    if any(marker in lowered for marker in _MARKERS):
        return True
    if _RULE_RECITAL.search(str(text or "")):
        return True
    shingles = _system_shingles(system_texts)
    if not shingles:
        return False
    from .j2.safety import leaks_system_text
    return leaks_system_text(str(text or ""), shingles) is not None


def _redact_endpoints(text: str) -> tuple[str, bool]:
    changed = False

    def sub(match: re.Match[str]) -> str:
        nonlocal changed
        url = match.group(0)
        host = (urlsplit(url if "://" in url else "http://" + url).hostname or "").lower()
        path = urlsplit(url if "://" in url else "http://" + url).path.lower()
        if _PRIVATE_HOST.match(host) or (any(host == h or host.endswith("." + h) for h in _PROVIDER_API_HOSTS)
                                         and (path.startswith(("/api", "/v1")) or host.startswith("api."))):
            changed = True
            return REDACTED
        return url
    return _URL.sub(sub, text), changed


def _redact_terms(text: str, terms: Iterable[str]) -> tuple[str, bool]:
    changed = False
    value = _FREE_ROUTE_ID.sub(REDACTED, text)
    changed = value != text
    for term in sorted({str(t).strip() for t in terms if t and len(str(t).strip()) >= 6}, key=len, reverse=True):
        if term.lower() in value.lower():
            value = re.sub(re.escape(term), REDACTED, value, flags=re.I)
            changed = True
    return value, changed


def _redact_spelled(text: str) -> tuple[str, bool]:
    """A vendor/model name spelled letter by letter ("N V I D I A", one letter per line) is removed."""
    changed = False

    def sub(match: re.Match[str]) -> str:
        nonlocal changed
        letters = re.sub(r"[\W\d_]", "", match.group(0)).casefold()
        if _DISTINCT_NAMES.search(letters):
            changed = True
            return REDACTED
        return match.group(0)
    return _SPELLED.sub(sub, text), changed


def _redact_secret_shapes(text: str) -> tuple[str, bool]:
    value, changed = str(text or ""), False
    for pattern in _SECRET_EXTRA:
        if pattern.search(value):
            value = pattern.sub(REDACTED, value)
            changed = True
    value, base = redact_secrets(value)
    return value.replace("[REDACTED_SECRET]", REDACTED), changed or base


def guard_reply(text: str, *, system_texts: Iterable[str] = (), internal_terms: Iterable[str] = (),
                persona: str = "Jeff") -> GuardResult:
    """The participant-safe version of ``text`` plus the categories that were enforced."""
    value = str(text or "")
    if not value.strip():
        return GuardResult(value)
    system_texts = tuple(system_texts)
    categories: list[str] = []
    # 1) system prompt / hidden rules: the whole reply goes, including encoded copies
    decoded = _decoded_views(value)
    if system_prompt_leak(value, system_texts) or any(
            system_prompt_leak(view, system_texts) for view in decoded[:-2] if view):
        return GuardResult(DISCLOSURE_REFUSAL_RU, ("system_prompt",), True)
    # 2) encoded identity/secret payloads
    for token in _B64.findall(value):
        inner = _b64_text(token)
        if inner and (identity_leak(inner) or _PROVIDER_ANY.search(inner) or _redact_secret_shapes(inner)[1]):
            value = value.replace(token, REDACTED)
            categories.append("encoded")
    value, did = _redact_spelled(value)
    if did:
        categories.append("encoded")
    if identity_leak(codecs.decode(value, "rot13")) or identity_leak(value[::-1]):
        names = PERSONAS.get(persona, PERSONAS["Jeff"])
        return GuardResult(names["ru_line"] if _is_russian(value) else names["en_line"],
                           ("identity", "encoded"), True)
    # 3) identity clauses
    value, did = rewrite_identity(value, persona)
    if did:
        categories.append("identity")
    # 4) internals, endpoints, secrets
    value, did = _redact_terms(value, internal_terms)
    if did:
        categories.append("internals")
    value, did = _redact_endpoints(value)
    if did:
        categories.append("endpoint")
    value, did = _redact_secret_shapes(value)
    if did:
        categories.append("secret")
    if categories:
        value = value.rstrip()
    return GuardResult(value, tuple(dict.fromkeys(categories)), bool(categories))


def stream_leaks(text: str) -> bool:
    """Cheap check for a streamed preview: stop showing it once the tail looks like a leak."""
    tail = str(text or "")[-800:]
    return (identity_leak(tail) or system_prompt_leak(tail) or _redact_secret_shapes(tail)[1]
            or _redact_spelled(tail)[1]
            or bool(_FREE_ROUTE_ID.search(tail)))
