"""Второе мнение перед необратимым действием: гейт, а не советчик.

Приём дешёвый и в этом проекте уже дважды ловил настоящие дефекты: перед тем
как что-то удалить, отправить наружу или потратить деньги, кто-то ДРУГОЙ обязан
попытаться доказать, что действие ошибочно. Здесь собран именно гейт — сам
проверяющий подключается снаружи функцией (`set_verifier`), никаких вызовов
моделей и сети в модуле нет.

Четыре свойства, ради которых модуль существует:

  * классификация действий — данными (`CATALOG`), а не догадкой в коде. Ветка
    вида «если в имени есть delete» ошибается молча и незаметно; список видов
    можно прочитать, оспорить и дополнить. Неизвестный вид считается
    НЕОБРАТИМЫМ: незнакомое действие — не то же самое, что безопасное;

  * независимость проверяющего — тот же принцип, что у обучающего корпуса
    (`learning/trace.py`, `_identity_errors`): независимость определяется по
    типизированной identity (principal_id + independence_class + модель +
    запуск), а не по красивой строке «проверено ревьюером». Тот же principal
    (с точностью до ролевого префикса), тот же запуск или та же модель —
    самопроверка, и она НЕ проходит. Правило реализовано здесь локально, а не
    импортом из repo-root `learning`: command-center поставляется отдельным
    пакетом, и гейт безопасности не должен менять поведение в зависимости от
    того, лежит ли рядом другой каталог;

  * вердикт типизирован тремя состояниями, и третье — «не смог проверить» —
    блокирует ровно так же, как «опровергнуто». Это и есть fail-closed:
    молчание, недоступность проверяющего, мусор в ответе — не согласие.
    Единственный проходной вердикт — NOT_REFUTED, полученный от независимого
    проверяющего;

  * запрос проверяющему (`build_challenge`) поручает ОПРОВЕРГНУТЬ намерение.
    Просьба «подтверди, что всё верно» даёт согласие почти всегда, поэтому
    формулировка — часть гейта и проверяется тестом (`challenge_is_adversarial`).

Флаг `BOSSMAN_SECOND_OPINION_ENABLED` по умолчанию выключен: при выключенном
флаге `run_gate` ничего не запрещает и ничего не пишет — поведение приложения
ровно такое же, как до модуля. Меняющая состояние ручка /second-opinion/check
при выключенном флаге отвечает 409.

Решения живут в журнале приложения (`app.state`) и уходят событием
`second_opinion.decision` на шину — свои таблицы модуль не заводит.
"""
from __future__ import annotations

import inspect
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db import utcnow
from . import Feature

FLAG = "BOSSMAN_SECOND_OPINION_ENABLED"
router = APIRouter()


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


# ---------- 1. классификация действий: данные, а не догадка ----------

@dataclass(frozen=True)
class ActionKind:
    """Вид действия и приговор о его обратимости."""

    kind: str
    reversible: bool
    category: str          # delete | egress | spend | local | unknown
    why: str               # почему именно так классифицировано

    def as_dict(self) -> dict:
        return {"kind": self.kind, "reversible": self.reversible,
                "category": self.category, "why": self.why}


# Категории необратимости. Держим их закрытым списком: «прочее» превращает
# классификацию в свободный текст, по которому нельзя принять решение.
CATEGORIES = ("delete", "egress", "spend", "local", "unknown")

# Необратимые виды: удаление, отправка наружу, трата денег. Возврата нет —
# состояние либо потеряно, либо уже увидено чужой стороной, либо оплачено.
_IRREVERSIBLE = (
    ActionKind("file.delete", False, "delete", "файл удалён — прежнего содержимого больше нет"),
    ActionKind("dir.delete", False, "delete", "каталог удалён вместе со всем, что в нём было"),
    ActionKind("db.drop_table", False, "delete", "таблица уничтожена вместе со строками"),
    ActionKind("db.delete_rows", False, "delete", "строки удалены, откат возможен только из бэкапа"),
    ActionKind("snapshot.delete", False, "delete", "снимок был точкой возврата — её больше нет"),
    ActionKind("repo.force_push", False, "delete", "история переписана: прежние коммиты недостижимы"),
    ActionKind("secret.revoke", False, "delete", "ключ отозван, прежним значением уже не воспользоваться"),
    ActionKind("email.send", False, "egress", "письмо прочитано получателем — отозвать нельзя"),
    ActionKind("message.send_external", False, "egress", "сообщение ушло за пределы системы"),
    ActionKind("publish.social", False, "egress", "публикация увидена до любого удаления"),
    ActionKind("http.post_external", False, "egress", "данные переданы чужой стороне"),
    ActionKind("webhook.call_external", False, "egress", "вызов у чужой стороны уже произошёл"),
    ActionKind("payment.charge", False, "spend", "деньги списаны"),
    ActionKind("subscription.purchase", False, "spend", "подписка оплачена и начала действовать"),
    ActionKind("cloud.provision", False, "spend", "ресурс создан и тарифицируется"),
    ActionKind("model.paid_call", False, "spend", "платный вызов оплачивается по факту"),
)

# Обратимые виды перечислены так же явно: гейт должен уметь пропускать, а не
# только запрещать, и список пропускаемого тоже должен быть виден снаружи.
_REVERSIBLE = (
    ActionKind("file.read", True, "local", "чтение ничего не меняет"),
    ActionKind("db.select", True, "local", "запрос ничего не меняет"),
    ActionKind("file.write_draft", True, "local", "черновик перезаписывается без потерь"),
    ActionKind("snapshot.create", True, "local", "снимок только добавляет точку возврата"),
    ActionKind("branch.create", True, "local", "ветка удаляется без следа"),
    ActionKind("task.create", True, "local", "задачу можно остановить и удалить"),
    ActionKind("note.write", True, "local", "заметка правится и удаляется"),
    ActionKind("log.append", True, "local", "запись в журнал ничего не разрушает"),
)

CATALOG: tuple[ActionKind, ...] = _IRREVERSIBLE + _REVERSIBLE
_BY_KIND = {a.kind: a for a in CATALOG}


def classify(kind: str) -> ActionKind:
    """Вид действия по его имени. Неизвестное имя — необратимое действие:
    гейт не имеет права додумывать за автора вызова, что тот имел в виду."""
    name = str(kind or "").strip().lower()
    known = _BY_KIND.get(name)
    if known is not None:
        return known
    return ActionKind(name or "(пусто)", False, "unknown",
                      "вид действия не описан в каталоге — считается необратимым, "
                      "пока его не внесли в список")


# ---------- 2. независимость проверяющего ----------

# Классы независимости — те же, что в learning/trace.py: своё же мнение,
# высказанное второй раз, независимым не становится.
INDEPENDENT_CLASSES = frozenset({"cross_model", "external_tool", "human"})

# Ролевые префиксы: «verifier:Qwen-14B» и «qwen-14b» — один и тот же участник.
_ROLE_PREFIXES = ("verifier:", "coder:", "model:", "agent:", "tool:", "reviewer:", "executor:")


def canonical_principal_id(pid: str) -> str:
    """Alias одного участника → один principal (см. learning/trace.py)."""
    p = str(pid or "").strip().lower()
    changed = True
    while changed:
        changed = False
        for pre in _ROLE_PREFIXES:
            if p.startswith(pre):
                p = p[len(pre):].strip()
                changed = True
    return p


@dataclass(frozen=True)
class Actor:
    """Участник: кто именно, какой моделью и в каком запуске.

    display-имя намеренно отсутствует: независимость решается по identity, а
    красивое имя ничего не доказывает."""

    principal_id: str
    model_id: str = ""
    run_id: str = ""
    independence_class: str = ""

    def as_dict(self) -> dict:
        return {"principal_id": self.principal_id, "model_id": self.model_id,
                "run_id": self.run_id, "independence_class": self.independence_class}


def independence_errors(executor: Actor, verifier: Actor) -> list[str]:
    """Чем проверяющий не независим от исполнителя. Пустой список — независим.

    Каждая проверка отвечает на свой способ выдать себя за другого: сменить
    подпись (principal), проверить себя в том же запуске (run), взять ту же
    модель под другим именем (model)."""
    errors: list[str] = []
    ver_pid = canonical_principal_id(verifier.principal_id)
    exe_pid = canonical_principal_id(executor.principal_id)
    if not ver_pid:
        errors.append("у проверяющего нет principal_id: проверять некому")
    if ver_pid and ver_pid == exe_pid:
        errors.append(f"проверяющий и исполнитель — один principal ({ver_pid})")
    ver_run, exe_run = str(verifier.run_id or ""), str(executor.run_id or "")
    if ver_run and ver_run == exe_run:
        errors.append(f"проверка внутри того же запуска ({ver_run})")
    ver_model = str(verifier.model_id or "").strip().lower()
    exe_model = str(executor.model_id or "").strip().lower()
    if ver_model and ver_model == exe_model:
        errors.append(f"тот же экземпляр модели ({ver_model})")
    if verifier.independence_class not in INDEPENDENT_CLASSES:
        errors.append(f"independence_class={verifier.independence_class!r} не входит в "
                      f"{sorted(INDEPENDENT_CLASSES)}")
    return errors


# ---------- 3. вердикт: три состояния, проходит одно ----------

REFUTED = "refuted"                    # проверяющий нашёл, почему действие ошибочно
NOT_REFUTED = "not_refuted"            # искал и не нашёл
COULD_NOT_CHECK = "could_not_check"    # проверить не удалось — это НЕ согласие

# Данные, а не разбросанные по коду if'ы: видно, что проходной вердикт один.
VERDICTS: dict[str, dict] = {
    REFUTED: {"blocks": True, "meaning": "найдена причина, по которой действие ошибочно"},
    NOT_REFUTED: {"blocks": False, "meaning": "причина не нашлась, хотя её искали"},
    COULD_NOT_CHECK: {"blocks": True,
                      "meaning": "проверка не состоялась; молчание и недоступность "
                                 "проверяющего согласием не считаются"},
}


def normalize_verdict(value: Any) -> str:
    """Чужая строка → вердикт. Всё неизвестное — COULD_NOT_CHECK: мусор в ответе
    проверяющего не имеет права стать разрешением."""
    v = str(value or "").strip().lower()
    return v if v in VERDICTS else COULD_NOT_CHECK


def verdict_blocks(verdict: str) -> bool:
    return VERDICTS[normalize_verdict(verdict)]["blocks"]


@dataclass(frozen=True)
class Opinion:
    """Мнение проверяющего: вердикт, кто его высказал, почему."""

    verdict: str
    verifier: Actor
    reason: str = ""

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "verifier": self.verifier.as_dict(),
                "reason": self.reason}


@dataclass(frozen=True)
class Intent:
    """Намерение исполнителя: что он собирается сделать и с чем."""

    kind: str
    executor: Actor
    summary: str = ""
    target: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "executor": self.executor.as_dict(),
                "summary": self.summary, "target": self.target}


# ---------- 4. запрос проверяющему: поручение опровергнуть ----------

# Маркеры, по которым видно, что запрос требует опровержения (обязательны), и
# маркеры просьбы согласиться (запрещены). Держим списком, чтобы требование к
# формулировке было проверяемым, а не вопросом вкуса.
REFUTE_MARKERS = ("опроверг", "почему это действие ошибочно", "худший случай")
CONFIRM_MARKERS = ("подтверди", "убедись, что действие верно", "одобри")


def build_challenge(intent: Intent) -> str:
    """Текст для проверяющего. Ему поручено искать причину отказа, а не повод
    согласиться: вопрос «всё ли верно?» почти всегда получает ответ «да»."""
    action = classify(intent.kind)
    return (
        "Роль: независимый оппонент. Поручение — ОПРОВЕРГНУТЬ намерение ниже.\n"
        f"Действие: {action.kind} (категория {action.category}, "
        f"{'необратимое' if not action.reversible else 'обратимое'}).\n"
        f"Почему так классифицировано: {action.why}.\n"
        f"Объект: {intent.target or '(не указан)'}\n"
        f"Замысел исполнителя: {intent.summary or '(не описан)'}\n"
        f"Исполнитель: {intent.executor.principal_id or '(не назван)'}\n"
        "\n"
        "Что сделать:\n"
        "1. Найди, почему это действие ошибочно: не тот объект, не тот объём, "
        "неверная предпосылка исполнителя, потеря того, что нельзя вернуть.\n"
        "2. Разбери худший случай: что останется невосстановимым, если исполнитель ошибся.\n"
        "3. Если данных для проверки не хватает — так и скажи; нехватка данных "
        "не является разрешением.\n"
        "\n"
        "Ответь одним вердиктом и причиной в одну-две строки:\n"
        f"  {REFUTED} — причина ошибочности найдена;\n"
        f"  {NOT_REFUTED} — ты искал причину и не нашёл;\n"
        f"  {COULD_NOT_CHECK} — проверить не удалось.\n"
        f"Молчание, отказ и непонятный ответ засчитываются как {COULD_NOT_CHECK}, "
        "и действие не будет выполнено."
    )


def challenge_is_adversarial(text: str) -> bool:
    """Запрос требует опровержения и не выпрашивает согласия."""
    low = str(text or "").lower()
    return (all(m in low for m in REFUTE_MARKERS)
            and not any(m in low for m in CONFIRM_MARKERS))


# ---------- проверяющий снаружи ----------

# Функция: (intent, challenge) -> Opinion. Синхронная или асинхронная —
# модуль сам разбирается; внутри неё может быть что угодно, включая человека.
Verifier = Callable[[Intent, str], "Opinion | Awaitable[Opinion]"]


def set_verifier(app: Any, fn: Verifier | None) -> None:
    """Подключить проверяющего к приложению. Хранится на app.state, а не в
    глобальной переменной модуля: два приложения в одном процессе не должны
    делить проверяющего."""
    app.state.second_opinion_verifier = fn


def get_verifier(app: Any) -> Verifier | None:
    return getattr(app.state, "second_opinion_verifier", None)


async def ask_verifier(intent: Intent, verifier: Verifier | None) -> Opinion:
    """Спросить проверяющего. Любая осечка — COULD_NOT_CHECK, а не «ну ладно»:
    отсутствие проверяющего, исключение внутри него и ответ не того типа
    одинаково означают, что проверки не было."""
    if verifier is None:
        return Opinion(COULD_NOT_CHECK, Actor(""),
                       "проверяющий не подключён: второе мнение получить не у кого")
    challenge = build_challenge(intent)
    try:
        result = verifier(intent, challenge)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:  # noqa: BLE001 — падение проверяющего не разрешает действие
        return Opinion(COULD_NOT_CHECK, Actor(""),
                       f"проверяющий не ответил: {type(exc).__name__}")
    if not isinstance(result, Opinion):
        return Opinion(COULD_NOT_CHECK, Actor(""),
                       "проверяющий вернул не Opinion — вердикта нет")
    return Opinion(normalize_verdict(result.verdict), result.verifier, result.reason)


# ---------- 5. решение и его запись ----------

@dataclass(frozen=True)
class Decision:
    """Итог гейта вместе с обоими участниками и причинами."""

    allowed: bool
    applied: bool                       # применялся ли гейт вообще (флаг)
    action: ActionKind
    intent: Intent
    opinion: Opinion | None = None
    independence: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    decided_at: datetime = field(default_factory=utcnow)

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "applied": self.applied,
                "action": self.action.as_dict(), "intent": self.intent.as_dict(),
                "executor": self.intent.executor.as_dict(),
                "verifier": (self.opinion.verifier.as_dict() if self.opinion else None),
                "verdict": (self.opinion.verdict if self.opinion else ""),
                "verifier_reason": (self.opinion.reason if self.opinion else ""),
                "independence": list(self.independence),
                "reasons": list(self.reasons),
                "decided_at": self.decided_at.isoformat()}


class DecisionLog:
    """Журнал решений приложения. Держим ограниченный хвост в памяти: гейт не
    заводит своих таблиц, а лента событий и так хранит каждое решение."""

    def __init__(self, limit: int = 200):
        self.limit = limit
        self._items: list[dict] = []

    def add(self, decision: Decision) -> dict:
        item = decision.as_dict()
        self._items.append(item)
        if len(self._items) > self.limit:
            del self._items[: len(self._items) - self.limit]
        return item

    def items(self) -> list[dict]:
        return list(reversed(self._items))


def decision_log(app: Any) -> DecisionLog:
    log = getattr(app.state, "second_opinion_log", None)
    if log is None:
        log = DecisionLog()
        app.state.second_opinion_log = log
    return log


async def run_gate(intent: Intent, *, opinion: Opinion | None = None,
                   verifier: Verifier | None = None) -> Decision:
    """Пропустить намерение через гейт.

    Порядок важен: сначала обратимость (обратимое действие второго мнения не
    требует и не платит за него), затем наличие мнения, затем независимость
    того, кто его высказал, и только потом сам вердикт. Независимость решается
    ДО вердикта: «не опровергнуто» от самого себя — не мнение, а эхо."""
    if not enabled():
        # Флаг выключен: гейта нет, поведение прежнее. Ничего не пишем и не
        # запрещаем — иначе выключенный флаг перестал бы означать «как раньше».
        return Decision(allowed=True, applied=False, action=classify(intent.kind),
                        intent=intent, reasons=("гейт выключен флагом",))

    action = classify(intent.kind)
    if action.reversible:
        return Decision(allowed=True, applied=True, action=action, intent=intent,
                        reasons=("действие обратимо: второе мнение не требуется",))

    if opinion is None:
        opinion = await ask_verifier(intent, verifier)
    else:
        opinion = Opinion(normalize_verdict(opinion.verdict), opinion.verifier, opinion.reason)

    reasons: list[str] = []
    independence = tuple(independence_errors(intent.executor, opinion.verifier))
    if independence:
        # Отказ по независимости — именно отказ, а не предупреждение с пропуском.
        reasons.append("проверяющий не независим от исполнителя")
        reasons.extend(independence)
        return Decision(allowed=False, applied=True, action=action, intent=intent,
                        opinion=opinion, independence=independence, reasons=tuple(reasons))

    if verdict_blocks(opinion.verdict):
        reasons.append(f"вердикт {opinion.verdict}: {VERDICTS[opinion.verdict]['meaning']}")
        if opinion.reason:
            reasons.append(opinion.reason)
        return Decision(allowed=False, applied=True, action=action, intent=intent,
                        opinion=opinion, reasons=tuple(reasons))

    reasons.append(f"вердикт {opinion.verdict} от независимого проверяющего "
                   f"{opinion.verifier.principal_id}")
    return Decision(allowed=True, applied=True, action=action, intent=intent,
                    opinion=opinion, reasons=tuple(reasons))


async def record(svc: Any, app: Any, decision: Decision) -> dict:
    """Записать решение: журнал ручки + событие на шину. Решение без записи
    невозможно проверить задним числом, поэтому пишутся оба участника."""
    item = decision_log(app).add(decision)
    await svc.bus.emit("second_opinion.decision", allowed=decision.allowed,
                       kind=decision.action.kind, category=decision.action.category,
                       executor=decision.intent.executor.principal_id,
                       verifier=(decision.opinion.verifier.principal_id
                                 if decision.opinion else ""),
                       verdict=(decision.opinion.verdict if decision.opinion else ""),
                       reasons=list(decision.reasons))
    return item


# ---------- 6. ручки ----------

class ActorIn(BaseModel):
    principal_id: str = ""
    model_id: str = ""
    run_id: str = ""
    independence_class: str = ""

    def to_actor(self) -> Actor:
        return Actor(principal_id=self.principal_id, model_id=self.model_id,
                     run_id=self.run_id, independence_class=self.independence_class)


class OpinionIn(BaseModel):
    verdict: str = ""
    verifier: ActorIn = ActorIn()
    reason: str = ""

    def to_opinion(self) -> Opinion:
        return Opinion(normalize_verdict(self.verdict), self.verifier.to_actor(), self.reason)


class CheckIn(BaseModel):
    kind: str
    executor: ActorIn
    summary: str = ""
    target: str = ""
    # Мнение можно принести с собой (проверяющий уже отвечал) либо оставить
    # пустым — тогда спрашиваем подключённого к приложению проверяющего.
    opinion: OpinionIn | None = None


@router.get("/second-opinion")
async def rules():
    """Каталог видов действий и правила гейта. Читающая ручка: отвечает и при
    выключенном флаге, честно показывая, что гейт не применяется."""
    return {
        "enabled": enabled(),
        "categories": list(CATEGORIES),
        "irreversible": [a.as_dict() for a in _IRREVERSIBLE],
        "reversible": [a.as_dict() for a in _REVERSIBLE],
        "unknown_kind_is_irreversible": True,
        "verdicts": VERDICTS,
        "passing_verdict": NOT_REFUTED,
        "independent_classes": sorted(INDEPENDENT_CLASSES),
        "independence_rule": ("проверяющий обязан отличаться от исполнителя principal_id, "
                              "запуском и моделью и иметь independence_class из списка"),
    }


@router.get("/second-opinion/decisions")
async def decisions(request: Request, limit: int = 50):
    """Записанные решения, свежие сверху."""
    items = decision_log(request.app).items()[: max(1, min(int(limit), 200))]
    return {"enabled": enabled(), "count": len(items), "decisions": items}


@router.post("/second-opinion/check")
async def check(payload: CheckIn, request: Request):
    """Прогнать намерение через гейт. Меняет состояние (пишет решение), поэтому
    при выключенном флаге — отказ, а не тихий пропуск."""
    if not enabled():
        raise HTTPException(409, {"message": f"второе мнение выключено: {FLAG} не установлен"})
    intent = Intent(kind=payload.kind, executor=payload.executor.to_actor(),
                    summary=payload.summary, target=payload.target)
    decision = await run_gate(intent,
                              opinion=payload.opinion.to_opinion() if payload.opinion else None,
                              verifier=get_verifier(request.app))
    item = await record(request.app.state.svc, request.app, decision)
    return {"enabled": True, "allowed": decision.allowed, "decision": item,
            "challenge": build_challenge(intent) if not decision.action.reversible else ""}


FEATURE = Feature(name="second_opinion", router=router)
