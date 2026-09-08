"""Адаптер Higgsfield поверх БРАУЗЕРНОЙ СЕССИИ аккаунта, а не поверх страницы.

Разница не косметическая. Порт страницы (`DomPort`) умеет нажимать и читать —
и всё. Он не знает, чей это аккаунт, не ведёт журнал, не считает отказы, не
умеет позвать человека и не отличает «кнопки нет» от «интерфейс сменился».
Адаптер, работающий прямо с портом, каждую из этих защит обходит молча: не
потому что кто-то так решил, а потому что их просто нет на его пути.

Поэтому здесь всё идёт через `AccountBrowserSession`:

* **личность.** Сессия проверяет, чей аккаунт в контексте, ДО первого действия
  и после каждого вмешательства человека. Генерация в чужой рабочей области
  тратит чужую квоту и оставляет файлы там, куда владелец не смотрит;
* **проверки человека.** Капча, повторный вход, второй фактор — сессия уводит
  себя в `TAKEOVER_REQUIRED`, а работа получает `HUMAN_CHALLENGE`/
  `NEEDS_OWNER_AUTH`. Обхода нет ни здесь, ни в сессии, ни в настройках;
* **отпечаток цели.** Между «нашли кнопку» и «нажали» страница успевает
  измениться; сессия снимает отпечаток заново прямо перед нажатием;
* **журнал.** Каждое нажатие и каждое событие жизненного цикла работы уходит в
  аудит сессии, уже вычищенное её редактором;
* **реестр возможностей.** Три детерминированных отказа подряд на одной версии
  пакета селекторов — это сменившийся интерфейс, а не невезение: возможность
  уходит в паузу, и адаптер в неё не ломится.

Границы продукта соблюдаются буквально: лимит частоты, исчерпанный план и
недоступная возможность записываются честно и уводят работу на другую
стратегию. Ни докупки, ни смены плана, ни повторов «а вдруг пропустят» здесь
нет.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
import asyncio
import time

from ..browser.capabilities import FailureKind
from ..browser.session import AccountBrowserSession, BrokenUi, IdentityMismatch
from ..browser.states import BrowserState
from ..domain.errors import ProviderError
from .browser_worker import AdapterRefusal
from .higgsfield_browser_contracts import (
    BrowserGenerationObservation,
    BrowserGenerationRequest,
    BrowserGenerationState,
    MediaKind,
    SubmissionReceipt,
)
from .media_gate import MediaRejection, MediaVerdict, inspect_generated_media
from .workspace import GenerationWorkspace
from .higgsfield_selectors import (
    ACTION_ASPECT,
    ACTION_DOWNLOAD,
    ACTION_DURATION,
    ACTION_JOB_CARD,
    ACTION_PRESET,
    ACTION_PROMPT,
    ACTION_SUBMIT,
    PROVIDER,
)

# Сколько независимых признаков обязаны подтвердить отправку. Двойка не
# настройка: одно нажатие ничего не доказывает, а единственный признак слишком
# легко получить случайно (текст «generating» мог висеть от прошлой работы).
# Вынести это число в конфигурацию значило бы дать возможность выставить его в
# ноль — то есть вернуть «нажали, значит отправлено».
REQUIRED_SUBMISSION_SIGNALS = 2


class SubmissionNotObserved(AdapterRefusal):
    """Нажали, но отправки не видно. Повторять НЕЛЬЗЯ.

    Отсутствие признака — это не «не отправилось», это «неизвестно». Повтор
    при неизвестности порождает вторую генерацию за деньги владельца и второй
    файл, который потом некому сопоставить. Работа останавливается и ждёт
    решения оркестратора.
    """

    state = BrowserGenerationState.FAILED


class DuplicateSubmission(AdapterRefusal):
    """Эта работа уже отправлялась через этот адаптер."""

    state = BrowserGenerationState.FAILED


class DownloadFailed(RuntimeError):
    """Кнопку нажали, файл не приехал. Повторить работу можно."""


class InvalidGeneratedMedia(RuntimeError):
    """Файл приехал, но это не то, что просили. Повторить работу можно.

    Отдельный тип, потому что действие разное: битую генерацию имеет смысл
    переделать, а `UNMEASURED` — это отсутствие ffmpeg на машине, и переделывать
    там нечего, пока владелец его не поставит.
    """

    def __init__(self, verdict: MediaVerdict) -> None:
        super().__init__(verdict.reason)
        self.verdict = verdict

    @property
    def owner_action_required(self) -> bool:
        return self.verdict.owner_action_required


@dataclass(frozen=True, slots=True)
class HiggsfieldAdapterConfig:
    """Адрес генератора, каталог карантина и словарь признаков страницы.

    Признаки — данные: интерфейс провайдера меняет формулировки, и правка
    списка не должна означать правку логики. Порогов и выключателей защит
    здесь нет намеренно.
    """

    generation_url: str
    quarantine_dir: Path
    min_artifact_bytes: int = 1024
    auth_text: tuple[str, ...] = (
        "sign in", "log in", "log into", "continue with google", "create an account",
        "войти", "зарегистрироваться",
    )
    rate_limit_text: tuple[str, ...] = (
        "too many requests", "rate limit", "try again later", "slow down",
        "слишком много запросов",
    )
    policy_text: tuple[str, ...] = (
        "upgrade your plan", "out of credits", "no credits left", "quota exceeded",
        "not available on your plan", "subscription required", "buy credits",
    )
    progress_text: tuple[str, ...] = (
        "generating", "in queue", "queued", "processing", "rendering",
    )
    failure_text: tuple[str, ...] = (
        "generation failed", "something went wrong", "failed to generate",
        "we couldn't generate",
    )
    download_timeout_s: float = 30.0
    download_poll_s: float = 0.25

    def __post_init__(self) -> None:
        if not str(self.generation_url).strip():
            raise ValueError("адрес страницы генерации обязателен")
        if self.download_timeout_s <= 0 or self.download_poll_s <= 0:
            raise ValueError("сроки скачивания должны быть положительными")
        if self.min_artifact_bytes < 1:
            raise ValueError("порог размера должен быть положительным")


@dataclass(frozen=True, slots=True)
class _Surface:
    """Наблюдаемое состояние страницы одним куском.

    Снимается до и после нажатия «Generate»: отправку доказывает РАЗНИЦА, а не
    отдельно взятое значение.
    """

    url: str
    text: str
    submit_identity: str
    submit_present: bool
    submit_disabled: bool
    job_cards: int

    def markers(self, markers: Sequence[str]) -> str:
        for marker in markers:
            if marker in self.text:
                return marker
        return ""


class HiggsfieldBrowserAdapter:
    """Одна браузерная сессия — один генератор. Второй аккаунт сюда не попадает."""

    provider_name = "higgsfield-browser"

    def __init__(self, *, session: AccountBrowserSession,
                 config: HiggsfieldAdapterConfig,
                 workspace: GenerationWorkspace | None = None) -> None:
        if session.provider != PROVIDER:
            raise ValueError(
                f"сессия обслуживает провайдера {session.provider!r}, а адаптер — "
                f"{PROVIDER!r}: пакет селекторов был бы взят чужой")
        if workspace is not None and workspace.account_id != session.account_id:
            # Карантин другого аккаунта — это чужой скачанный файл, подобранный
            # как свой. Проверка стоит здесь, а не в вызывающем коде, потому что
            # вызывающих будет много, а изоляция должна быть одна.
            raise ValueError(
                f"карантин принадлежит аккаунту {workspace.account_id!r}, а сессия "
                f"обслуживает {session.account_id!r}")
        self.session = session
        self.config = config
        self.workspace = workspace
        self._submitted: dict[str, SubmissionReceipt] = {}

    # ------------------------------------------------------------------ служебное

    @property
    def adapter_version(self) -> str:
        """Версия пакета селекторов и есть версия адаптера для отчёта о дрейфе."""
        return f"{PROVIDER}/{self.session.pack_version}"

    def _observation(self, request: BrowserGenerationRequest,
                     state: BrowserGenerationState, *, safe_message: str = "",
                     **evidence: Any) -> BrowserGenerationObservation:
        evidence.setdefault("selector_pack", self.session.pack_version)
        return BrowserGenerationObservation(
            request.job_id, state, time.time(),
            safe_message=self.session.redactor.text(safe_message),
            evidence={key: str(value) for key, value in evidence.items()})

    def _audit(self, action: str, result: str, **fields: Any) -> None:
        self.session.audit_event(action=action, result=result, **fields)

    async def _surface(self) -> _Surface:
        """Снять наблюдаемые признаки страницы, ничего на ней не меняя."""
        snapshot = await self.session.snapshot()
        submit = await self._peek(ACTION_SUBMIT)
        cards = await self._count(ACTION_JOB_CARD)
        return _Surface(
            url=snapshot.url, text=snapshot.text.lower(),
            submit_identity=str((submit or {}).get("accessible_name")
                                or (submit or {}).get("text") or ""),
            submit_present=submit is not None,
            submit_disabled=bool((submit or {}).get("disabled")),
            job_cards=cards)

    async def _peek(self, action_name: str) -> dict[str, Any] | None:
        """Посмотреть, есть ли цель, НЕ считая её отсутствие поломкой.

        Отдельный путь мимо `plan()` нужен именно затем, чтобы наблюдение не
        засчитывалось реестру как отказ: «кнопки скачивания ещё нет» — это
        нормальное состояние ожидания, а не сменившийся интерфейс.
        """
        action = self.session.pack().get(action_name)
        if action is None:
            return None
        for strategy in action.strategies:
            found = await self.session.dom.find(strategy.kind, strategy.value)
            if found:
                return found[0]
        return None

    async def _count(self, action_name: str) -> int:
        action = self.session.pack().get(action_name)
        if action is None:
            return 0
        for strategy in action.strategies:
            found = await self.session.dom.find(strategy.kind, strategy.value)
            if found:
                return len(found)
        return 0

    # ------------------------------------------------------------------ подготовка

    async def prepare(self, request: BrowserGenerationRequest
                      ) -> BrowserGenerationObservation:
        """Довести сессию до состояния, из которого можно отправлять работу."""
        if self.session.ledger.cooling_down(ACTION_SUBMIT):
            # Пакет селекторов уже признан несоответствующим интерфейсу. Идти
            # туда снова — это и есть повтор в изменившийся интерфейс.
            return self._observation(
                request, BrowserGenerationState.UI_CHANGED,
                safe_message=self.session.ledger.reason_of(ACTION_SUBMIT),
                cooling_down="true")

        blocked = await self._reach_ready(request)
        if blocked is not None:
            return blocked

        current = await self.session.dom.current_url()
        if not current.startswith(self.config.generation_url):
            challenge = await self.session.visit(self.config.generation_url)
            if challenge.present:
                return self._observation(
                    request, BrowserGenerationState.HUMAN_CHALLENGE,
                    safe_message=challenge.describe(), challenge=challenge.kind.value,
                    provider=challenge.provider)

        surface = await self._surface()
        marker = surface.markers(self.config.auth_text)
        if marker:
            return self._observation(
                request, BrowserGenerationState.NEEDS_OWNER_AUTH,
                safe_message="страница генератора предлагает войти: сессия владельца "
                             "не активна",
                marker=marker)
        marker = surface.markers(self.config.policy_text)
        if marker:
            return self._observation(
                request, BrowserGenerationState.POLICY_BLOCKED,
                safe_message="генератор сообщает об ограничении плана или квоты; "
                             "план не меняется и кредиты не покупаются автоматически",
                marker=marker)
        marker = surface.markers(self.config.rate_limit_text)
        if marker:
            return self._observation(
                request, BrowserGenerationState.RATE_LIMITED,
                safe_message="генератор сообщает об ограничении частоты; обхода нет",
                marker=marker)

        # Обязательные цели ищутся через сессию: их отсутствие — это дрейф
        # интерфейса, и он ОБЯЗАН попасть в реестр возможностей.
        try:
            await self.session.plan(ACTION_PROMPT)
            await self.session.plan(ACTION_SUBMIT)
        except BrokenUi as broken:
            return self._observation(
                request, BrowserGenerationState.UI_CHANGED,
                safe_message=str(broken), failure=broken.kind.value,
                adapter_version=self.adapter_version)
        except ProviderError as refusal:
            return self._observation(
                request, BrowserGenerationState.HUMAN_CHALLENGE,
                safe_message=refusal.safe_detail)

        return self._observation(request, BrowserGenerationState.READY,
                                 url=surface.url)

    async def _reach_ready(self, request: BrowserGenerationRequest
                           ) -> BrowserGenerationObservation | None:
        """Поднять сессию и перевести её состояние в состояние работы.

        Возвращает `None`, если сессия готова, и наблюдение — если работать
        нельзя. Автоматического входа здесь нет: `LOGIN_REQUIRED` уходит
        владельцу, а не в подстановку пароля.
        """
        if self.session.state is BrowserState.DISABLED:
            try:
                await self.session.start()
            except IdentityMismatch as mismatch:
                return self._observation(
                    request, BrowserGenerationState.NEEDS_OWNER_AUTH,
                    safe_message="в браузере открыт другой аккаунт генератора; "
                                 "работа не выполняется от чужого имени",
                    expected=mismatch.expected)
        state = self.session.state
        if state is BrowserState.TAKEOVER_REQUIRED:
            challenge = self.session.challenge
            target = (BrowserGenerationState.HUMAN_CHALLENGE if challenge.present
                      else BrowserGenerationState.NEEDS_OWNER_AUTH)
            return self._observation(
                request, target,
                safe_message=challenge.describe() if challenge.present
                else "генератор ждёт действия владельца в браузере",
                challenge=challenge.kind.value)
        if state in {BrowserState.LOGIN_REQUIRED, BrowserState.REAUTH_REQUIRED,
                     BrowserState.STOPPED}:
            return self._observation(
                request, BrowserGenerationState.NEEDS_OWNER_AUTH,
                safe_message="браузерная сессия генератора не аутентифицирована; "
                             "вход выполняет владелец",
                session_state=state.value)
        if state is BrowserState.BROKEN_UI:
            return self._observation(
                request, BrowserGenerationState.UI_CHANGED,
                safe_message="сессия остановлена на сменившемся интерфейсе",
                session_state=state.value,
                adapter_version=self.adapter_version)
        if state is BrowserState.COOLDOWN:
            return self._observation(
                request, BrowserGenerationState.RATE_LIMITED,
                safe_message="сессия в паузе после предупреждений площадки",
                session_state=state.value)
        if state is not BrowserState.READY:
            return self._observation(
                request, BrowserGenerationState.FAILED,
                safe_message=f"браузерная сессия в состоянии {state.value}",
                session_state=state.value)
        return None

    # ------------------------------------------------------------------ отправка

    async def submit(self, request: BrowserGenerationRequest) -> SubmissionReceipt:
        """Заполнить форму и нажать «Generate». Ровно один раз на работу."""
        known = self._submitted.get(request.job_id)
        if known is not None:
            raise DuplicateSubmission(
                f"работа {request.job_id} уже отправлялась в {known.provider}; "
                f"повторная отправка потратила бы квоту владельца дважды")

        before = await self._surface()

        await self.session.fill_text(ACTION_PROMPT, request.prompt)
        if request.aspect_ratio:
            await self._fill_optional(ACTION_ASPECT, request.aspect_ratio)
        if request.media_kind is MediaKind.VIDEO and request.duration_seconds:
            await self._fill_optional(ACTION_DURATION, str(request.duration_seconds))
        if request.preset:
            await self._fill_optional(ACTION_PRESET, request.preset)

        target = await self.session.plan(ACTION_SUBMIT)
        await self.session.act(target, operation="click",
                               idempotency_key=request.job_id)

        after = await self._surface()
        signals = self._submission_signals(before, after)
        if len(signals) < REQUIRED_SUBMISSION_SIGNALS:
            self.session.ledger.record_failure(
                ACTION_SUBMIT, selector_pack_version=self.session.pack_version,
                kind=FailureKind.POSTCONDITION_FAILED)
            self._audit("generation.submit", "not_observed",
                        idempotency_key=request.job_id,
                        target_identity=target.descriptor.semantic_identity(),
                        target_fingerprint=target.fingerprint,
                        url_before=before.url, url_after=after.url,
                        error_class="SUBMISSION_NOT_OBSERVED",
                        detail=f"признаков отправки: {sorted(signals)}; нужно "
                               f"{REQUIRED_SUBMISSION_SIGNALS}. Повтор не делается: "
                               f"неизвестно, ушла работа или нет")
            raise SubmissionNotObserved(
                f"после нажатия не видно отправки (признаки: {sorted(signals)}); "
                f"повторная отправка не делается")

        receipt = SubmissionReceipt(
            job_id=request.job_id, provider=self.provider_name,
            submitted_at_epoch_s=time.time(),
            evidence={"selector_pack": self.session.pack_version,
                      "signals": ",".join(sorted(signals)),
                      "submit_control": target.descriptor.semantic_identity(),
                      "url": after.url})
        self._submitted[request.job_id] = receipt
        self._audit("generation.submit", "ok", idempotency_key=request.job_id,
                    target_identity=target.descriptor.semantic_identity(),
                    target_fingerprint=target.fingerprint,
                    url_before=before.url, url_after=after.url,
                    detail=f"отправка подтверждена признаками: {sorted(signals)}")
        return receipt

    async def _fill_optional(self, action_name: str, value: str) -> None:
        """Заполнить необязательное поле, если оно есть в этом интерфейсе.

        Отсутствие такого поля — не поломка: у изображения нет длительности, а
        соотношение сторон может выбираться иначе. Поэтому цель сначала
        осматривается мимо реестра и только потом заполняется через сессию.
        """
        if await self._peek(action_name) is None:
            return
        await self.session.fill_text(action_name, value)

    def _submission_signals(self, before: _Surface, after: _Surface) -> set[str]:
        """Чем именно подтверждается, что работа ушла провайдеру."""
        signals: set[str] = set()
        if before.submit_present and not after.submit_present:
            signals.add("submit_control_gone")
        elif after.submit_disabled and not before.submit_disabled:
            signals.add("submit_control_disabled")
        elif before.submit_identity and after.submit_identity != before.submit_identity:
            signals.add("submit_control_changed")
        if after.job_cards > before.job_cards:
            signals.add("job_card_appeared")
        marker = after.markers(self.config.progress_text)
        if marker and not before.markers(self.config.progress_text):
            signals.add("progress_text")
        if after.url != before.url:
            signals.add("url_changed")
        return signals

    # ------------------------------------------------------------------ ожидание

    async def poll(self, request: BrowserGenerationRequest,
                   receipt: SubmissionReceipt) -> BrowserGenerationObservation:
        """Посмотреть, что происходит у провайдера. Ничего не нажимает."""
        snapshot = await self.session.snapshot()
        if snapshot.challenge.present:
            challenge = await self.session.check_for_challenge()
            return self._observation(
                request, BrowserGenerationState.HUMAN_CHALLENGE,
                safe_message=challenge.describe(), challenge=challenge.kind.value)

        text = snapshot.text.lower()

        def marker_of(markers: Sequence[str]) -> str:
            for marker in markers:
                if marker in text:
                    return marker
            return ""

        marker = marker_of(self.config.policy_text)
        if marker:
            return self._observation(
                request, BrowserGenerationState.POLICY_BLOCKED,
                safe_message="генератор сообщает об ограничении плана или квоты",
                marker=marker)
        marker = marker_of(self.config.rate_limit_text)
        if marker:
            return self._observation(
                request, BrowserGenerationState.RATE_LIMITED,
                safe_message="генератор сообщает об ограничении частоты; обхода нет",
                marker=marker)
        marker = marker_of(self.config.auth_text)
        if marker:
            return self._observation(
                request, BrowserGenerationState.HUMAN_CHALLENGE,
                safe_message="генератор просит войти посреди ожидания: нужен владелец",
                marker=marker)

        if await self._peek(ACTION_DOWNLOAD) is not None:
            return self._observation(request, BrowserGenerationState.OUTPUT_READY,
                                     url=snapshot.url)

        marker = marker_of(self.config.failure_text)
        if marker:
            return self._observation(
                request, BrowserGenerationState.FAILED,
                safe_message="генератор сообщил о неудачной генерации",
                marker=marker)
        return self._observation(request, BrowserGenerationState.WAITING_PROVIDER)

    # ------------------------------------------------------------------ забор файла

    async def collect(self, request: BrowserGenerationRequest,
                      receipt: SubmissionReceipt) -> Path:
        """Скачать результат в карантин, ИЗМЕРИТЬ его и только потом принять.

        Три шага, и порядок между ними не переставляется. Пока файл лежит в
        карантине, он не результат работы: `output_workspace` — это уже
        свидетельство, на которое будут ссылаться сборка ролика и отчёт
        владельцу, и HTML-страница ошибки, попавшая туда под именем `.mp4`,
        обнаружится не здесь, а в момент, когда ролик надо отдавать.
        """
        quarantine = self._quarantine()
        before = {path.resolve() for path in quarantine.glob("*") if path.is_file()}

        target = await self.session.plan(ACTION_DOWNLOAD)
        await self.session.act(target, operation="click",
                               idempotency_key=f"{request.job_id}:download")

        arrived = await self._wait_for_download(quarantine, before)
        if arrived is None:
            self._audit("generation.collect", "download_failed",
                        idempotency_key=f"{request.job_id}:download",
                        error_class="DOWNLOAD_FAILED",
                        detail=f"за {self.config.download_timeout_s:.0f} с в "
                               f"карантине не появился файл")
            raise DownloadFailed(
                "скачанный файл не появился в карантине браузера")

        verdict = inspect_generated_media(
            arrived, media_kind=request.media_kind,
            min_bytes=self.config.min_artifact_bytes)
        if not verdict.accepted:
            self._reject(arrived, request, verdict)
            raise InvalidGeneratedMedia(verdict)

        accepted = self._promote(arrived, request, verdict)
        self._audit("generation.collect", "ok",
                    idempotency_key=f"{request.job_id}:download",
                    target_identity=target.descriptor.semantic_identity(),
                    detail=f"принято в рабочую область: {accepted.name}; "
                           f"{verdict.reason}")
        return accepted

    def _quarantine(self) -> Path:
        """Каталог карантина. Из рабочей области аккаунта, если она задана."""
        if self.workspace is not None:
            return self.workspace.prepare()
        quarantine = Path(self.config.quarantine_dir)
        quarantine.mkdir(parents=True, exist_ok=True)
        return quarantine

    async def _wait_for_download(self, quarantine: Path,
                                 before: set[Path]) -> Path | None:
        deadline = time.monotonic() + self.config.download_timeout_s
        while True:
            arrived = self._new_files(quarantine, before)
            if arrived:
                return arrived[0]
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(self.config.download_poll_s)

    def _promote(self, source: Path, request: BrowserGenerationRequest,
                 verdict: MediaVerdict) -> Path:
        """Перенести принятый файл под именем, собранным НАМИ.

        Имя провайдера в путь не попадает: расширение берётся из измерения, а
        основа — из идентификатора работы. Так файл в рабочей области всегда
        сопоставим с записью о работе, а `scene.mp4`, оказавшийся картинкой,
        не выглядит видео.
        """
        name = f"{request.job_id}{verdict.extension}"
        if self.workspace is not None:
            return self.workspace.promote(source, workspace=request.output_workspace,
                                          name=name)
        destination = Path(request.output_workspace).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        moved = destination / name
        source.replace(moved)
        return moved

    def _reject(self, source: Path, request: BrowserGenerationRequest,
                verdict: MediaVerdict) -> None:
        """Убрать непринятый файл и записать, почему."""
        reason = f"{request.job_id}: {verdict.reason}"
        if self.workspace is not None:
            self.workspace.reject(source, reason=reason)
        else:
            rejected = source.parent / "rejected"
            rejected.mkdir(parents=True, exist_ok=True)
            source.replace(rejected / source.name)
        self.session.ledger.record_failure(
            ACTION_DOWNLOAD, selector_pack_version=self.session.pack_version,
            kind=(FailureKind.TRANSIENT
                  if verdict.rejection is MediaRejection.UNMEASURED
                  else FailureKind.POSTCONDITION_FAILED))
        self._audit("generation.collect", "rejected_media",
                    idempotency_key=f"{request.job_id}:download",
                    error_class=(verdict.rejection.value if verdict.rejection
                                 else "OUTPUT_INVALID"),
                    detail=verdict.reason)

    @staticmethod
    def _new_files(directory: Path, before: set[Path]) -> list[Path]:
        candidates = [
            path for path in directory.glob("*")
            if path.is_file() and path.resolve() not in before
            and not path.name.endswith((".crdownload", ".part", ".tmp"))
        ]
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return candidates


__all__ = ["REQUIRED_SUBMISSION_SIGNALS", "DownloadFailed", "DuplicateSubmission",
           "HiggsfieldAdapterConfig", "HiggsfieldBrowserAdapter",
           "InvalidGeneratedMedia", "SubmissionNotObserved"]
