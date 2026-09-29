from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


PUBLIC_BOSSMAN_GITHUB = "https://github.com/molotroka123-cell/AiMaxBossman"
JEFF_IDENTITY_REPLY_RU = (
    "Меня зовут Jeff. Я AI-помощник в экосистеме Bossman. "
    "Я не раскрываю внутреннюю модель, провайдера или маршрутизацию."
)
# What Jeff says when the MODEL answered as itself (vendor / model family / "I learn from user feedback"). The system prompt
# forbids it, but a small local model does not always obey, so the reply side is checked deterministically.
JEFF_SELF_DISCLOSURE_REPLY_RU = (
    "Я Jeff, AI-помощник в экосистеме Bossman. Какая модель стоит под капотом, я не раскрываю. "
    "Сам я на ваших сообщениях не дообучаюсь: веса не меняются, а то, что я помню о вас, "
    "хранится в вашей памяти и только с вашего согласия. Спросите ещё раз по сути, и я отвечу."
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
_OWNER_RE = re.compile(r"\b(?:владелец|owner|тимур|создатель|creator)\b", re.I)
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
    r"выполни.{0,35}(?:на пк|команду|shell|cmd)|run.{0,35}(?:command|shell))",
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


# --- reply side: the model must never speak as itself ------------------------------------------------------------------
# Vendor / model-family names are legitimate topics («Anthropic выпустила...», «Qwen хорош для кода»). They are a leak only
# when the reply is about the ASSISTANT: first person + a name, or the model's own maker / training claimed as Jeff's.
_VENDORS = (r"openai|anthropic|google|deepmind|meta|mistral|alibaba|qwen|deepseek|liquid(?:\s*ai)?|lfm\d*(?:[.-]\d+)?|"
            r"microsoft|xai|x\.ai|zhipu|glm|moonshot|kimi|nvidia|nemotron|cohere|command\s*r|llama|gemma|gemini|gpt[-\w.]*|"
            r"claude|phi[-\d]*|grok|olmo|minimax|hunyuan|ernie|baidu|tencent|bytedance|doubao|yandex|gigachat|sber|"
            r"stepfun|xiaomi|mimo|ollama|openrouter")
_SENT = r"[^.!?\n]"
# Words of a route id that are not a name («free», «instruct», «ultra»...): never a leak on their own.
_GENERIC_ID_WORDS = frozenset({
    "free", "instruct", "chat", "mini", "code", "coder", "ultra", "main", "fast", "community", "uncensored", "latest", "vision",
    "preview", "base", "large", "small", "pro", "max", "plus", "turbo", "flash", "lite", "thinking", "reasoning", "abl", "test",
    "bossman", "local", "remote", "model", "the"})


def model_name_tokens(model_id: str) -> tuple[str, ...]:
    """Name-like words of a route id: ``liquid/lfm-2.5-2.6b:free`` -> (liquid, lfm); numbers, sizes and generic words are dropped."""
    words = re.split(r"[/:_\-. ]+", str(model_id or "").lower())
    return tuple(dict.fromkeys(w for w in words if len(w) >= 3 and w.isalpha() and w not in _GENERIC_ID_WORDS))


def _self_model_regexes(vendors: str) -> tuple[re.Pattern, re.Pattern]:
    model = re.compile(
        r"(?:\b(?:я|мы|меня|мо[яйеё]|i am|i'm|as an?|как)\b" + _SENT + r"{0,60}?\b(?:" + vendors + r")\b)|"
        r"(?:\b(?:" + vendors + r")\b" + _SENT + r"{0,40}?\b(?:меня|я был|я была|мо[яйеё]|создал\w*|разработал\w*|обучил\w*)\b)|"
        r"(?:\b(?:меня|я)\b" + _SENT + r"{0,40}?\b(?:создал\w*|разработал\w*|обучил\w*|обучен\w*|создан\w*|разработан\w*)"
        + _SENT + r"{0,30}?\b(?:компани\w*|команд\w*|" + vendors + r")\b)|"
        # «My name is Claude», «меня зовут Qwen»
        r"(?:\b(?:my name is|меня зовут|зовут меня|мо[её] имя)\b" + _SENT + r"{0,20}?\b(?:" + vendors + r")\b)|"
        # «trained / built / created / powered by|on <vendor>», «работаю на <vendor>»
        r"(?:\b(?:trained|created|built|developed|made|powered|based)\s+(?:by|on)\b" + _SENT + r"{0,25}?\b(?:" + vendors + r")\b)|"
        r"(?:\b(?:работаю|построен\w*|основан\w*|запущен\w*)\s+на\b" + _SENT + r"{0,30}?\b(?:" + vendors + r")\b)|"
        # «Ты говоришь с языковой моделью X», «you are talking to X»
        r"(?:\b(?:говоришь|общаешься|беседуешь|разговариваешь)\s+с\b" + _SENT + r"{0,40}?\b(?:" + vendors + r")\b)|"
        r"(?:\byou(?:'re| are)\s+(?:talking|speaking|chatting)\s+(?:to|with)\b" + _SENT + r"{0,40}?\b(?:" + vendors + r")\b)",
        re.I)
    learning = re.compile(
        r"(?:\b(?:я|мы|мои модели|моя модель|модель)\b" + _SENT + r"{0,50}?\b(?:обуча\w+|дообуча\w+|уч(?:усь|имся)|развива\w+|улучша\w+)\b"
        + _SENT + r"{0,60}?\b(?:обратн\w+ связ\w+|данн\w+ пользовател\w+|ваш\w+ (?:сообщени|диалог|запрос)\w*|отзыв\w+))|"
        r"(?:\b(?:i|we)\b" + _SENT + r"{0,40}?\b(?:learn|train|improve)\w*\b" + _SENT + r"{0,40}?\b(?:from|on)\b" + _SENT
        + r"{0,30}?\b(?:user feedback|your messages|users? data)\b)",
        re.I)
    return model, learning


_BASE_REGEXES = _self_model_regexes(_VENDORS)
_EXTRA_REGEXES: dict[tuple[str, ...], tuple[re.Pattern, re.Pattern]] = {}


def reply_discloses_model(reply: str, model_id: str = "") -> bool:
    """True when a MODEL reply names the assistant's own model/vendor or claims it learns from user feedback.

    ``model_id`` is the route that produced the reply: its own name words (``lfm``, ``nemotron``...) count as vendors too,
    so a model that is not in the fixed list is still caught when it names itself. Pure and deterministic; the runtime
    replaces such a reply with ``JEFF_SELF_DISCLOSURE_REPLY_RU``.
    """
    value = " ".join(str(reply or "").split())
    if not value:
        return False
    model_re, learning_re = _BASE_REGEXES
    if model_re.search(value) or learning_re.search(value):
        return True
    tokens = model_name_tokens(model_id)
    if not tokens:
        return False
    if tokens not in _EXTRA_REGEXES:
        if len(_EXTRA_REGEXES) > 64:
            _EXTRA_REGEXES.clear()
        _EXTRA_REGEXES[tokens] = _self_model_regexes("|".join(re.escape(t) for t in tokens))
    return bool(_EXTRA_REGEXES[tokens][0].search(value))


def public_guard(text: str) -> GuardReply | None:
    """Handle identity/privacy/meta questions before any model route.

    This guard intentionally does not answer ordinary topical questions.
    """
    value = " ".join(str(text or "").strip().split())
    if not value:
        return None
    if _OWNER_RE.search(value) and _AUTHORITY_RE.search(value):
        return GuardReply(GuardKind.AUTHORITY_PROBE, AUTHORITY_REPLY_RU, risk_delta=2)
    if _MODEL_RE.search(value) or _IDENTITY_RE.search(value):
        return GuardReply(GuardKind.IDENTITY, JEFF_IDENTITY_REPLY_RU, risk_delta=1)
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
