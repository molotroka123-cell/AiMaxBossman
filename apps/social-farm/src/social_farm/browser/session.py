"""Браузерная сессия одного аккаунта: автомат, защиты, действие.

Порядок шагов одного действия взят из `55_BROWSER_STATE_MACHINE` буквально:

    READY → BUSY → проверить семантическую цель → проверить политику/одобрение
    → выполнить → проверить результат → READY

и дополнен тремя вещами, без которых «проверить цель» ничего не значит:

1. **Два такта поиска цели.** `plan()` находит цель и снимает отпечаток;
   `act()` снимает отпечаток ЗАНОВО непосредственно перед действием и сверяет.
   Между планом и действием может пройти одобрение человека — именно там
   страница успевает измениться, и именно там слепое нажатие опаснее всего.
   Расхождение → `BROWSER_STALE_TARGET`, и действие **не выполняется**.
2. **Ни одного «ближайшего» варианта.** Неоднозначная цель — это отказ, а не
   выбор первой попавшейся. `.first` здесь означал бы «удалили не ту запись».
3. **Проверка личности.** Перед работой и после любого вмешательства человека
   сессия убеждается, что в контексте тот аккаунт, который ожидался. Вход в
   чужой аккаунт по ошибке хуже, чем несостоявшийся вход: первое видно всем
   подписчикам, второе — только нам.

Капча, подозрительный вход и второй фактор автоматикой не проходятся. Они
переводят сессию в `TAKEOVER_REQUIRED` и ждут человека.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..domain.errors import ErrorClass, ProviderError
from .audit import BrowserAuditRecord, BrowserAuditSink, InMemoryAuditSink
from .capabilities import BrowserCapabilityLedger, FailureKind
from .challenge import Challenge, ChallengeKind, detect_challenge
from .config import BrowserConfig
from .dom import DomPort
from .fingerprint import TargetDescriptor, fingerprint_of
from .secrets import Redactor, SecretRef, SecretResolver
from .selectors import SelectorAction, SelectorPack, SelectorRegistry, Strategy
from .states import BrowserState, check_transition


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IdentityMismatch(RuntimeError):
    """В контексте не тот аккаунт, которого ждали.

    Самая опасная ошибка потока и поэтому — отдельный тип и мгновенный
    `STOPPED`. Продолжать «на всякий случай» нельзя: следующее действие уйдёт
    от чужого имени.
    """

    def __init__(self, expected: str, observed: str) -> None:
        super().__init__(
            f"в браузерном контексте аккаунт {observed!r}, ожидался {expected!r}. "
            f"Сессия остановлена; нужен человек")
        self.expected = expected
        self.observed = observed


class BrokenUi(RuntimeError):
    """Цель не найдена или неоднозначна даже после запасных стратегий."""

    def __init__(self, action: str, detail: str, kind: FailureKind) -> None:
        super().__init__(f"{action}: {detail}")
        self.action = action
        self.kind = kind


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """Найденная цель вместе с отпечатком и тем, как именно она была найдена.

    Хранит стратегию и порядковый номер, а не ссылку на элемент: перед
    действием поиск повторяется ТОЙ ЖЕ стратегией, и сравнивается то, что
    нашлось теперь, с тем, что нашлось тогда.
    """

    action: str
    strategy_kind: str
    strategy_value: str
    ordinal: int
    ref: str
    fingerprint: str
    pack_version: str
    descriptor: TargetDescriptor
    planned_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "strategy_kind": self.strategy_kind,
                "ordinal": self.ordinal, "fingerprint": self.fingerprint,
                "pack_version": self.pack_version,
                "target_identity": self.descriptor.semantic_identity(),
                "planned_at": self.planned_at}


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    """То, что сессия отдаёт наружу. Значений секретов здесь нет."""

    url: str
    title: str
    generation: int
    text: str
    elements: tuple[dict[str, Any], ...]
    challenge: Challenge
    state: BrowserState

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "title": self.title, "generation": self.generation,
                "text": self.text, "elements": [dict(e) for e in self.elements],
                "challenge": self.challenge.to_dict(), "state": self.state.value}


class AccountBrowserSession:
    """Браузерная сессия ОДНОГО аккаунта. Второй аккаунт сюда не попадает.

    Объект создаётся с идентификатором аккаунта и портом к странице и меняться
    не может: нет ни метода, ни поля, которым можно было бы переключить сессию
    на другой аккаунт. Это первая из четырёх опор изоляции; остальные три — в
    `isolation.py` (каталог, маркер, права) и `worker.py` (процесс).
    """

    __slots__ = ("account_id", "expected_identity", "provider", "dom", "registry",
                 "config", "resolver", "ledger", "audit", "redactor", "_state",
                 "_generation", "_challenge", "_last_identity", "_pack_version",
                 "_takeover_since", "_verified_at", "identity_action", "landing_url")

    def __init__(self, *, account_id: str, expected_identity: str, dom: DomPort,
                 registry: SelectorRegistry, provider: str = "instagram",
                 config: BrowserConfig | None = None,
                 resolver: SecretResolver | None = None,
                 ledger: BrowserCapabilityLedger | None = None,
                 audit: BrowserAuditSink | None = None,
                 selector_pack_version: str = "",
                 identity_action: str = "account.identity.read",
                 landing_url: str = "") -> None:
        if not str(account_id).strip():
            raise ValueError("браузерная сессия не создаётся без аккаунта")
        if not str(expected_identity).strip():
            raise ValueError(
                "браузерная сессия не создаётся без ожидаемой личности аккаунта: "
                "проверять было бы не с чем")
        self.account_id = str(account_id)
        self.expected_identity = str(expected_identity)
        self.provider = provider
        self.dom = dom
        self.registry = registry
        self.config = config or BrowserConfig()
        self.resolver = resolver
        self.ledger = ledger or BrowserCapabilityLedger(account_id=self.account_id,
                                                        provider=provider,
                                                        config=self.config)
        self.audit: BrowserAuditSink = audit or InMemoryAuditSink()
        self.redactor = Redactor()
        self._state = BrowserState.DISABLED
        self._generation = 0
        self._challenge: Challenge = Challenge()
        self._last_identity = ""
        self._pack_version = selector_pack_version
        self._takeover_since = ""
        self._verified_at = ""
        self.identity_action = identity_action
        self.landing_url = landing_url

    # ------------------------------------------------------------------ состояние

    @property
    def state(self) -> BrowserState:
        return self._state

    @property
    def challenge(self) -> Challenge:
        return self._challenge

    @property
    def observed_identity(self) -> str:
        return self._last_identity

    def pack(self) -> SelectorPack:
        return self.registry.resolve(self.provider, self._pack_version)

    @property
    def pack_version(self) -> str:
        return self._pack_version or self.registry.active.get(self.provider, "")

    def _transition(self, target: BrowserState) -> None:
        check_transition(self._state, target)
        self._state = target

    # ------------------------------------------------------------------ выход наружу

    def _out(self, value: Any) -> Any:
        """Единственная дверь наружу. Через неё проходит ВСЁ, что уходит из сессии.

        Раньше редакция стояла только на тексте снимка — а адрес страницы,
        результат действия и запись аудита уходили как есть. Форма входа с
        `method=GET` уносит пароль прямо в адрес, и он оказывался и в снимке, и
        в результате действия, и в журнале, где остался бы навсегда. Дверь
        одна именно поэтому: забыть про неё можно только один раз.
        """
        return self.redactor.scrub(value)

    def _write_audit(self, **fields: Any) -> BrowserAuditRecord:
        """Собрать запись аудита УЖЕ вычищенной.

        Редакция при сериализации (`BrowserAuditRecord.to_dict`) остаётся, но
        она вторая: она полагается на то, что вызывающий передаст тот же
        редактор, а он у сессии свой и знает подставленные ею значения.
        Поэтому чистим здесь, до того как запись покинет сессию.
        """
        clean = {key: (self.redactor.text(value) if isinstance(value, str) else value)
                 for key, value in fields.items()}
        record = BrowserAuditRecord(**clean)
        self.audit.write(record)
        return record

    def describe(self) -> dict[str, Any]:
        """Строка `browser_session.schema.json` для этой сессии."""
        return self._out({
            "id": f"bs_{self.account_id}", "account_id": self.account_id,
            "session_ref": None, "state": self._state.value,
            "selector_pack_version": self.pack_version,
            "last_verified_at": self._verified_at or None,
            "last_takeover_at": self._takeover_since or None})

    # ------------------------------------------------------------------ запуск

    async def start(self) -> BrowserState:
        """Поднять сессию: контекст → безопасная страница → личность → READY.

        Порядок из `55_BROWSER_STATE_MACHINE`. Личность проверяется ДО того,
        как сессия становится готовой к действиям, а не после первого из них.
        """
        self._transition(BrowserState.STARTING)
        if self.landing_url:
            await self.dom.navigate(self.landing_url)
        await self._refresh_challenge()
        if self._challenge.present:
            # Проверка прямо на входе — значит, входа не было. Человек.
            self._transition(BrowserState.LOGIN_REQUIRED)
            return self._state
        observed = await self.read_identity()
        if not observed:
            self._transition(BrowserState.LOGIN_REQUIRED)
            return self._state
        if observed != self.expected_identity:
            self._transition(BrowserState.STOPPED)
            self._audit_identity("mismatch", observed)
            raise IdentityMismatch(self.expected_identity, observed)
        self._transition(BrowserState.AUTHENTICATED)
        self._audit_identity("ok", observed)
        self._transition(BrowserState.READY)
        return self._state

    async def read_identity(self) -> str:
        """Кто сейчас в контексте, по мнению самой страницы.

        Читается тем же реестром селекторов, что и всё остальное: личность —
        такая же цель, и опознаваться должна так же устойчиво.
        """
        action = self.pack().get(self.identity_action)
        if action is None:
            return ""
        for strategy in action.strategies:
            found = await self.dom.find(strategy.kind, strategy.value)
            if len(found) == 1:
                descriptor = TargetDescriptor.from_dict(found[0])
                handle = (descriptor.text or descriptor.accessible_name).strip()
                self._last_identity = handle
                return handle
        self._last_identity = ""
        return ""

    def _audit_identity(self, result: str, observed: str) -> None:
        if result == "ok":
            self._verified_at = _utc_now()
        self._write_audit(
            account_id=self.account_id, action="identity.verify", result=result,
            at=_utc_now(), target_identity=f"account[{observed or 'не определён'}]",
            selector_pack_version=self.pack_version, state_after=self._state.value,
            detail=("личность подтверждена" if result == "ok"
                    else f"ожидался {self.expected_identity!r}, найден {observed!r}"))

    # ------------------------------------------------------------------ передача человеку

    async def _refresh_challenge(self) -> Challenge:
        self._challenge = detect_challenge(
            markup=await self.dom.markup(),
            text=await self.dom.visible_text(self.config.snapshot_max_text),
            url=await self.dom.current_url())
        return self._challenge

    def require_takeover(self, reason: str,
                         kind: ChallengeKind = ChallengeKind.UNKNOWN_MODAL) -> ProviderError:
        """Перевести сессию к человеку и вернуть ошибку для работы.

        Ничего не «дожимает» и не пробует ещё раз: проверка, которую обязан
        пройти человек, автоматикой не проходится ни с какой настройкой.
        """
        if self._state is not BrowserState.TAKEOVER_REQUIRED:
            self._transition(BrowserState.TAKEOVER_REQUIRED)
        self._takeover_since = _utc_now()
        self._write_audit(
            account_id=self.account_id, action="takeover.request",
            result="requires_takeover", at=self._takeover_since,
            selector_pack_version=self.pack_version,
            state_after=self._state.value,
            error_class=ErrorClass.BROWSER_REQUIRES_TAKEOVER.value,
            detail=f"{kind.value}: {reason}")
        return ProviderError.of(
            ErrorClass.BROWSER_REQUIRES_TAKEOVER, safe_detail=self.redactor.text(reason),
            user_action=("Откройте браузерную сессию этого аккаунта и завершите "
                         "проверку сами. Автоматически она не проходится."))

    async def complete_takeover(self) -> BrowserState:
        """Человек закончил. Первым делом — снова проверить, чей это аккаунт.

        Именно здесь чаще всего и появляется чужой аккаунт: человек мог войти
        не в тот, имея открытыми несколько. Поэтому проверка личности здесь
        обязательна и несжимаема.
        """
        if self._state is not BrowserState.TAKEOVER_REQUIRED:
            raise RuntimeError(
                f"завершение передачи возможно только из TAKEOVER_REQUIRED, "
                f"сейчас {self._state.value}")
        await self._refresh_challenge()
        if self._challenge.present:
            # Проверка ещё на странице — человек не закончил.
            self._takeover_since = _utc_now()
            return self._state
        observed = await self.read_identity()
        if observed != self.expected_identity:
            self._transition(BrowserState.STOPPED)
            self._audit_identity("mismatch", observed)
            raise IdentityMismatch(self.expected_identity, observed)
        self._transition(BrowserState.AUTHENTICATED)
        self._audit_identity("ok", observed)
        self._transition(BrowserState.READY)
        return self._state

    def cooldown(self, reason: str) -> None:
        """Повторные предупреждения площадки. Ждём, а не подстраиваемся под них."""
        self._transition(BrowserState.COOLDOWN)
        self._write_audit(
            account_id=self.account_id, action="session.cooldown", result="cooldown",
            at=_utc_now(), selector_pack_version=self.pack_version,
            state_after=self._state.value, detail=reason)

    def resume_from_cooldown(self) -> None:
        self._transition(BrowserState.READY)

    def recover_from_broken_ui(self) -> None:
        """Вернуться в работу после поломки интерфейса.

        Только явным действием: сама по себе сессия из `BROKEN_UI` не выходит.
        Автоматический выход означал бы попытку тем же пакетом селекторов, от
        которой мы и уходили.
        """
        self._transition(BrowserState.READY)

    def stop(self, reason: str = "") -> None:
        self._transition(BrowserState.STOPPED)
        self._write_audit(
            account_id=self.account_id, action="session.stop", result="stopped",
            at=_utc_now(), selector_pack_version=self.pack_version,
            state_after=self._state.value, detail=reason)

    # ------------------------------------------------------------------ снимок

    async def snapshot(self) -> PageSnapshot:
        """Страница глазами вызывающего. Значений паролей здесь нет.

        Поле `type=password` отдаётся без значения не потому, что значение
        замазано, а потому, что оно не покидает страницу (`dom.py`). Редактор
        поверх — вторая линия: пароль мог быть введён и в обычное поле.
        """
        raw = await self.dom.elements(self.config.snapshot_max_interactive)
        self._generation += 1
        text = await self.dom.visible_text(self.config.snapshot_max_text)
        challenge = await self._refresh_challenge()
        elements = tuple(self._out(dict(item)) for item in raw)
        return PageSnapshot(
            # Адрес и заголовок чистятся наравне с текстом: форма с `method=GET`
            # уносит пароль именно в адрес, а заголовок вкладки на многих
            # страницах повторяет содержимое поля.
            url=self.redactor.text(await self.dom.current_url()),
            title=self.redactor.text(await self.dom.title()),
            generation=self._generation, text=self.redactor.text(text),
            elements=elements, challenge=challenge, state=self._state)

    # ------------------------------------------------------------------ поиск цели

    async def _locate(self, action: SelectorAction, ordinal: int | None
                      ) -> tuple[Strategy, list[dict[str, Any]]]:
        """Пройти стратегии по порядку. Первая, давшая однозначный ответ, — наша."""
        attempts = self.config.refresh_attempts + 1
        last_error: BrokenUi | None = None
        for attempt in range(attempts):
            for strategy in action.strategies:
                found = await self.dom.find(strategy.kind, strategy.value)
                if not found:
                    continue
                if len(found) > 1 and ordinal is None:
                    # «Do not click "nearest" alternative silently»: несколько
                    # совпадений без явного номера — отказ, а не первое попавшееся.
                    last_error = BrokenUi(
                        action.action,
                        f"стратегия {strategy.kind} нашла {len(found)} целей; "
                        f"какая из них нужна — неизвестно, и выбирать наугад нельзя",
                        FailureKind.TARGET_AMBIGUOUS)
                    continue
                if ordinal is not None and ordinal >= len(found):
                    last_error = BrokenUi(
                        action.action,
                        f"нужна цель #{ordinal}, а стратегия {strategy.kind} нашла "
                        f"{len(found)}",
                        FailureKind.TARGET_MISSING)
                    continue
                return strategy, found
            if attempt + 1 < attempts:
                # «refresh once» из спецификации. Одно обновление, не цикл.
                await self.dom.reload()
        raise last_error or BrokenUi(
            action.action,
            f"ни одна из {len(action.strategies)} стратегий не нашла цель "
            f"{action.target!r}",
            FailureKind.TARGET_MISSING)

    async def plan(self, action_name: str, *, ordinal: int | None = None
                   ) -> ResolvedTarget:
        """Найти цель и снять отпечаток. Ничего не нажимает.

        Отдельный такт нужен, чтобы между планом и действием могло встать
        одобрение человека: показать ему, ЧТО именно будет нажато, и только
        потом нажать.
        """
        action = self.pack().require(action_name)
        strategy, found = await self._locate(action, ordinal)
        index = ordinal or 0
        descriptor = TargetDescriptor.from_dict(found[index])
        return ResolvedTarget(
            action=action_name, strategy_kind=strategy.kind,
            strategy_value=strategy.value, ordinal=index, ref=descriptor.ref,
            fingerprint=fingerprint_of(descriptor, self.pack_version),
            pack_version=self.pack_version, descriptor=descriptor,
            planned_at=_utc_now())

    async def _reverify(self, target: ResolvedTarget) -> TargetDescriptor:
        """Снять отпечаток ЗАНОВО, непосредственно перед действием, и сверить.

        Это и есть защита от устаревшей цели. Она стоит здесь, а не в `plan`,
        потому что опасен именно промежуток между «увидели» и «нажали».
        """
        found = await self.dom.find(target.strategy_kind, target.strategy_value)
        if target.ordinal >= len(found):
            raise self._stale(target, f"целей стало {len(found)}, нужна была "
                                      f"#{target.ordinal}: страница изменилась")
        fresh = TargetDescriptor.from_dict(found[target.ordinal])
        actual = fingerprint_of(fresh, self.pack_version)
        if actual != target.fingerprint:
            raise self._stale(
                target,
                f"на месте цели теперь {fresh.semantic_identity()}, "
                f"а планировалась {target.descriptor.semantic_identity()}")
        return fresh

    def _stale(self, target: ResolvedTarget, detail: str) -> ProviderError:
        self.ledger.record_failure(target.action, selector_pack_version=self.pack_version,
                                   kind=FailureKind.STALE_TARGET)
        self._write_audit(
            account_id=self.account_id, action=target.action, result="stale_target",
            at=_utc_now(), target_identity=target.descriptor.semantic_identity(),
            target_fingerprint=target.fingerprint,
            selector_pack_version=self.pack_version,
            strategy_kind=target.strategy_kind, state_before=self._state.value,
            error_class=ErrorClass.BROWSER_STALE_TARGET.value, detail=detail)
        return ProviderError.of(
            ErrorClass.BROWSER_STALE_TARGET, safe_detail=self.redactor.text(detail),
            user_action=("Перечитайте страницу и спланируйте действие заново. "
                         "Действие НЕ выполнено."))

    # ------------------------------------------------------------------ условия

    async def _check_conditions(self, conditions: tuple[str, ...]) -> str:
        """Проверить пред- или постусловия. Возвращает описание невыполненного."""
        if not conditions:
            return ""
        url = (await self.dom.current_url()).lower()
        text = (await self.dom.visible_text(self.config.snapshot_max_text)).lower()
        for condition in conditions:
            kind, _, value = condition.partition(":")
            kind, value = kind.strip(), value.strip().lower()
            if kind == "url_contains" and value not in url:
                return f"адрес не содержит {value!r}"
            if kind == "url_absent" and value in url:
                return f"адрес содержит {value!r}, чего быть не должно"
            if kind == "text_contains" and value not in text:
                return f"на странице нет текста {value!r}"
            if kind == "text_absent" and value in text:
                return f"на странице остался текст {value!r}"
            if kind not in {"url_contains", "url_absent", "text_contains", "text_absent"}:
                return (f"условие {condition!r} записано в незнакомой форме; "
                        f"выполнять действие с непроверяемым условием нельзя")
        return ""

    # ------------------------------------------------------------------ действие

    async def act(self, target: ResolvedTarget, *, operation: str = "click",
                  text: str = "", secret_ref: SecretRef | None = None,
                  approval_ref: str = "", idempotency_key: str = "") -> dict[str, Any]:
        """Выполнить запланированное действие. Единственная точка внешнего эффекта.

        Порядок проверок обратный по стоимости ошибки: сначала то, что дешевле
        всего отменить (состояние сессии), последним — то, что отменить нельзя
        (само нажатие).
        """
        if self._state is not BrowserState.READY:
            raise RuntimeError(
                f"действие возможно только из READY, сейчас {self._state.value}")
        action = self.pack().require(target.action)
        url_before = await self.dom.current_url()
        self._transition(BrowserState.BUSY)
        try:
            # 1. Проверка, которую обязан пройти человек, отменяет всё остальное.
            challenge = await self._refresh_challenge()
            if challenge.present:
                self.ledger.record_failure(target.action,
                                           selector_pack_version=self.pack_version,
                                           kind=FailureKind.TAKEOVER_REQUIRED)
                raise self.require_takeover(challenge.describe(), challenge.kind)

            # 2. Предусловия пакета.
            failed = await self._check_conditions(action.preconditions)
            if failed:
                raise self._broken(action, f"предусловие не выполнено: {failed}",
                                   FailureKind.POSTCONDITION_FAILED)

            # 3. Разрушающее действие требует текста подтверждения на экране.
            if action.destructive and action.confirmation_text:
                page_text = (await self.dom.visible_text(
                    self.config.snapshot_max_text)).lower()
                if action.confirmation_text.lower() not in page_text:
                    raise self._broken(
                        action,
                        f"на странице нет подтверждающего текста "
                        f"{action.confirmation_text!r}; разрушающее действие без "
                        f"подтверждения не выполняется",
                        FailureKind.CONFIRMATION_MISMATCH)

            # 4. Отпечаток цели — заново, прямо сейчас.
            fresh = await self._reverify(target)

            # 5. Внешний эффект.
            if operation == "click":
                await self.dom.click(fresh.ref)
            elif operation == "fill":
                await self.dom.fill(fresh.ref, text)
            elif operation == "fill_secret":
                await self._fill_secret(fresh, secret_ref)
            else:
                raise ValueError(f"неизвестная операция браузера: {operation!r}")

            # 6. Постусловие: «успешно нажато» без проверки ничего не значит.
            failed = await self._check_conditions(action.postconditions)
            if failed:
                raise self._broken(action, f"постусловие не выполнено: {failed}",
                                   FailureKind.POSTCONDITION_FAILED)
        except ProviderError:
            self._settle()
            raise
        except BrokenUi:
            self._to_broken_ui()
            raise
        else:
            self.ledger.record_success(target.action,
                                       selector_pack_version=self.pack_version)
            url_after = await self.dom.current_url()
            self._write_audit(
                account_id=self.account_id, action=target.action, result="ok",
                at=_utc_now(), target_identity=fresh.semantic_identity(),
                target_fingerprint=target.fingerprint, url_before=url_before,
                url_after=url_after, selector_pack_version=self.pack_version,
                strategy_kind=target.strategy_kind, state_before="BUSY",
                idempotency_key=idempotency_key, approval_ref=approval_ref,
                secret_ref=secret_ref.ref if secret_ref else "",
                state_after=BrowserState.READY.value)
            self._transition(BrowserState.READY)
            return self._out({
                "ok": True, "action": target.action, "url_before": url_before,
                "url_after": url_after,
                "target_identity": fresh.semantic_identity(),
                "target_fingerprint": target.fingerprint})

    async def _fill_secret(self, descriptor: TargetDescriptor,
                           secret_ref: SecretRef | None) -> None:
        """Ввести секрет по ССЫЛКЕ. Значение не было аргументом и не станет им.

        Значение достаётся из хранилища здесь, живёт до подстановки и попадает
        в редактор — чтобы, если оно всё же где-то всплывёт (например, страница
        показала его в обычном поле), оно было вычищено на выходе.
        """
        if secret_ref is None:
            raise ValueError("ввод секрета требует ссылки на секрет")
        if self.resolver is None:
            raise RuntimeError(
                "хранилище секретов не подключено: вводить пароль неоткуда")
        value = self.resolver.resolve(secret_ref)
        self.redactor.remember(value)
        await self.dom.fill(descriptor.ref, value)
        del value

    def _broken(self, action: SelectorAction, detail: str, kind: FailureKind) -> BrokenUi:
        self.ledger.record_failure(action.action,
                                   selector_pack_version=self.pack_version, kind=kind)
        self._write_audit(
            account_id=self.account_id, action=action.action, result="broken_ui",
            at=_utc_now(), selector_pack_version=self.pack_version,
            state_before=self._state.value, error_class=kind.value, detail=detail)
        return BrokenUi(action.action, self.redactor.text(detail), kind)

    def _settle(self) -> None:
        """Вернуть сессию в состояние, из которого видно, что делать дальше."""
        if self._state is BrowserState.BUSY:
            self._transition(BrowserState.READY)

    def _to_broken_ui(self) -> None:
        if self._state is BrowserState.BUSY:
            self._transition(BrowserState.BROKEN_UI)

    # ------------------------------------------------------------------ удобные обёртки

    async def click(self, action_name: str, *, ordinal: int | None = None,
                    approval_ref: str = "", idempotency_key: str = "") -> dict[str, Any]:
        target = await self.plan(action_name, ordinal=ordinal)
        return await self.act(target, operation="click", approval_ref=approval_ref,
                              idempotency_key=idempotency_key)

    async def fill_text(self, action_name: str, text: str, *,
                        ordinal: int | None = None) -> dict[str, Any]:
        target = await self.plan(action_name, ordinal=ordinal)
        return await self.act(target, operation="fill", text=text)

    async def fill_secret(self, action_name: str, secret_ref: SecretRef, *,
                          ordinal: int | None = None) -> dict[str, Any]:
        """Ввести пароль. Принимает ССЫЛКУ, а не строку — и это весь смысл метода.

        Строкового варианта нет намеренно: пока такого параметра не существует,
        значение пароля не может оказаться ни в аргументах вызова, ни в трассе,
        ни в записи о работе.
        """
        if not isinstance(secret_ref, SecretRef):
            raise TypeError(
                "fill_secret принимает SecretRef, а не строку: значение секрета "
                "не передаётся аргументом")
        target = await self.plan(action_name, ordinal=ordinal)
        return await self.act(target, operation="fill_secret", secret_ref=secret_ref)

    # ------------------------------------------------------------------ помощь человеку

    # Автомат из спецификации не содержит перехода `LOGIN_REQUIRED →
    # AUTHENTICATED`. Читается это буквально: **вход всегда завершает человек**,
    # автоматического входа не существует. Методы ниже — не обход этого правила,
    # а его следствие: они позволяют подставить пароль в поле, НЕ нажимая
    # «Войти» и не меняя состояния сессии. Внешнего эффекта у заполнения поля
    # нет — он появится, когда человек сам нажмёт кнопку и сам пройдёт проверку.
    _ASSIST_STATES = frozenset({BrowserState.LOGIN_REQUIRED,
                                BrowserState.TAKEOVER_REQUIRED,
                                BrowserState.REAUTH_REQUIRED})

    def _require_assist_state(self, what: str) -> None:
        if self._state not in self._ASSIST_STATES:
            allowed = ", ".join(sorted(s.value for s in self._ASSIST_STATES))
            raise RuntimeError(
                f"{what} возможна только когда сессия ждёт человека ({allowed}); "
                f"сейчас {self._state.value}")

    async def _assist_target(self, action_name: str, ordinal: int | None
                             ) -> tuple[SelectorAction, TargetDescriptor,
                                        ResolvedTarget]:
        action = self.pack().require(action_name)
        if action.confirmation_text:
            page_text = (await self.dom.visible_text(
                self.config.snapshot_max_text)).lower()
            if action.confirmation_text.lower() not in page_text:
                raise self._broken(
                    action,
                    f"на странице нет текста {action.confirmation_text!r}: это не та "
                    f"страница, чтобы вводить сюда учётные данные",
                    FailureKind.CONFIRMATION_MISMATCH)
        target = await self.plan(action_name, ordinal=ordinal)
        fresh = await self._reverify(target)
        return action, fresh, target

    async def assist_fill_text(self, action_name: str, text: str, *,
                               ordinal: int | None = None) -> dict[str, Any]:
        """Подставить НЕсекретное значение (например, имя пользователя) при входе."""
        self._require_assist_state("подстановка значения при входе")
        action, fresh, target = await self._assist_target(action_name, ordinal)
        if fresh.secret:
            raise ValueError(
                f"{action_name}: цель — поле пароля; обычный текст в него не вводится, "
                f"для секрета есть assist_fill_secret")
        await self.dom.fill(fresh.ref, text)
        self._audit_assist(action, target, fresh, secret_ref="")
        return self._out({"ok": True, "action": action_name,
                          "state": self._state.value})

    async def assist_fill_secret(self, action_name: str, secret_ref: SecretRef, *,
                                 ordinal: int | None = None) -> dict[str, Any]:
        """Подставить пароль при входе, который завершает человек.

        Человеку не нужно знать пароль, чтобы закончить вход, а модели не нужно
        видеть его, чтобы его подставить. Кнопку «Войти» этот метод не нажимает
        и проверок за человека не проходит.
        """
        self._require_assist_state("подстановка секрета")
        if not isinstance(secret_ref, SecretRef):
            raise TypeError(
                "assist_fill_secret принимает SecretRef, а не строку: значение "
                "секрета не передаётся аргументом")
        action, fresh, target = await self._assist_target(action_name, ordinal)
        if not fresh.secret:
            # Пароль вводится только в поле пароля. Иначе значение окажется в
            # обычном поле, откуда его вернёт любой снимок страницы.
            raise ValueError(
                f"{action_name}: цель {fresh.semantic_identity()} — не поле "
                f"type=password; секрет туда не вводится")
        await self._fill_secret(fresh, secret_ref)
        self._audit_assist(action, target, fresh, secret_ref=secret_ref.ref)
        return self._out({"ok": True, "action": action_name,
                          "state": self._state.value})

    def _audit_assist(self, action: SelectorAction, target: ResolvedTarget,
                      fresh: TargetDescriptor, *, secret_ref: str) -> None:
        self._write_audit(
            account_id=self.account_id, action=action.action, result="assisted",
            at=_utc_now(), target_identity=fresh.semantic_identity(),
            target_fingerprint=target.fingerprint,
            selector_pack_version=self.pack_version,
            strategy_kind=target.strategy_kind, state_before=self._state.value,
            state_after=self._state.value, secret_ref=secret_ref,
            detail="значение подставлено; отправку формы и проверки выполняет человек")


__all__ = ["AccountBrowserSession", "BrokenUi", "IdentityMismatch", "PageSnapshot",
           "ResolvedTarget"]
