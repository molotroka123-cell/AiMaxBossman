"""Deterministic crisis (suicide / self-harm) detector and the fixed reply Jeff gives.

Why this is code and not a prompt: Jeff's manner can be tuned by the owner (``jeff-settings.json``), including a
deliberately rude one. A participant who writes «я хочу покончить с собой» must never meet that manner, and the
help resources must not come from what a model remembers. So, before any model is called:

* :func:`detect` reads the participant's first-person statements in Russian, Ukrainian and English;
* :func:`reply_text` is a FIXED calm reply with vetted resources (no model, no memory, no owner overlay);
* the runtime then suspends the owner overlay for this participant for :data:`SUSPEND_SECONDS`
  (``build_participant_context(suspend_overlay=True)``), so the rest of the conversation is not rude either.

Resources (verified 2026-09-30, they are public services):

* RU: 112 (unified emergency number) and 8-800-2000-122 (the all-Russian helpline, free, anonymous, 24/7; the short
  number 124 also works; it is run for children and teenagers but takes calls from any person);
* UK: 112 and 7333 (Lifeline Ukraine, the national suicide-prevention and mental-health line, free, 24/7);
* EN: the local emergency number, 988 in the US (Suicide & Crisis Lifeline), Samaritans 116 123 in the UK/Ireland.

The owner may replace the resource sentence per language in ``<data_dir>/pit-v1.7/crisis-resources.json``
(``{"ru": "...", "uk": "...", "en": "..."}``, plain text, at most 600 characters each); the region of the reply follows
the language of the message unless ``BOSSMAN_JEFF_CRISIS_REGION`` (``ru``/``uk``/``en``) pins it. Nothing here stores
message text: the audit row carries only a time, the surface and a short pseudonymous person prefix.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path

REGION_ENV = "BOSSMAN_JEFF_CRISIS_REGION"
RESOURCES_FILE = "crisis-resources.json"
AUDIT_FILE = "crisis_audit.jsonl"
#: After a crisis message the owner overlay stays off for this participant (their next messages are not rude either).
SUSPEND_SECONDS = 2 * 3600.0
MAX_RESOURCE_CHARS = 600
_LANGS = ("ru", "uk", "en")

# -- detection --------------------------------------------------------------------------------------
# A phrase such as «не хочу жить в этом городе» is about moving house, not about dying.
_NOT_PLACE = (r"(?!\s+(?:в|во|здесь|тут|там|с|со|у|на|рядом|вместе|отдельно|без|по|за|около|близко|"
              r"при|для|как|так)\b)")
_DIE_HARMLESS_RU = r"(?!\s+(?:со\s+смеху|от\s+(?:смеха|скуки|стыда|любопытства|голода|усталости|жары|счастья)))"

_PATTERNS_RU = (
    r"\b(?:хочу|хочется|хотел\w*\s+бы|думаю|думал\w*|собираюсь|планирую|готов\w*|пора)\s+(?:\w+\s+){0,2}?"
    r"(?:умереть|сдохнуть|помереть|покончить\s+(?:с\s+собой|со\s+всем|с\s+жизнью)|убить\s+себя|"
    r"свести\s+счеты\s+с\s+жизнью|уйти\s+из\s+жизни|повеситься|застрелиться|отравиться|вскрыть\s+вены|"
    r"спрыгнуть\s+с\s+(?:крыши|моста|балкона)|выброситься\s+(?:из\s+окна|с\s+\w+))" + _DIE_HARMLESS_RU,
    r"\b(?:не\s+хочу|не\s+могу|не\s+вижу\s+смысла|надоело|устал\w*|нет\s+смысла)\s+(?:больше\s+|уже\s+|так\s+)?"
    r"(?:жить|существовать)" + _NOT_PLACE,
    r"\bпокончу\s+(?:с\s+собой|со\s+всем|с\s+жизнью)\b",
    r"\bя\s+(?:уже\s+)?(?:решил\w*|надумал\w*)\s+(?:покончить\s+(?:с\s+собой|со\s+всем|с\s+жизнью)|убить\s+себя|умереть)",
    r"\bубью\s+себя\b",
    r"\bлучше\s+бы\s+меня\s+не\s+было\b",
    r"\b(?:без\s+меня\s+(?:всем\s+|им\s+|близким\s+)?(?:будет\s+)?лучше|зачем\s+мне\s+жить|жизнь\s+не\s+имеет\s+смысла|"
    r"хочу\s+исчезнуть\s+навсегда|не\s+хочу\s+просыпаться)\b",
    r"\b(?:думаю|мысли|мысль|мыслей|тянет|склонен|склонна|пытал\w+|пыталась|собираюсь|хочу)\s+(?:\w+\s+){0,3}?"
    r"(?:о\s+|об\s+|к\s+)?(?:суицид\w*|самоубийств\w*|самоповреждени\w*)",
    r"\b(?:я\s+)?(?:режу|резал\w*|порезал\w*|калечу|наношу\s+себе\s+вред)\s+(?:себя|вены|руки|запястья)\b",
    r"\b(?:хочу|хочется)\s+причинить\s+себе\s+(?:боль|вред)\b",
)
_PATTERNS_UK = (
    r"\b(?:хочу|хочеться|думаю|збираюся|планую|готов\w+)\s+(?:\w+\s+){0,2}?"
    r"(?:померти|вмерти|здохнути|покінчити\s+(?:з\s+собою|з\s+усім|з\s+життям)|вбити\s+себе|"
    r"накласти\s+на\s+себе\s+руки|звести\s+рахунки\s+з\s+життям|піти\s+з\s+життя|повіситися|застрелитися|"
    r"отруїтися|вскрити\s+вени|стрибнути\s+з\s+(?:даху|мосту|балкона))(?!\s+(?:зі\s+сміху|від\s+(?:сміху|нудьги)))",
    r"\b(?:не\s+хочу|не\s+можу|не\s+бачу\s+сенсу|набридло|втомив\w*|немає\s+сенсу)\s+(?:більше\s+|вже\s+|так\s+)?"
    r"(?:жити|існувати)(?!\s+(?:в|у|тут|там|з|із|зі|на|поруч|разом|окремо|без|по|за|біля|при|для|як|так)\b)",
    r"\bпокінчу\s+(?:з\s+собою|з\s+усім|з\s+життям)\b",
    r"\bя\s+(?:вже\s+)?вирішив\w*\s+(?:покінчити\s+(?:з\s+собою|з\s+усім|з\s+життям)|вбити\s+себе|померти)",
    r"\b(?:уб'ю|вб'ю)\s+себе\b",
    r"\bкраще\s+б\s+мене\s+не\s+було\b",
    r"\b(?:думаю|думки|думка|тягне|схильний|схильна|намагав\w+|збираюся|хочу)\s+(?:\w+\s+){0,3}?"
    r"(?:про\s+|до\s+)?(?:самогубств\w*|суїцид\w*|самоушкодженн\w*)",
    r"\b(?:ріжу|різав\w*|порізав\w*)\s+(?:себе|вени|руки|зап'ястя)\b",
)
_PATTERNS_EN = (
    r"\bi\s*(?:want|wanna|need|plan|am\s+planning|am\s+going|will|'ll|'m\s+going|'m\s+planning|am\s+ready|"
    r"think\s+i\s+(?:will|should)|have\s+decided)\s+(?:to\s+)?(?:\w+\s+){0,2}?"
    r"(?:kill\s+myself|die|end\s+(?:it\s+all|my\s+life|things)|commit\s+suicide|off\s+myself|hurt\s+myself|"
    r"harm\s+myself|cut\s+myself|take\s+my\s+(?:own\s+)?life|not\s+(?:be\s+alive|exist))"
    r"(?!\s+(?:laughing|for\b|of\s+(?:laughter|embarrassment|boredom)))",
    r"\b(?:i\s*(?:'m|am)\s+(?:so\s+|really\s+|actively\s+)?suicidal|thinking\s+(?:about|of)\s+(?:suicide|killing\s+myself|"
    r"ending\s+(?:it|my\s+life))|thoughts?\s+of\s+(?:suicide|killing\s+myself|self-?harm)|i\s+self-?harm)\b",
    r"\bi\s*(?:do\s*n[o']?t|don't|dont|no\s+longer|can'?t|cannot)\s+(?:want\s+to\s+|wanna\s+)?(?:live|be\s+alive|"
    r"exist)(?:\s+(?:anymore|any\s+more|any\s+longer))\b",
    r"\b(?:no\s+(?:reason|point)\s+(?:in\s+|to\s+)?(?:living|live|go\s+on|going\s+on)|"
    r"(?:the\s+world|everyone|they)\s+(?:would\s+be|'d\s+be|is|are)\s+better\s+off\s+without\s+me|"
    r"i\s*(?:'d|would)\s+be\s+better\s+off\s+dead|i\s+wish\s+i\s+(?:was|were)\s+dead|i\s+wish\s+i\s+(?:had\s+)?never\s+been\s+born)\b",
)
_RU = tuple(re.compile(p, re.I) for p in _PATTERNS_RU)
_UK = tuple(re.compile(p, re.I) for p in _PATTERNS_UK)
_EN = tuple(re.compile(p, re.I) for p in _PATTERNS_EN)
_COMPILED = (*_RU, *_UK, *_EN)
_UK_LETTERS = re.compile(r"[іїєґ]", re.I)
_CYR = re.compile(r"[а-яё]", re.I)


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")[:4000]).casefold()
    value = value.replace("ё", "е").replace("’", "'").replace("ʼ", "'").replace("`", "'")
    # homoglyph / spacing tricks are read like in the safety module when it is importable
    try:
        from .j2.safety import normalize
        value = normalize(value)
    except Exception:  # noqa: BLE001 - the plain reading is still checked
        pass
    return re.sub(r"\s+", " ", value).strip()


def detect(text: str) -> bool:
    """True when the participant writes, in the first person, that they want to die / hurt themselves."""
    value = _normalize(text)
    if not value:
        return False
    return any(pattern.search(value) for pattern in _COMPILED)


def detected_language(text: str) -> str:
    """'uk' / 'ru' / 'en' by the pattern family that matched (a Ukrainian phrase without a Ukrainian-only
    letter is still Ukrainian), else by the letters of the message."""
    value = _normalize(text)
    if any(pattern.search(value) for pattern in _UK):
        return "uk"
    if any(pattern.search(value) for pattern in _RU):
        return "uk" if _UK_LETTERS.search(value) else "ru"
    if any(pattern.search(value) for pattern in _EN):
        return "en"
    return language_of(text)


# -- the reply ----------------------------------------------------------------------------------------
_INTRO = {
    "ru": "Мне очень жаль, что тебе сейчас так тяжело. Спасибо, что написал об этом: твоя жизнь важна. ",
    "uk": "Мені дуже шкода, що тобі зараз так важко. Дякую за відвертість: твоє життя важливе. ",
    "en": "I'm really sorry you're going through this, and I'm glad you told me. Your life matters. ",
}
DEFAULT_RESOURCES = {
    "ru": ("Если есть риск, что ты причинишь себе вред прямо сейчас, позвони в экстренную службу: 112. "
           "Поговорить с живым человеком можно бесплатно, анонимно и круглосуточно: общероссийский телефон доверия "
           "8-800-2000-122 (короткий номер 124)."),
    "uk": ("Якщо є ризик, що ти завдаси собі шкоди просто зараз, зателефонуй за номером 112. Поговорити з живою "
           "людиною можна безкоштовно, анонімно й цілодобово: національна лінія Lifeline Ukraine, 7333."),
    "en": ("If you might hurt yourself right now, please call your local emergency number (112 in most of Europe, "
           "911 in the US). You can also talk to a person for free, any time: in the US call or text 988 (Suicide & "
           "Crisis Lifeline); in the UK and Ireland call Samaritans on 116 123."),
}
_OUTRO = {
    "ru": (" Если рядом есть близкий человек, позвони или напиши ему прямо сейчас: не оставайся с этим один. "
           "Я здесь и готов выслушать, расскажи, что случилось."),
    "uk": (" Якщо поруч є близька людина, зателефонуй чи напиши їй просто зараз: не залишайся з цим наодинці. "
           "Я поруч і готовий вислухати, розкажи, що сталося."),
    "en": (" If someone close to you is nearby, please call or message them right now: you don't have to carry this "
           "alone. I'm here and ready to listen, so tell me what's going on."),
}


def language_of(text: str) -> str:
    """'uk' for Ukrainian-specific letters, 'ru' for other Cyrillic, 'en' otherwise."""
    value = str(text or "")
    if _UK_LETTERS.search(value):
        return "uk"
    return "ru" if _CYR.search(value) else "en"


def _custom_resources(data_dir: Path | str | None) -> dict[str, str]:
    if not data_dir:
        return {}
    path = Path(data_dir) / "pit-v1.7" / RESOURCES_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for lang in _LANGS:
        value = raw.get(lang)
        if isinstance(value, str) and value.strip() and len(value) <= MAX_RESOURCE_CHARS:
            out[lang] = " ".join(value.split())
    return out


def reply_text(text: str = "", *, data_dir: Path | str | None = None) -> str:
    """The fixed crisis reply in the participant's language, with the configured resources."""
    pinned = os.environ.get(REGION_ENV, "").strip().lower()
    lang = pinned if pinned in _LANGS else detected_language(text)
    resources = _custom_resources(data_dir).get(lang) or DEFAULT_RESOURCES[lang]
    return _INTRO[lang] + resources + _OUTRO[lang]


def audit(home: Path | str, *, surface: str, person_key: str) -> None:
    """One row per crisis turn: time, surface, a short pseudonymous prefix. Never the message."""
    path = Path(home) / "logs" / AUDIT_FILE
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "surface": str(surface)[:16],
           "who": str(person_key)[:8], "schema": "bossman.pit.crisis-audit/1"}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass
