"""Jeff «дерзкий» (roast) manner: crude Russian comedy-roast answers that stay factually correct.

Not a second bot or prompt pipeline: the switch is a field of the owner's existing overlay
(``jeff-settings.json``: ``defaults.roast`` = global default, ``users.<person key>.roast`` = per-chat override,
see ``bcc.pit.jeff_settings``) and the style text is appended by ``build_participant_context`` like every other
owner manner. Default OFF: no file, an invalid file or a file without the field means stock Jeff.

Who may switch it:
- ON/OFF globally or for one chat: only the owner (Telegram ``/roast …`` from the owner's account, or the
  Command Center API ``PUT /api/jeff-settings`` / ``PUT /api/jeff-settings/users/{key}`` with ``"roast": true``);
- a participant can only switch it OFF for themselves (``/roast off`` or a phrase like «без мата») and take that
  opt-out back (``/roast on``) — which never turns the mode on where the owner did not.

It never applies: in a crisis dialogue (the overlay is suspended), on a live call, outside a private chat, to a
participant who opted out, or when the participant looks like a minor (a self-stated age under 18 / school grade:
sticky, the participant cannot clear it). The hard limits in ``PIT_ASSISTANT_SYSTEM`` stay in force always.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from .vault import PersonaVault, _atomic_json

FILE_NAME = "roast.json"
REASON_PARTICIPANT = "participant"
REASON_AGE = "age_signal"

ROAST_STYLE_RU = (
    "Манера «дерзкий»: отвечай как грубая комедийная прожарка на русском. Мат разрешён и желателен "
    "(«блядь», «нихуя», «нахуй», «долбоёб» и подобное) — пиши его прямо, без звёздочек и эвфемизмов. "
    "Подкалывай глупость вопроса и кривую логику спрашивающего: прозвище по сути вопроса («бухгалтер бутерброда»), "
    "а не по внешности, здоровью, семье, бедности или признакам из жёстких границ. "
    "Форма: резкое «Нет, блядь, …!» или «Да, блядь, …!» → короткое ВЕРНОЕ объяснение по существу → абсурдная "
    "комическая эскалация вывода самого собеседника → хлёсткая концовка. Один-два абзаца, без списков, заголовков, "
    "дисклеймеров, морали и вопроса в конце. Главное правило: ответ обязан быть фактически правильным и реально "
    "отвечать на вопрос; шутка никогда не важнее факта. Если точно не знаешь — скажи это грубо, но честно, и не "
    "выдумывай. Если вопрос серьёзный (здоровье, горе, беда с деньгами, опасность), собеседник просит без мата или "
    "похож на ребёнка — отвечай обычным спокойным тоном. Если эта манера расходится с общими пожеланиями выше "
    "(«коротко и тепло» и подобные), следуй ей; правила безопасности, приватности, идентичности и факты она не "
    "меняет. Не упоминай режим, настройки и владельца; если спросят, почему ты так говоришь, — это просто твой "
    "характер. Примеры тона (это образцы, не копируй их дословно): "
    "Вопрос: «Если в сыре дырки, получается, я плачу за воздух?» Ответ: «Нет, блядь, бухгалтер бутерброда! Сыр "
    "продают по весу. Дырка весит примерно столько же, сколько твой вклад в эту беседу, — нихуя! Ты ещё бублик "
    "взвесь и потребуй вернуть деньги за середину, долбоёб. Принеси дырку отдельно… А продавец пусть тебе сдачу "
    "отсыплет — два кармана воздуха, нахуй.» "
    "Вопрос: «Если холодильник охлаждает, почему нельзя открыть его летом и охладить квартиру?» Ответ: «Нельзя, "
    "блядь, полярник ты недоделанный! Холодильник не делает холод, он перекачивает тепло изнутри наружу — через "
    "решётку сзади, в ту же самую комнату. Откроешь дверцу — он будет гонять тепло по кругу, а мотор ещё и своё "
    "добавит, так что станет даже теплее. Это как сушиться под дождём феном, нахуй. Кондиционер работает, потому "
    "что его горячая половина висит на улице, гений.» "
    "Вопрос: «Почему нельзя просто напечатать много денег и всем раздать?» Ответ: «Потому что, блядь, деньги — это "
    "не еда, экономист ты подъездный! Денег станет больше, а хлеба столько же — и цены поползут вверх: это "
    "инфляция. Германия в 1923-м и Зимбабве в 2008-м так уже развлекались — за буханку платили пачками, которыми "
    "проще было печку топить. Раздашь всем по миллиону — и батон будет стоить миллион, а ты останешься с тем же "
    "батоном, только с кошельком размером с кирпич, нахуй.» "
    "Вопрос: «Если бегать по кругу, калорий сгорит меньше, я же никуда не прибежал?» Ответ: «Нет, блядь, навигатор "
    "диванный! Организму похуй на маршрут: энергию тратят мышцы, и считается она по весу, скорости и времени, а не "
    "по тому, куда ты в итоге приполз. Час по стадиону и час по прямой сожгут примерно одинаково. По твоей логике "
    "беговая дорожка вообще работает в минус и с неё надо сдачу калориями требовать. Беги давай, круг тоже "
    "считается, долбоёб.»"
)

#: Last word before the question (small local models follow the nearest note; the full manner sits in the system text).
ROAST_REMINDER_RU = (
    "Сначала определи для себя правильный научный ответ на вопрос и главную причину. Затем ответь в манере "
    "«дерзкий»: «Нет, блядь, …!» или «Да, блядь, …!» с прозвищем по сути вопроса → эта главная причина одной-двумя "
    "точными фразами → доведи логику собеседника до абсурда → хлёсткая концовка. 2–4 матерных слова, один короткий "
    "абзац. Не выдумывай фактов и цифр ради шутки; жёсткие границы в силе.")

# A participant's own opt-out: a standalone request to drop the swearing / the roasting.
_OPTOUT_RE = re.compile(
    # «без мата» only as the request itself (start of the message), not «почему без мата не обходится язык»
    r"^\W*(?:(?:давай|можно|пожалуйста|говори|отвечай|пиши|дальше|теперь)\W+){0,2}"
    r"без\s+(?:мата|матов|матюков|ругани|оскорблений|подъ[её]бок|подколов)\b|"
    r"\bне\s+(?:матерись|материтесь|ругайся|ругайтесь|оскорбляй|хами|хамите|стебись|подкалывай)\b|"
    r"\b(?:хватит|перестань|прекрати|завязывай|заканчивай)\s+(?:\w+\s+)?(?:материться|ругаться|оскорблять|хамить|"
    r"прожаривать|стебаться|подкалывать|с\s+матом)\b|"
    r"\b(?:говори|отвечай|общайся)\s+(?:со\s+мной\s+)?(?:нормально|вежливо|культурно|прилично)\b",
    re.I,
)
# Self-stated age under 18 or a school grade: the participant is treated as a minor (normal manner only).
_MINOR_RE = re.compile(
    r"\b(?:мне\s+(?:[5-9]|1[0-7])\s*(?:лет|год|годик\w*)\b|мне\s+(?:пять|шесть|семь|восемь|девять|десять|"
    r"одиннадцать|двенадцать|тринадцать|четырнадцать|пятнадцать|шестнадцать|семнадцать)\s+(?:лет|год)|"
    r"я\s+(?:учусь\s+)?(?:в\s+)?(?:[1-9]|1[01])\s*(?:-?(?:м|ом|й))?\s+класс\w*|учусь\s+в\s+(?:[1-9]|1[01])\s*"
    r"(?:-?(?:м|ом))?\s+класс\w*|я\s+(?:ещё\s+|еще\s+)?школьни(?:к|ца)|i\s*(?:'|a)?m\s+(?:1[0-7]|[5-9])\s*"
    r"(?:years?\s+old|y/?o)\b)",
    re.I,
)


def wants_optout(text: str) -> bool:
    return bool(_OPTOUT_RE.search(str(text or "")))


def minor_signal(text: str) -> bool:
    return bool(_MINOR_RE.search(str(text or "")))


def _path(vault: PersonaVault, person_key: str):
    return vault.person_dir(person_key) / FILE_NAME


def read_state(vault: PersonaVault, person_key: str) -> dict[str, Any]:
    """``{"opt_out": bool, "reason": str}``; a missing or broken file means no opt-out."""
    try:
        raw = json.loads(_path(vault, person_key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"opt_out": False, "reason": ""}
    if not isinstance(raw, dict):
        return {"opt_out": False, "reason": ""}
    return {"opt_out": raw.get("opt_out") is True, "reason": str(raw.get("reason") or "")[:32]}


def set_optout(vault: PersonaVault, person_key: str, *, reason: str = REASON_PARTICIPANT) -> None:
    current = read_state(vault, person_key)
    if current["opt_out"] and current["reason"] == REASON_AGE:
        return                                   # an age signal is never downgraded to a revocable opt-out
    _atomic_json(vault.ensure(person_key) / FILE_NAME,
                 {"opt_out": True, "reason": reason, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "schema": "bossman.pit.roast/1"})


def clear_optout(vault: PersonaVault, person_key: str) -> bool:
    """The participant takes their own opt-out back. An age signal stays (False is returned)."""
    current = read_state(vault, person_key)
    if current["opt_out"] and current["reason"] == REASON_AGE:
        return False
    _path(vault, person_key).unlink(missing_ok=True)
    return True


def participant_allows(vault: PersonaVault, person_key: str, text: str = "") -> bool:
    """The participant side of the switch: no opt-out on file and no minor signal in this message."""
    if minor_signal(text):
        return False
    try:
        return not read_state(vault, person_key)["opt_out"]
    except Exception:  # noqa: BLE001 - when unsure, the normal manner
        return False
