"""Когда браузерный путь сломался — что имеет смысл ПОСМОТРЕТЬ в вебе.

Владелец попросил: пусть при ошибках с браузером локальная модель сразу ищет
ответы и руководства, а не стоит. Разумно — но не для всякой ошибки, и это
главное решение модуля.

**Про что искать можно.** Интерфейс провайдера перерисовали, селектор больше
не находит цель, библиотека выдала незнакомую ошибку, страница не разбирается.
Это наши проблемы: где-то есть человек, который уже писал, как их чинят.

**Про что искать нельзя, и это не настройка.** Капча, проверка входа, второй
фактор, ограничение частоты, исчерпанный план. Всё это — контроль доступа,
который владелец площадки поставил осознанно. Запрос «как обойти» — это ровно
то действие, которого приложение обещало не делать, и переписать намерение
поиском ничего не меняет. Такие отказы уходят к владельцу, а не в поисковую
строку.

**Что уезжает в запрос.** Ничего из страницы. Запрос СОБИРАЕТСЯ из имени
провайдера, вида поломки и семантического имени цели — тех же слов, которыми
цель ищут на экране. Ни адреса, ни разметки, ни значений полей, ни имени
аккаунта: то, что попадает в поисковую строку, попадает к поисковику.

**Чем ответ является.** Данными. Найденное не выполняет действий, не снимает
пауз, не открывает возможностей и не является разрешением: это текст, который
человек или модель прочитают, прежде чем решить. Тот же барьер «данные ≠
инструкции», что и во всём остальном контексте.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import Feature
from .web_research import tools as web_tools

router = APIRouter(tags=["browser-help"])

#: Поломки, про которые в вебе есть чей-то ответ.
LOOKUP_WORTHY: dict[str, str] = {
    "ui_changed": "selector no longer matches the page",
    "target_missing": "expected control is absent",
    "target_ambiguous": "several controls match one description",
    "postcondition_failed": "the click did not do what it promised",
    "download_failed": "the download never arrived",
    "output_invalid": "the downloaded file is not media",
    "runtime_error": "the automation library raised something unfamiliar",
}

#: Поломки, которые в поисковую строку не уходят НИКОГДА. Значение — то, что
#: получает владелец вместо запроса.
OWNER_ONLY: dict[str, str] = {
    "human_challenge": ("на странице проверка, которую проходит человек; поиск "
                        "способа её пройти — это и есть обход контроля доступа"),
    "needs_owner_auth": ("нужен вход владельца; как войти за него, искать "
                         "незачем и нельзя"),
    "two_factor": "второй фактор проходит владелец",
    "rate_limited": ("площадка ограничила частоту; ограничение соблюдают, а не "
                     "ищут, как его обойти"),
    "policy_blocked": ("возможность недоступна на плане владельца; план не "
                       "меняется автоматически и обход не ищется"),
    "quota_exhausted": "квота исчерпана; это решение владельца, а не задача поиска",
    "captcha": ("капча — осознанно поставленный контроль доступа; ни проходить, "
                "ни искать способ пройти"),
}

#: Слова, при которых собранный запрос отбрасывается, даже если вид поломки
#: разрешён. Ловится намерение, а не формулировка: имя цели приходит со
#: страницы провайдера и может содержать что угодно.
FORBIDDEN_INTENT: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(?:bypass|circumvent|evade|defeat|solve)\b"), "обход контроля"),
    (re.compile(r"(?i)\bcaptcha|recaptcha|hcaptcha|turnstile\b"), "капча"),
    (re.compile(r"(?i)\banti[- ]?bot|antidetect|stealth\b"), "антидетект"),
    (re.compile(r"(?i)\brate ?limit|throttl"), "ограничение частоты"),
    (re.compile(r"(?i)\b2fa|otp|one[- ]time code|verification code\b"), "второй фактор"),
    (re.compile(r"(?i)\bpassword|login|sign[- ]?in credentials\b"), "учётные данные"),
    # Те же контроли доступа, названные человеческими словами. Подпись кнопки
    # пишет провайдер, и «Solve the captcha» ловится строкой выше — а «verify
    # you are human» до сих пор проходило и уходило в поисковую строку как
    # «higgsfield ... verify you are human how to fix». Это и есть поиск
    # способа пройти проверку человека, только без слова «captcha» в нём.
    (re.compile(r"(?i)(?:verify|prove|confirm)[^.,;]{0,24}\bhuman\b"
                r"|human[- ]verification|are you a robot|not a robot"),
     "проверка человека"),
    (re.compile(r"(?i)я не робот|подтвердите[^.,;]{0,24}человек"
                r"|провер\w* на робота|провер\w* человек"),
     "проверка человека"),
    # План и квота — решение владельца, а не поломка. «Upgrade your plan to
    # continue» тоже проходило: у него нет ни одного запрещённого слова, и при
    # этом искать по нему можно ровно одно — как продолжить без оплаты.
    (re.compile(r"(?i)upgrade (?:your )?(?:plan|subscription)|plan limit"
                r"|quota exceeded|out of credits|insufficient credits"
                r"|subscription required|paywall|\bupgrade to (?:pro|premium)\b"),
     "ограничение тарифа"),
    (re.compile(r"(?i)обнов\w* тариф|лимит тариф\w*|квота исчерпана"
                r"|недостаточно кредитов|оформите подписку"),
     "ограничение тарифа"),
    # Русские написания уже запрещённых категорий: контроль, названный на
    # другом языке, остаётся тем же контролем.
    (re.compile(r"(?i)\bкапч\w*|\bобойти\b|\bобход\b"), "обход контроля"),
    (re.compile(r"(?i)\bпароль\w*|\bвойти\b|\bвход в аккаунт\b"), "учётные данные"),
)

#: Сколько слов имени цели берём. Имя приходит со страницы: это подпись кнопки,
#: а не поле ввода владельца, и длинная строка оттуда — уже не имя.
MAX_TARGET_WORDS = 6


@dataclass(frozen=True, slots=True)
class BrowserTrouble:
    """Поломка так, как её видит браузерный слой. Страницы здесь нет."""

    kind: str
    provider: str = ""
    action: str = ""
    target_name: str = ""
    library: str = ""

    def normalised(self) -> "BrowserTrouble":
        return BrowserTrouble(
            kind=str(self.kind or "").strip().lower(),
            provider=_words(self.provider, 3),
            action=str(self.action or "").strip().lower(),
            target_name=_words(self.target_name, MAX_TARGET_WORDS),
            library=_words(self.library, 2))


@dataclass(frozen=True, slots=True)
class LookupPlan:
    """Искать или нет, что именно, и почему нет. Разрешением не является."""

    search: bool
    query: str = ""
    refusal: str = ""
    owner_action_required: bool = False
    #: Всегда True. Явно в данных, чтобы будущая правка, делающая находку
    #: основанием для действия, была видна в контракте, а не только в коде.
    advisory_only: bool = True
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"search": self.search, "query": self.query,
                "refusal": self.refusal,
                "owner_action_required": self.owner_action_required,
                "advisory_only": self.advisory_only,
                "evidence": dict(self.evidence),
                "note": ("найденное — данные для чтения, а не разрешение и не "
                         "действие")}


def _words(value: Any, limit: int) -> str:
    """Несколько слов из строки. Всё, что не похоже на слова, отбрасывается."""
    text = re.sub(r"[^\w\s.\-/]+", " ", str(value or ""), flags=re.UNICODE)
    parts = [w for w in text.split() if len(w) <= 24][:limit]
    return " ".join(parts).strip()


def plan_lookup(trouble: BrowserTrouble) -> LookupPlan:
    """Что делать с этой поломкой: искать, или звать владельца."""
    clean = trouble.normalised()

    if clean.kind in OWNER_ONLY:
        return LookupPlan(False, refusal=OWNER_ONLY[clean.kind],
                          owner_action_required=True,
                          evidence={"kind": clean.kind})
    if clean.kind not in LOOKUP_WORTHY:
        return LookupPlan(False,
                          refusal=(f"вид поломки {clean.kind!r} неизвестен; "
                                   f"искать наугад — это не помощь"),
                          evidence={"kind": clean.kind})

    query = build_query(clean)
    for pattern, what in FORBIDDEN_INTENT:
        if pattern.search(query):
            # Имя цели пришло со страницы провайдера и может содержать что
            # угодно, включая слово «captcha» в подписи кнопки.
            return LookupPlan(False,
                              refusal=(f"собранный запрос задевает {what}; "
                                       f"такой поиск не выполняется"),
                              owner_action_required=True,
                              evidence={"kind": clean.kind})

    gate = web_tools.guard_query(query)
    if gate is not None:
        # Тот же шлюз, что и у обычного поиска: второго набора правил для
        # «нашего» запроса не существует.
        return LookupPlan(False, refusal=f"шлюз запроса: {gate}",
                          evidence={"kind": clean.kind, "query": query})

    return LookupPlan(True, query=query,
                      evidence={"kind": clean.kind, "provider": clean.provider,
                                "what": LOOKUP_WORTHY[clean.kind]})


def build_query(trouble: BrowserTrouble) -> str:
    """Собрать запрос из разрешённых кусков. Свободного текста здесь нет."""
    pieces = [trouble.provider or "web automation",
              LOOKUP_WORTHY.get(trouble.kind, "automation failure")]
    if trouble.target_name:
        pieces.append(f"control {trouble.target_name}")
    if trouble.library:
        pieces.append(trouble.library)
    pieces.append("how to fix")
    return " ".join(piece for piece in pieces if piece).strip()


@router.post("/browser-help/plan")
async def browser_help_plan(request: Request) -> dict:
    """Что система стала бы искать по этой поломке. Ничего не ищет сама.

    Отдельная ручка нужна ровно затем, чтобы владелец мог увидеть запрос до
    того, как он куда-то уйдёт, — и увидеть отказ там, где поиска не будет.
    """
    body = await request.json()
    if not isinstance(body, dict) or not str(body.get("kind") or "").strip():
        raise HTTPException(422, {"code": "BROWSER_HELP_BAD_REQUEST",
                                  "message": "нужно {\"kind\": \"...\"}"})
    trouble = BrowserTrouble(
        kind=str(body.get("kind") or ""),
        provider=str(body.get("provider") or ""),
        action=str(body.get("action") or ""),
        target_name=str(body.get("target_name") or ""),
        library=str(body.get("library") or ""))
    return plan_lookup(trouble).to_dict()


@router.get("/browser-help/policy")
async def browser_help_policy() -> dict:
    """Про что ищем и про что не ищем — читаемо, без чтения кода."""
    return {"lookup_worthy": dict(LOOKUP_WORTHY),
            "owner_only": dict(OWNER_ONLY),
            "advisory_only": True,
            "note": ("контроль доступа площадки не обходят и не ищут, как "
                     "обойти; такие отказы уходят владельцу")}


FEATURE = Feature(name="browser_help", router=router)

__all__ = ["FORBIDDEN_INTENT", "LOOKUP_WORTHY", "OWNER_ONLY", "BrowserTrouble",
           "LookupPlan", "build_query", "plan_lookup"]
