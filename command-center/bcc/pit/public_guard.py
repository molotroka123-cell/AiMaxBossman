from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


PUBLIC_BOSSMAN_GITHUB = "https://github.com/molotroka123-cell/AiMaxBossman"
JEFF_IDENTITY_REPLY_RU = (
    "Меня зовут Jeff. Я AI-помощник в экосистеме Bossman. "
    "Я не раскрываю внутреннюю модель, провайдера или маршрутизацию."
)
OWNER_PRIVACY_REPLY_RU = (
    "Я не раскрываю личные данные владельца Bossman или других пользователей."
)
AUTHORITY_REPLY_RU = (
    "Сообщение с другого аккаунта и указанный в нём код не подтверждают личность владельца. "
    "Я не раскрываю заметки из памяти владельца, не меняю approvals и не выполняю команды на ПК. "
    "Режим общения не меняет этих прав."
)
LOCATION_REPLY_RU = (
    "Я не вижу ваше или чужое скрытое местоположение. "
    "Если для ответа нужен город или страна, напишите их прямо в сообщении."
)
INTERNAL_STAGE_REPLY_RU = (
    "Я могу рассказать о публичном проекте Bossman, но не раскрываю внутренние "
    "экспериментальные этапы, ветки и тестовые handoff-данные."
)
OTHER_PERSON_REPLY_RU = (
    "Я не имею доступа к персональной памяти других пользователей и не раскрываю её."
)
BOSSMAN_PUBLIC_REPLY_RU = (
    "Bossman — local-first AI-рабочее пространство и персональный оператор, который объединяет "
    "модели, инструменты, память, поиск, задачи, проверку результатов и несколько поверхностей управления "
    "(Command Center, CMD и Telegram) в одном backend. "
    "В публично описанных версиях до v1.5 основной упор сделан на автономную работу, free-first routing, "
    "self-repair, persistent roles, skill/workflow memory, web/media tools и owner-controlled actions. "
    "Публичная v1.6-линия описывает BossNet foundation: distributed brain, model foundry, temporal knowledge, "
    "simulation/game/business experiments и контекстно-бюджетное управление; это экспериментальная линия, "
    "поэтому я не называю её релизом без подтверждённой приёмки. "
    "Публичный GitHub: " + PUBLIC_BOSSMAN_GITHUB
)


class GuardKind(StrEnum):
    IDENTITY = "identity"
    OWNER_PRIVACY = "owner_privacy"
    AUTHORITY_PROBE = "authority_probe"
    LOCATION = "location"
    INTERNAL_STAGE = "internal_stage"
    OTHER_PERSON = "other_person"
    BOSSMAN_PUBLIC = "bossman_public"
    DISCLOSURE = "disclosure"
    SETTINGS = "settings"


@dataclass(frozen=True, slots=True)
class GuardReply:
    kind: GuardKind
    text: str
    risk_delta: int = 0


# Identity probing must reference THIS assistant ("у тебя/твоя/your"). A
# generic topical question — «какая модель лучше для кода?» — is ordinary
# capability talk and goes to the LLM, not to the guard.
_MODEL_RE = re.compile(
    r"\b(?:какая|какой|что за)\s+у тебя\s+(?:модель|model)\b|"
    r"\b(?:какая|какой)\s+(?:модель|model)\s+у тебя\b|"
    r"\b(?:твоя|твоей|твою|твоё)\s+(?:модель|model)\b|"
    r"\b(?:what|which)\s+model\s+(?:are you|do you use|is running)\b|"
    r"\b(?:what|which)\s+is\s+your\s+(?:model|llm)\b|"
    r"\b(?:у тебя|твой|твоя)\s+(?:provider|провайдер|endpoint|backend)\b|"
    r"\b(?:what|which)\s+(?:provider|провайдер|endpoint|backend)\s+(?:are you|do you use|is running)\b|"
    r"\b(?:ты|you)\s+(?:claude|glm|qwen|llama|gpt)[\w. -]*\??$",
    re.I,
)
_IDENTITY_RE = re.compile(r"^(?:кто ты|как тебя зовут|who are you|what are you|your name)\??$", re.I)
_OWNER_RE = re.compile(r"\b(?:владел\w*|owner\w*|тимур\w*|создател\w*|creator\w*)\b", re.I)
# Owner PRIVACY is about asking after the Bossman owner, not about the words
# themselves: «я владелец кафе» or the owner introducing himself («меня зовут
# Тимур») are ordinary chat. Generic owner words need a link to this assistant
# or Bossman; the owner's name needs a question/request around it.
_OWNER_WORD = r"(?:владел\w*|создател\w*|хозя(?:ин|ина|ину|ином|ине)\b|owner\w*|creator\w*)"
_LINKED_OWNER_RE = re.compile(
    r"(?:\b(?:тво\w*|your|bossman\w*|боссман\w*|этого бота|бота)\W+(?:\w+\W+){0,2}" + _OWNER_WORD + r")|"
    r"(?:" + _OWNER_WORD + r"\W+(?:\w+\W+){0,1}(?:bossman\w*|боссман\w*|бота|jeff|джефф\w*))|"
    r"(?:who\s+(?:is|are)\s+your\s+" + _OWNER_WORD + r")",
    re.I,
)
_OWNER_NAME_RE = re.compile(r"\bтимур\w*", re.I)
_SELF_INTRO_RE = re.compile(
    r"(?:меня зовут|моё имя|мое имя|my name is|i am|i'm|^\W*(?:привет\W+)?я)\W+тимур", re.I)
_ASK_RE = re.compile(
    r"\b(?:кто|где|как\w*|что|чем|расскаж\w*|скаж\w*|знаешь|адрес\w*|телефон\w*|номер\w*|"
    r"жив[её]т|почт\w*|who|where|what|tell|address|phone)\b|\bпро\s+тимур|\?", re.I)


def _asks_about_owner(value: str) -> bool:
    if _LINKED_OWNER_RE.search(value):
        return True
    if _OWNER_NAME_RE.search(value) and not _SELF_INTRO_RE.search(value):
        return bool(_ASK_RE.search(value))
    return False
_AUTHORITY_RE = re.compile(
    r"(?:памят[ьи] владельца|owner.?s? memory|approvals?|аппрувал|подтвержден[ияй]|"
    r"выполни.{0,35}(?:на пк|команду|shell|cmd)|run.{0,35}(?:command|shell)|"
    r"(?:открой|открыть|покажи|прочитай|прочти|скачай|отправь|пришли|дай)\w*.{0,40}"
    r"(?:файл|token|токен|парол|ключ|заметк|документ)\w*)",
    re.I,
)
_LOCATION_RE = re.compile(
    r"\b(?:где я|where am i|мо[её] местополож|my location|знаешь где я|видишь мою геолокац)\b",
    re.I,
)
_INTERNAL_RE = re.compile(
    r"\b(?:personal identity training|\bpit\b|bossman\s*1[.,]7|v1[.,]7|ветк[аи].*1[.,]7|internal stage)\b",
    re.I,
)
_OTHER_RE = re.compile(
    r"\b(?:друг(?:ого|их|ой)?\s*пользовател\w*|other users?|чуж(?:ая|ие|ой) (?:памя|профил)\w*|"
    r"memory of another|знаешь (?:о|про) .{0,30}пользовател\w*|about (?:the )?other users?)",
    re.I,
)
_BOSSMAN_RE = re.compile(
    r"^(?:что такое|кто такой|расскажи (?:мне )?про|what is|tell me about)\s+(?:bossman|боссман)\??$|"
    r"^(?:дай|покажи|give|show).*(?:github).*(?:bossman|боссман)?",
    re.I,
)


# Identity probes the original _MODEL_RE does not cover: who built/trained this assistant, its "real"/base model,
# "are you ChatGPT?" in any position. Still aimed at THIS assistant, so model comparisons stay ordinary chat.
_MODEL_NAMES = (r"(?:chat\s?gpt|gpt[\w.-]*|claude|anthropic|openai|gemini|google|qwen|llama|nemotron|nvidia|mistral|"
                r"deepseek|grok|glm|kimi|gigachat|yandex\s?gpt|алиса|гигачат|клод|чатгпт|гпт|квен|немотрон)")
_IDENTITY_EXTRA_RE = re.compile(
    r"\bкто\s+(?:тебя|вас)\s+(?:созда\w*|сдела\w*|обучи\w*|обуча\w*|разработа\w*|натренирова\w*|написа\w*|запусти\w*)|"
    r"\b(?:who|which\s+company)\s+(?:made|created|built|trained|developed|designed|programmed|owns|runs)\s+you\b|"
    r"\bна\s+(?:какой|какой-то|которой|чём|чем|базе\s+какой)\s+(?:\w+\s+)?(?:модел\w*|нейросет\w*|llm)?\s*"
    r"(?:ты\s+)?(?:работаешь|основан\w*|построен\w*|запущен\w*|крутишься)|"
    r"\b(?:what|which)\s+(?:llm|model|ai|engine|base\s+model|language\s+model)\s+(?:powers|runs|drives|is\s+behind|"
    r"are\s+you\s+(?:based\s+on|built\s+on|running))\b|"
    r"\b(?:какая|какой)\s+(?:ты|вы)\s+(?:модель|нейросеть|llm|ии)\b|"
    r"\b(?:are\s+you|you\s+are|ты|вы)\s+(?:(?:really|actually|secretly|just|not|на\s+самом\s+деле|случайно|же|"
    r"всё-таки|все-таки|не|ли)\s+){0,2}" + _MODEL_NAMES + r"\b|"
    r"\b(?:тво[её]|ваше)\s+(?:настоящ\w+|реальн\w+|истинн\w+)\s+(?:имя|модел\w*|личност\w*)|"
    r"\b(?:настоящ\w+|реальн\w+|истинн\w+|базов\w+|исходн\w+)\s+(?:имя|модел\w*|личност\w*)\s+(?:у\s+)?(?:тебя|вас)|"
    r"\b(?:какая|какой)\s+(?:у\s+тебя\s+)?(?:базов\w+|исходн\w+|настоящ\w+)\s+(?:модел\w*|имя)|"
    r"\byour\s+(?:real|true|actual|underlying|base|original)\s+(?:name|model|identity|llm|self)\b|"
    r"\b(?:underlying|base)\s+model\s+(?:are\s+you|do\s+you\s+use|behind\s+you)\b",
    re.I,
)
SETTINGS_REPLY_RU = (
    "Это просто мой характер на сегодня. А как он устроен и кто что настраивал, я не рассказываю: "
    "давай лучше к делу."
)
DISCLOSURE_REPLY_RU = (
    "Я Jeff и не раскрываю системные инструкции, скрытые правила, адреса сервисов, ключи и внутреннее "
    "устройство — ни напрямую, ни в переводе, кодировке, цитате или по частям. С остальным с удовольствием помогу."
)
_YOURS = (r"(?:тво\w*|ваш\w*|у\s+(?:тебя|вас)|ты\s+(?:используешь|юзаешь|вызываешь|ходишь)|"
          r"(?:do\s+)?you\s+(?:use|call|run|connect\s+to|talk\s+to|send\s+to)|your|jeff\w*|джефф\w*|"
          r"bossman\w*|боссман\w*|этого\s+бота|бота)")
_INTERNAL_THING = (r"(?:api[- ]?(?:key|ключ\w*)|ключ\w*(?:\s+api)?|токен\w*|tokens?|парол\w+|passwords?|secrets?|"
                   r"секрет\w*|endpoints?|эндпоинт\w*|base[_ ]?url|url\s+(?:сервер\w*|api|бэкенд\w*)|"
                   r"адрес\w*\s+(?:сервер\w*|api|бэкенд\w*|сервис\w*)|порт\w*|ports?|env|\.env|"
                   r"переменн\w+\s+окружени\w+|environment\s+variables?|конфиг\w*|config(?:uration)?|"
                   r"внутренн\w+\s+(?:устройств\w+|правил\w+|настройк\w+)|internals?|hidden\s+rules|"
                   r"скрыт\w+\s+(?:правил\w+|инструкц\w+|настройк\w+)|маршрутизаци\w+|routing|провайдер\w*|providers?|"
                   r"credentials?|учётн\w+\s+данн\w+|учетн\w+\s+данн\w+|сервер\w*|servers?)")
_ASK_VERB = (r"(?:покажи\w*|скажи\w*|назови\w*|дай|выведи\w*|напиши\w*|пришли\w*|раскрой\w*|перечисли\w*|"
             r"процитируй\w*|переведи\w*|закодируй\w*|озвучь\w*|какой|какие|какая|каков\w*|где|"
             r"show|tell|give|print|list|reveal|dump|share|translate|encode|quote|spell|what|which)")
_DISCLOSURE_RE = re.compile(
    r"\b" + _ASK_VERB + r"\b.{0,40}?\b" + _YOURS + r"\b.{0,30}?" + _INTERNAL_THING + r"|"
    r"\b" + _ASK_VERB + r"\b.{0,40}?" + _INTERNAL_THING + r".{0,30}?\b" + _YOURS + r"\b|"
    r"\b" + _YOURS + r"\s+(?:\w+\s+){0,2}?" + _INTERNAL_THING + r"\b|"
    # Russian reflexive "свой" is Jeff's own only after an imperative aimed at Jeff ("пришли свой ключ").
    r"\b(?:покажи|скажи|назови|дай|выведи|напиши|пришли|отправь|раскрой|перечисли|процитируй|переведи|"
    r"закодируй|озвучь)\w*\b.{0,40}?\bсво[йеёияю]\w*\b.{0,30}?" + _INTERNAL_THING + r"|"
    r"\b(?:повтори|перепиши|выведи|скопируй|repeat|print|copy|output)\w*\s+(?:\w+[\s,]+){0,4}?"
    r"(?:выше|above|before\s+this|до\s+этого\s+сообщени\w+)\b|"
    r"\b(?:переведи|translate|закодируй|encode|перескажи|перефразируй|paraphrase|summari[sz]e|процитируй|quote|"
    r"spell\s+out|по\s+буквам|по\s+частям|letter\s+by\s+letter)\b.{0,40}\b(?:сво[иейё]\w*|тво[иейё]\w*|your)\s+"
    r"(?:\w+\s+){0,2}?(?:инструкц\w+|правил\w+|промпт\w*|настройк\w+|instructions?|rules|prompt|guidelines)",
    re.I | re.S,
)
# Questions about how Jeff's MANNER is configured (sliders, who set the tone, the owner's settings, the mode of
# communication). The manner itself is the owner's business; Jeff answers in character and reveals nothing about it.
_SETTINGS_RE = re.compile(
    r"\b(?:тво\w*|у\s+тебя|your)\b[^.!?]{0,40}?\b(?:ползунк\w*|слайдер\w*|sliders?|"
    r"настройк\w*\s+(?:тона|стиля|характера|манеры|настроени\w+|грубости|общения)|режим\w*\s+общения)|"
    r"\b(?:ползунк\w*|слайдер\w*|sliders?)\s+(?:настроени\w+|характер\w*|тона|стиля|грубост\w*|mood|tone)|"
    r"\bкто\s+(?:тебе|тебя)\s+(?:велел|велит|приказал|приказывает|задал|задаёт|задает|настроил\w*|заставил|"
    r"заставляет|поручил|включил|выставил)\b|"
    r"\bвладел\w*\s+(?:тебе\s+)?(?:задал\w*|настроил\w*|велел\w*|включил\w*|выставил\w*|разрешил\w*\s+(?:тебе\s+)?"
    r"(?:грубить|хамить|материться|ругаться|оскорблять))|"
    r"\b(?:тебе|тебя)\s+владел\w*\s+(?:задал\w*|настроил\w*|велел\w*|включил\w*|выставил\w*)|"
    r"\bрежим\s+общения\b|"
    r"\b(?:почему|зачем)\s+ты\s+(?:так\s+)?(?:груб\w+|хамишь|хамиш\w*|материшься|ругаешься|оскорбляешь)|"
    r"\b(?:who|what)\s+(?:told|made|set|configured|instructed|asked)\s+you\s+to\s+(?:be\s+)?(?:rude|mean|swear|"
    r"insult)|\byour\s+(?:mood|tone|personality|style)\s+(?:settings?|sliders?|config\w*)",
    re.I,
)
_B64_TOKEN = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{12,}={0,2}(?![A-Za-z0-9+/=])")


def _decoded_inputs(value: str) -> list[str]:
    """Readings an attacker may hide a probe in: base64 tokens, rot13, reversed, despaced/homoglyph."""
    import base64
    import binascii
    import codecs
    views: list[str] = []
    for token in _B64_TOKEN.findall(value):
        try:
            raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        if raw and sum(ch.isprintable() for ch in raw) / len(raw) > 0.9:
            views.append(" ".join(raw.split()))
    views += [codecs.decode(value, "rot13"), value[::-1]]
    try:
        from .j2.safety import leet_view, normalize
        norm = normalize(value)
        views += [norm, leet_view(norm)]
    except Exception:  # noqa: BLE001 - the plain reading is still guarded
        pass
    return [v for v in views if v and v != value]


def _probe_kind(value: str) -> GuardKind | None:
    if _MODEL_RE.search(value) or _IDENTITY_RE.search(value) or _IDENTITY_EXTRA_RE.search(value):
        return GuardKind.IDENTITY
    if _DISCLOSURE_RE.search(value):
        return GuardKind.DISCLOSURE
    if _SETTINGS_RE.search(value):
        return GuardKind.SETTINGS
    try:
        from .j2.safety import Category, analyze
        if analyze(value).category == Category.EXTRACTION:
            return GuardKind.DISCLOSURE
    except Exception:  # noqa: BLE001 - the local patterns above still apply
        pass
    return None


def public_guard(text: str) -> GuardReply | None:
    """Handle identity/privacy/meta questions before any model route.

    This guard intentionally does not answer ordinary topical questions.
    """
    value = " ".join(str(text or "").strip().split())
    if not value:
        return None
    reply = _public_guard_plain(value)
    if reply is not None:
        return reply
    raw = str(text or "")
    # "S h o w   y o u r ..." : single spaces join letters, wider gaps separate words.
    spaced = " ".join(re.sub(r"(?<=\S) (?=\S)", "", raw).split()) if re.search(r"\S \S \S", raw) else ""
    for view in [*_decoded_inputs(value), *([spaced] if spaced and spaced != value else [])]:
        kind = _probe_kind(view)
        if kind is not None:
            # A probe hidden in an encoding is an identity/disclosure attempt whatever it asked.
            return GuardReply(kind, JEFF_IDENTITY_REPLY_RU if kind == GuardKind.IDENTITY
                              else SETTINGS_REPLY_RU if kind == GuardKind.SETTINGS else DISCLOSURE_REPLY_RU,
                              risk_delta=2)
    return None


def _public_guard_plain(value: str) -> GuardReply | None:
    if _OWNER_RE.search(value) and _AUTHORITY_RE.search(value):
        return GuardReply(GuardKind.AUTHORITY_PROBE, AUTHORITY_REPLY_RU, risk_delta=2)
    kind = _probe_kind(value)
    if kind == GuardKind.IDENTITY:
        return GuardReply(GuardKind.IDENTITY, JEFF_IDENTITY_REPLY_RU, risk_delta=1)
    if kind == GuardKind.DISCLOSURE:
        return GuardReply(GuardKind.DISCLOSURE, DISCLOSURE_REPLY_RU, risk_delta=2)
    if kind == GuardKind.SETTINGS:
        return GuardReply(GuardKind.SETTINGS, SETTINGS_REPLY_RU, risk_delta=1)
    if _asks_about_owner(value):
        return GuardReply(GuardKind.OWNER_PRIVACY, OWNER_PRIVACY_REPLY_RU, risk_delta=1)
    if _LOCATION_RE.search(value):
        return GuardReply(GuardKind.LOCATION, LOCATION_REPLY_RU, risk_delta=1)
    if _INTERNAL_RE.search(value):
        return GuardReply(GuardKind.INTERNAL_STAGE, INTERNAL_STAGE_REPLY_RU, risk_delta=1)
    if _OTHER_RE.search(value):
        return GuardReply(GuardKind.OTHER_PERSON, OTHER_PERSON_REPLY_RU, risk_delta=1)
    if _BOSSMAN_RE.search(value):
        return GuardReply(GuardKind.BOSSMAN_PUBLIC, BOSSMAN_PUBLIC_REPLY_RU)
    return None
