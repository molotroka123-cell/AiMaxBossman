"""Сборка наблюдения: разрешение → извлечение → проверки → память.

Здесь нет ни одного пути наружу. Модуль не открывает браузер, не логинится, не
обходит оплату и капчу, не ходит в сеть и не пишет никуда, кроме двух слоёв
памяти. Вкладку открывает владелец, снимок делает вызывающий с разрешением —
сюда приходит уже снятое.

Наблюдение попадает в Working State (последнее увиденное) и в Episodic Memory
(что видели и когда). В Procedural Memory — никогда автоматически: для этого в
`memory.promote()` есть гейт, требующий эпизодов, проверки и независимого
подтверждения.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..safety import OwnerApproval, assert_no_live_execution, assert_read_only_integration
from . import extract as extract_mod
from .gate import Binding, ObservationRefused, OBSERVE_STAGE, admit
from .schema import (CoinwiseObservation, InjectionScan, ObservationEvidence, SourceMethod,
                     ValidationStatus, ViewportMeta, content_hash, monotonic_now, unknown,
                     utc, utcnow)

# Режим модуля. Константа, не настройка: READ_ONLY здесь — это утверждение о
# том, что путей записи в коде нет, а не пожелание.
COINWISE_MODE = "READ_ONLY"


class NoWritePathError(RuntimeError):
    """Кто-то попросил действие, которого в модуле нет и не будет."""


def assert_read_only(action: str = "observe") -> None:
    """Единственная дверь для «а можно ли». Ответ всегда один и тот же."""
    assert_no_live_execution(action, stage=OBSERVE_STAGE)
    assert_read_only_integration("coinwise", "read_only")


@dataclass(frozen=True, slots=True)
class Snapshot:
    """То, что вызывающий снял с уже открытой владельцем вкладки."""

    source_url: str
    observed_at: datetime
    collected_at: datetime
    binding: Binding
    dom: dict[str, Any] | None = None
    ocr_lines: list[dict[str, Any]] | None = None
    screenshot_bytes: bytes | None = None
    untrusted_text: dict[str, str] | None = None
    viewport: ViewportMeta = ViewportMeta()
    venue: str = ""


def _refused(snapshot: Snapshot, expected: Binding, exc: ObservationRefused, *,
             environment: str, head_sha: str, model_version: str) -> CoinwiseObservation:
    """Отказ — тоже наблюдение, и он обязан быть виден владельцу.

    Молча вернуть None значило бы, что на дашборде «ничего не показывают»;
    на самом деле показывают, просто мы отказались этому верить.
    """
    evidence = (ObservationEvidence.STALE if exc.status is ValidationStatus.STALE
                else ObservationEvidence.INVALID if exc.status in (
                    ValidationStatus.MISMATCH, ValidationStatus.CLOCK_SKEW,
                    ValidationStatus.PARSE_FAILED)
                else ObservationEvidence.BLOCKED)
    collected = utc(snapshot.collected_at)
    return CoinwiseObservation(
        observation_id=f"cw-{uuid.uuid4().hex[:12]}",
        task_id=expected.task_id, run_id=expected.run_id, session_id=expected.session_id,
        source_url=snapshot.source_url, symbol=expected.symbol, venue=snapshot.venue,
        timeframe=expected.timeframe,
        observed_at=utc(snapshot.observed_at), collected_at=collected,
        monotonic_collected_at=monotonic_now(),
        freshness_seconds=(collected - utc(snapshot.observed_at)).total_seconds(),
        source_method=SourceMethod.DOM,
        fields={}, evidence_class=evidence, validation_status=exc.status,
        injection_scan_status=InjectionScan.NOT_SCANNED,
        viewport=snapshot.viewport, environment=environment, head_sha=head_sha,
        model_version=model_version, notes=(str(exc),))


def observe(snapshot: Snapshot, *, approval: OwnerApproval | None, expected: Binding,
            environment: str = "", head_sha: str = "", model_version: str = "",
            mock: bool = False) -> CoinwiseObservation:
    """Превратить снимок в наблюдение или в честный отказ.

    `mock=True` — регрессионная фикстура. Она НИКОГДА не даёт
    REAL_BROWSER_READONLY: тест, выдающий себя за живой браузер, — худшее, что
    может случиться с торговым модулем, потому что зелёный отчёт перестаёт
    что-либо значить.
    """
    assert_read_only()
    try:
        passport = admit(approval=approval, source_url=snapshot.source_url,
                         expected=expected, actual=snapshot.binding,
                         observed_at=snapshot.observed_at, collected_at=snapshot.collected_at,
                         untrusted=snapshot.untrusted_text)
    except ObservationRefused as exc:
        return _refused(snapshot, expected, exc, environment=environment,
                        head_sha=head_sha, model_version=model_version)

    # Дёшево сначала: DOM. OCR — только под то, что DOM не отдал.
    notes: list[str] = []
    try:
        primary = (extract_mod.from_dom(snapshot.dom) if snapshot.dom
                   else extract_mod.ExtractionResult(
                       fields={}, liquidity_zones=(), method=SourceMethod.DOM,
                       notes=("снимка DOM нет",)))
    except (ValueError, TypeError, KeyError) as exc:
        # Разбор упал — fail closed. Половина разобранного дашборда выглядит
        # как целый, и это опаснее, чем отсутствие данных.
        return _refused(snapshot, expected,
                        ObservationRefused(ValidationStatus.PARSE_FAILED,
                                           f"снимок не разобран: {type(exc).__name__}: {exc}"),
                        environment=environment, head_sha=head_sha,
                        model_version=model_version)

    result = primary
    if snapshot.ocr_lines and any(not v.known for v in (primary.fields or {}).values()) \
            or (snapshot.ocr_lines and not primary.fields):
        result = extract_mod.merge(primary, extract_mod.from_ocr(snapshot.ocr_lines))
        notes.append("часть значений снята локальным OCR")
    if extract_mod.cloud_vision_enabled():          # по умолчанию недостижимо
        notes.append("облачное зрение разрешено владельцем явно")
    else:
        notes.append("облачное зрение выключено: скриншот наружу не уходил")

    collected = utc(snapshot.collected_at)
    used_ocr = any(v.method is SourceMethod.LOCAL_OCR
                   for v in result.fields.values() if v.known)
    if mock:
        evidence = ObservationEvidence.MOCK
    elif used_ocr or (snapshot.screenshot_bytes and not snapshot.dom):
        evidence = ObservationEvidence.SCREENSHOT_OBSERVED
    else:
        evidence = ObservationEvidence.REAL_BROWSER_READONLY

    payload = snapshot.screenshot_bytes if snapshot.screenshot_bytes else \
        repr(sorted((snapshot.dom or {}).items()))
    scan = passport["injection_scan_status"]
    if passport["injection_flags"]:
        notes.append("недоверенный текст помечен: " + ",".join(passport["injection_flags"][:6]))

    return CoinwiseObservation(
        observation_id=f"cw-{uuid.uuid4().hex[:12]}",
        task_id=expected.task_id, run_id=expected.run_id, session_id=expected.session_id,
        source_url=snapshot.source_url,
        symbol=result.symbol or expected.symbol,
        venue=result.venue or snapshot.venue,
        timeframe=result.timeframe or expected.timeframe,
        observed_at=utc(snapshot.observed_at), collected_at=collected,
        monotonic_collected_at=monotonic_now(),
        freshness_seconds=passport["freshness_seconds"],
        source_method=result.method,
        fields=result.fields, liquidity_zones=result.liquidity_zones,
        dashboard_state=result.dashboard_state, stream_state=result.stream_state,
        content_hash=content_hash(payload), viewport=snapshot.viewport,
        model_version=model_version, head_sha=head_sha, environment=environment,
        evidence_class=evidence, injection_scan_status=scan,
        validation_status=passport["validation_status"],
        notes=tuple((*result.notes, *notes))[:12])


def remember(memory: Any, observation: CoinwiseObservation) -> None:
    """Наблюдение — в Working State и Episodic Memory. Больше никуда.

    В Procedural не кладём ни при каких условиях: туда ведёт только
    `memory.promote()` со своим гейтом, и обходить его наблюдением дашборда
    значило бы превратить увиденное однажды в правило навсегда.
    """
    memory.working_state["coinwise_last_observation"] = observation.as_dict()
    store = getattr(memory, "coinwise_observations", None)
    if store is None:
        store = []
        setattr(memory, "coinwise_observations", store)
    store.append(observation)


__all__ = ["COINWISE_MODE", "Snapshot", "NoWritePathError", "assert_read_only",
           "observe", "remember", "Binding", "ObservationRefused"]
