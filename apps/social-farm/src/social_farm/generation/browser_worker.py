"""Работник браузерной генерации: границы, повторы, свидетельства.

Адаптер знает страницу конкретного провайдера. Работник не знает про неё
ничего и знать не должен — он отвечает за то, что одинаково у любого
провайдера: сколько попыток допустимо, когда пора остановиться, что записать в
долговременное состояние и что произойдёт после перезапуска.

Три решения этого модуля стоит назвать вслух.

**Отправка записывается до ожидания, а не после.** Если процесс умрёт между
нажатием «Generate» и записью, перезапуск отправит работу второй раз — за
деньги владельца и с двумя файлами, которые потом некому сопоставить. Если он
умрёт между записью и ожиданием, перезапуск просто продолжит ждать. Из двух
несовершенных порядков выбран тот, чья худшая ошибка дешевле.

**Работа ведётся одним работником.** Не флагом «занято», а арендой с концом:
упавший работник обязан отпустить работу сам, по истечении срока, а не держать
её до чьего-то вмешательства.

**Не всякий отказ можно повторять.** `AdapterRefusal` — про случаи, где повтор
опаснее неудачи: неподтверждённая отправка могла уйти провайдеру, и вторая
попытка потратит квоту дважды.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import asyncio
import hashlib
import os
import time

from .higgsfield_browser_contracts import (
    ArtifactReceipt,
    BrowserGenerationObservation,
    BrowserGenerationRequest,
    BrowserGenerationState,
    MediaKind,
    SubmissionReceipt,
    transition_allowed,
)
from .job_store import (DEFAULT_LEASE_SECONDS, GenerationJobRecord,
                        GenerationJobStore)


class DownloadFailed(RuntimeError):
    """Кнопку нажали, файл не приехал.

    Живёт здесь, а не в адаптере конкретного провайдера, потому что решение
    «это можно повторить» принимает работник, и принимать его по имени класса
    из чужого модуля означало бы сверять типы строками.
    """


class ArtifactEscapedWorkspace(RuntimeError):
    """Файл оказался вне утверждённой рабочей области.

    Не «работа не удалась», а сработавшая граница: адаптер записал что-то туда,
    куда ему не разрешали. Такое не превращается в тихую запись `FAILED` и не
    гасится циклом наполнения буфера — состояние работы отмечается, и ошибка
    идёт дальше наверх, потому что чинить надо адаптер, а не работу.
    """


class AdapterRefusal(RuntimeError):
    """Отказ адаптера, который НЕЛЬЗЯ повторять внутри той же работы.

    Обычная ошибка адаптера — «не получилось, попробуй ещё». Этот класс — про
    другое: «повторять запрещено, потому что повтор опаснее неудачи». Так
    выглядит неподтверждённая отправка (работа могла уйти провайдеру, и вторая
    попытка потратила бы квоту владельца дважды) и повторная отправка того же
    идентификатора.

    Отдельный тип нужен именно затем, чтобы работник различал эти два случая
    механически, а не по тексту сообщения.
    """

    state: "BrowserGenerationState" = None  # type: ignore[assignment]
    owner_action_required: bool = False


class BrowserGenerationAdapter(Protocol):
    provider_name: str

    async def prepare(self, request: BrowserGenerationRequest) -> BrowserGenerationObservation: ...
    async def submit(self, request: BrowserGenerationRequest) -> SubmissionReceipt: ...
    async def poll(self, request: BrowserGenerationRequest, receipt: SubmissionReceipt) -> BrowserGenerationObservation: ...
    async def collect(self, request: BrowserGenerationRequest, receipt: SubmissionReceipt) -> Path: ...


@dataclass(slots=True)
class BrowserWorkerConfig:
    """Границы одной работы. Ни одна из них не отключается значением 0."""

    poll_interval_s: float = 2.0
    # Опрос с нарастающим шагом: первое наблюдение быстро, дальше реже.
    # Спрашивать страницу каждые две секунды час подряд — это не наблюдение,
    # это нагрузка на провайдера и на машину владельца.
    max_poll_interval_s: float = 30.0
    poll_backoff: float = 1.5
    max_poll_seconds: float = 900.0
    min_artifact_bytes: int = 1024
    # Сколько раз повторять ЗАБОР файла. Повторяется только скачивание:
    # генерация уже оплачена и лежит у провайдера, а вот загрузка могла
    # сорваться. Непринятое медиа не перекачивается — файл не изменится.
    download_attempts: int = 2
    lease_seconds: float = DEFAULT_LEASE_SECONDS

    def __post_init__(self) -> None:
        if self.poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        if self.max_poll_interval_s < self.poll_interval_s:
            raise ValueError("max_poll_interval_s must not be below poll_interval_s")
        if self.poll_backoff < 1.0:
            raise ValueError("poll_backoff must not shrink the interval")
        if self.max_poll_seconds <= 0:
            raise ValueError("max_poll_seconds must be positive")
        if self.min_artifact_bytes < 1:
            raise ValueError("min_artifact_bytes must be positive")
        if self.download_attempts < 1:
            raise ValueError("download_attempts must be positive")
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")


class BrowserGenerationWorker:
    def __init__(
        self,
        *,
        adapter: BrowserGenerationAdapter,
        store: GenerationJobStore,
        config: BrowserWorkerConfig | None = None,
        owner: str = "",
    ) -> None:
        self.adapter = adapter
        self.store = store
        self.config = config or BrowserWorkerConfig()
        self.owner = str(owner or f"{adapter.provider_name}:pid-{os.getpid()}")

    # ------------------------------------------------------------------ состояние

    def _set_state(
        self,
        record: GenerationJobRecord,
        target: BrowserGenerationState,
        *,
        safe_message: str = "",
        owner_action_required: bool = False,
        error_class: str | None = None,
    ) -> None:
        if record.state != target and not transition_allowed(record.state, target):
            raise RuntimeError(f"illegal browser generation transition: {record.state.value} -> {target.value}")
        record.state = target
        record.safe_message = safe_message
        record.owner_action_required = owner_action_required
        record.last_error_class = error_class
        self.store.save(record)

    @staticmethod
    def _observed(record: GenerationJobRecord) -> BrowserGenerationObservation:
        return BrowserGenerationObservation(
            record.job_id, record.state, time.time(), record.safe_message,
            evidence=dict(record.submission_evidence))

    # ------------------------------------------------------------------ запуск

    async def run(self, request: BrowserGenerationRequest) -> ArtifactReceipt | BrowserGenerationObservation:
        record = self.store.create(job_id=request.job_id, mission_id=request.mission_id, provider=self.adapter.provider_name)
        if record.terminal:
            return self._observed(record)

        claimed = self.store.claim(record.job_id, owner=self.owner,
                                   ttl_s=self.config.lease_seconds)
        if claimed is None:
            # Не ошибка и не повод ждать: работу уже ведут, и второй работник на
            # той же работе — это вторая отправка.
            return BrowserGenerationObservation(
                record.job_id, record.state, time.time(),
                safe_message=f"работу {record.job_id} ведёт другой работник "
                             f"({record.lease_owner})")
        record = claimed
        try:
            if record.submitted:
                # Перезапуск с незавершённой работой: отправлять нечего, работа
                # уже у провайдера. Продолжаем с того места, где остановились.
                return await self._await_result(request, record,
                                                self._stored_receipt(record))
            return await self._submit_and_await(request, record)
        finally:
            self.store.release(record.job_id, owner=self.owner)

    def _stored_receipt(self, record: GenerationJobRecord) -> SubmissionReceipt:
        """Восстановить свидетельство отправки из долговременного состояния."""
        return SubmissionReceipt(
            job_id=record.job_id, provider=record.provider,
            submitted_at_epoch_s=record.submitted_at_epoch_s or 0.0,
            evidence=dict(record.submission_evidence) or {"restored": "job_store"},
            provider_job_id=record.provider_job_id)

    async def _submit_and_await(self, request: BrowserGenerationRequest,
                                record: GenerationJobRecord
                                ) -> ArtifactReceipt | BrowserGenerationObservation:
        for attempt in range(max(1, record.attempt + 1), request.max_attempts + 1):
            record.attempt = attempt
            self._set_state(record, BrowserGenerationState.STARTING)

            prep = await self.adapter.prepare(request)
            self._set_state(record, BrowserGenerationState.AUTH_CHECK)
            if prep.state in {
                BrowserGenerationState.NEEDS_OWNER_AUTH,
                BrowserGenerationState.HUMAN_CHALLENGE,
                BrowserGenerationState.POLICY_BLOCKED,
                # Ограничение частоты и сменившийся интерфейс — тоже конечные
                # исходы подготовки. Повторять их внутри той же работы значит
                # долбиться в лимит и в пропавшую кнопку.
                BrowserGenerationState.RATE_LIMITED,
                BrowserGenerationState.UI_CHANGED,
            }:
                self._set_state(
                    record,
                    prep.state,
                    safe_message=prep.safe_message,
                    owner_action_required=prep.owner_action_required,
                )
                return prep
            if prep.state is not BrowserGenerationState.READY:
                self._set_state(record, BrowserGenerationState.FAILED, safe_message=prep.safe_message, error_class="prepare")
                if attempt < request.max_attempts:
                    record.state = BrowserGenerationState.CREATED
                    self.store.save(record)
                    continue
                return self._observed(record)

            self._set_state(record, BrowserGenerationState.READY)
            self._set_state(record, BrowserGenerationState.SUBMITTING)
            try:
                receipt = await self.adapter.submit(request)
            except AdapterRefusal as refusal:
                # Повтора нет по решению адаптера, а не по исчерпанию попыток.
                state = refusal.state or BrowserGenerationState.FAILED
                self._set_state(record, state, safe_message=str(refusal)[:300],
                                owner_action_required=refusal.owner_action_required,
                                error_class=type(refusal).__name__)
                return self._observed(record)
            except Exception as exc:
                self._set_state(record, BrowserGenerationState.FAILED, safe_message=str(exc)[:300], error_class=type(exc).__name__)
                if attempt < request.max_attempts:
                    record.state = BrowserGenerationState.CREATED
                    self.store.save(record)
                    continue
                return self._observed(record)

            # Свидетельство отправки — в долговременное состояние ДО ожидания.
            record.provider_job_id = receipt.provider_job_id
            stored = self.store.record_submission(
                record.job_id, submitted_at_epoch_s=receipt.submitted_at_epoch_s,
                evidence=dict(receipt.evidence),
                provider_job_ref=receipt.provider_job_id or "")
            # Все три поля переносятся обратно в рабочую копию: следующий
            # `save` пишет её целиком, и незаполненное поле здесь стёрло бы то,
            # что `record_submission` только что записал.
            record.submitted_at_epoch_s = stored.submitted_at_epoch_s
            record.submission_evidence = stored.submission_evidence
            record.provider_job_ref = stored.provider_job_ref
            self.store.save(record)

            return await self._await_result(request, record, receipt)

        self._set_state(record, BrowserGenerationState.FAILED, safe_message="attempt budget exhausted")
        return self._observed(record)

    # ------------------------------------------------------------------ ожидание

    async def _await_result(self, request: BrowserGenerationRequest,
                            record: GenerationJobRecord, receipt: SubmissionReceipt
                            ) -> ArtifactReceipt | BrowserGenerationObservation:
        """Дождаться результата и забрать его. Отправка уже произошла."""
        if record.state in {BrowserGenerationState.SUBMITTING,
                            BrowserGenerationState.WAITING_PROVIDER}:
            self._set_state(record, BrowserGenerationState.WAITING_PROVIDER)
            waited = await self._poll_until_ready(request, record, receipt)
            if waited is not None:
                return waited
        elif record.state not in {BrowserGenerationState.OUTPUT_READY,
                                  BrowserGenerationState.COLLECTING,
                                  BrowserGenerationState.VERIFYING}:
            # Отправка записана, а состояние не из ожидающих: сказать честно, а
            # не догадываться, что там произошло.
            self._set_state(record, BrowserGenerationState.FAILED,
                            safe_message=f"работа отправлена, но её состояние "
                                         f"{record.state.value} не подлежит "
                                         f"продолжению",
                            error_class="unresumable")
            return self._observed(record)
        return await self._collect_and_verify(request, record, receipt)

    async def _poll_until_ready(self, request: BrowserGenerationRequest,
                                record: GenerationJobRecord,
                                receipt: SubmissionReceipt
                                ) -> BrowserGenerationObservation | None:
        """Наблюдать до готовности. `None` означает «результат готов»."""
        started = time.monotonic()
        interval = self.config.poll_interval_s
        while True:
            if request.deadline_epoch_s is not None and time.time() >= request.deadline_epoch_s:
                self._set_state(record, BrowserGenerationState.TIMEOUT, safe_message="generation deadline exceeded")
                return self._observed(record)
            if time.monotonic() - started >= self.config.max_poll_seconds:
                self._set_state(record, BrowserGenerationState.TIMEOUT, safe_message="provider polling timeout")
                return self._observed(record)

            observation = await self.adapter.poll(request, receipt)
            if observation.state is BrowserGenerationState.WAITING_PROVIDER:
                self.store.renew(record.job_id, owner=self.owner,
                                 ttl_s=self.config.lease_seconds)
                await asyncio.sleep(interval)
                interval = min(interval * self.config.poll_backoff,
                               self.config.max_poll_interval_s)
                continue
            if observation.state is BrowserGenerationState.OUTPUT_READY:
                self._set_state(record, BrowserGenerationState.OUTPUT_READY)
                return None
            if observation.state in {
                BrowserGenerationState.RATE_LIMITED,
                BrowserGenerationState.HUMAN_CHALLENGE,
                BrowserGenerationState.POLICY_BLOCKED,
                BrowserGenerationState.UI_CHANGED,
                BrowserGenerationState.TIMEOUT,
                BrowserGenerationState.FAILED,
            }:
                self._set_state(
                    record,
                    observation.state,
                    safe_message=observation.safe_message,
                    owner_action_required=observation.owner_action_required,
                )
                return observation
            self._set_state(record, BrowserGenerationState.FAILED, safe_message="unexpected provider state", error_class="protocol")
            return self._observed(record)

    # ------------------------------------------------------------------ забор

    async def _collect_and_verify(self, request: BrowserGenerationRequest,
                                  record: GenerationJobRecord,
                                  receipt: SubmissionReceipt
                                  ) -> ArtifactReceipt | BrowserGenerationObservation:
        last_error: Exception | None = None
        for attempt in range(1, self.config.download_attempts + 1):
            self._set_state(record, BrowserGenerationState.COLLECTING)
            try:
                artifact_path = await self.adapter.collect(request, receipt)
            except Exception as exc:                                # noqa: BLE001
                last_error = exc
                if attempt < self.config.download_attempts and self._retryable(exc):
                    continue
                return self._collection_failed(record, exc)

            self._set_state(record, BrowserGenerationState.VERIFYING)
            try:
                artifact = self._verify_artifact(request, artifact_path)
            except ArtifactEscapedWorkspace:
                # Состояние отмечается, но ошибка идёт наверх: это граница.
                self._collection_failed(record, ArtifactEscapedWorkspace(
                    "generated artifact escaped output workspace"))
                raise
            except Exception as exc:                                # noqa: BLE001
                return self._collection_failed(record, exc)
            record.output_path = str(artifact.path)
            record.artifact_sha256 = artifact.sha256
            self._set_state(record, BrowserGenerationState.COMPLETE)
            return artifact

        return self._collection_failed(record, last_error or RuntimeError("collect"))

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        """Скачивание повторяется, непринятое медиа — нет.

        Сорванная загрузка может пройти со второго раза: файл у провайдера тот
        же. Файл, который не является медиа, вторым скачиванием медиа не станет.
        """
        return isinstance(exc, DownloadFailed)

    def _collection_failed(self, record: GenerationJobRecord, exc: Exception
                           ) -> BrowserGenerationObservation:
        self._set_state(record, BrowserGenerationState.FAILED,
                        safe_message=str(exc)[:300],
                        owner_action_required=bool(
                            getattr(exc, "owner_action_required", False)),
                        error_class=type(exc).__name__)
        return self._observed(record)

    def _verify_artifact(self, request: BrowserGenerationRequest, path: Path) -> ArtifactReceipt:
        resolved_workspace = request.output_workspace.resolve()
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(resolved_workspace)
        except ValueError as exc:
            raise ArtifactEscapedWorkspace(
                "generated artifact escaped output workspace") from exc
        if not resolved_path.is_file():
            raise RuntimeError("generated artifact does not exist")
        size = resolved_path.stat().st_size
        if size < self.config.min_artifact_bytes:
            raise RuntimeError("generated artifact is too small")
        digest = hashlib.sha256(resolved_path.read_bytes()).hexdigest()
        suffix = resolved_path.suffix.lower()
        if request.media_kind is MediaKind.IMAGE and suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise RuntimeError("unexpected image artifact extension")
        if request.media_kind is MediaKind.VIDEO and suffix not in {".mp4", ".mov", ".webm", ".mkv"}:
            raise RuntimeError("unexpected video artifact extension")
        return ArtifactReceipt(
            job_id=request.job_id,
            path=resolved_path,
            sha256=digest,
            media_kind=request.media_kind,
            bytes_size=size,
            accepted_at_epoch_s=time.time(),
            qa_verdicts={"basic_file_validation": "pass"},
        )


__all__ = ["AdapterRefusal", "ArtifactEscapedWorkspace", "BrowserGenerationAdapter",
           "BrowserGenerationWorker", "BrowserWorkerConfig", "DownloadFailed"]
