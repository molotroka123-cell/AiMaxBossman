"""Конвейер контента, шаги 1–9 из `57_CONTENT_PIPELINE_DETAILED`.

## Порядок шагов и почему он такой

Спека допускает прочтение, при котором рендер происходит внутри работы
публикации, уже после одобрения. Решение **C9** выбирает другой порядок:
рендер и валидация идут ДО создания ревизии.

Причина одна и она решающая. Одобрение привязано к хешу ревизии
(`45_CONTENT_APPROVAL_AND_VERSIONING`), а в хеш входит состав ассетов вместе с
их контрольными суммами (`domain/content.py`). Если рендер произойдёт после
ревизии, то в момент, когда человек нажимает «да», финального состава ещё не
существует: он увидит исходники, а опубликуются производные — с другими
суммами, другими размерами, другим кадром. Одобрение окажется привязано к
тому, чего никто не публиковал.

Поэтому чекпоинт `MEDIA_RENDERED` (`domain/jobs.py`) означает «производные
ассеты ревизии на месте и валидны», а не «сейчас отрендерим».

## Что блокирует автоматическую публикацию

* любой `FAIL_*` из движка правил, кроме неизвестного правила, — ревизия не
  создаётся вовсе: собирать неизменяемую запись из заведомо непригодного
  состава незачем;
* `FAIL_PROVIDER_RULE_UNKNOWN` — ревизия создаётся, автопубликация
  блокируется, работа уходит в `WAITING_APPROVAL` (решение **G16**). Состав
  собран и, насколько мы можем судить, исправен; мы лишь не смогли
  подтвердить его правилами провайдера, и это решение человека, а не догадка;
* отсутствие ffprobe/ffmpeg — честный `NOT_SUPPORTED`. Не отказ файлу и не
  разрешение ему: мы просто не можем его проверить, а непроверенное не
  публикуется.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..domain.content import ContentRevision
from ..domain.errors import ErrorClass, ProviderError
from ..domain.identity import new_id, utc_now
from ..domain.jobs import JobState
from ..media.asset import MediaAsset
from ..media.probe import ProbeUnavailable, toolchain_status
from ..media.profiles import ProfileBundle, ProfileError
from ..media.rules import (MediaValidation, UnprobedAsset, ValidationOutcome,
                           validate_stored_asset)
from ..media.store import ChecksumMismatch, MediaStorageError, MediaStore
from ..media.transform import (RenderPlan, TransformFailed, TransformUnavailable,
                               plan_transform, transcode)
from .project import ContentProject, Selection, SelectionError


class PipelineOutcome(str, Enum):
    """Чем закончилась сборка.

    Три исхода, а не два: «нельзя» и «не смогли проверить» — разные ответы, и
    владельцу аккаунта они говорят разное. Первый означает «замени файл»,
    второй — «посмотри сам, правил провайдера мы не знаем».
    """

    READY = "READY"                       # состав валиден, можно на одобрение
    NEEDS_HUMAN = "NEEDS_HUMAN"           # G16: правило неизвестно → WAITING_APPROVAL
    REJECTED = "REJECTED"                 # медиа непригодно
    NOT_SUPPORTED = "NOT_SUPPORTED"       # проверить нечем (нет ffprobe/ffmpeg)


@dataclass(frozen=True, slots=True)
class AssetReport:
    """Что случилось с одним ассетом на пути к публикации."""

    source_asset_id: str
    final_asset_id: str | None
    validation: MediaValidation
    plan: RenderPlan | None = None
    transformed: bool = False
    note: str = ""


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Итог шагов 1–9. Всё, что нужно, чтобы объяснить решение человеку."""

    outcome: PipelineOutcome
    project_id: str
    account_id: str
    content_type: str
    revision: ContentRevision | None = None
    assets: tuple[MediaAsset, ...] = ()
    reports: tuple[AssetReport, ...] = ()
    checkpoints: tuple[str, ...] = ()
    error: ProviderError | None = None
    profile_ref: str = ""
    render_profile_ref: str = ""
    unknown_rules: tuple[str, ...] = ()

    @property
    def auto_publish_allowed(self) -> bool:
        """Можно ли планировать публикацию без человека.

        Единственный `True` — `READY`. `NEEDS_HUMAN` сюда не попадает по
        решению G16: неизвестное правило провайдера не выдумывается.
        """
        return self.outcome is PipelineOutcome.READY

    @property
    def next_job_state(self) -> JobState:
        """В каком состоянии должна оказаться работа публикации.

        `WAITING_APPROVAL` для `NEEDS_HUMAN` — ровно то, чего требует G16.
        """
        if self.outcome is PipelineOutcome.READY:
            return JobState.QUEUED
        if self.outcome is PipelineOutcome.NEEDS_HUMAN:
            return JobState.WAITING_APPROVAL
        return JobState.CANCELLED

    def explain(self) -> str:
        if self.error is not None:
            return str(self.error)
        return self.outcome.value


def _not_supported(project_id: str, account_id: str, content_type: str,
                   detail: str, checkpoints: tuple[str, ...]) -> PipelineResult:
    """Честный `NOT_SUPPORTED`: измерить нечем, значит публиковать нельзя.

    Класс ошибки — `CAPABILITY_UNAVAILABLE` из закрытого перечня: возможности
    преобразовать и проверить медиа в этой установке действительно нет.
    Перечень ошибок не расширяется (решение C15).
    """
    return PipelineResult(
        outcome=PipelineOutcome.NOT_SUPPORTED, project_id=project_id,
        account_id=account_id, content_type=content_type, checkpoints=checkpoints,
        error=ProviderError.of(
            ErrorClass.CAPABILITY_UNAVAILABLE,
            safe_detail=f"NOT_SUPPORTED: {detail}",
            user_action="Установите ffmpeg и ffprobe — без них медиа не "
                        "проверяется и не публикуется."))


def run_pipeline(project: ContentProject, selection: Selection, *,
                 account_id: str, store: MediaStore, profiles: ProfileBundle,
                 revision_no: int = 1, supersedes_revision_id: str | None = None,
                 allow_transform: bool = True) -> PipelineResult:
    """Пройти шаги 1–9 для ОДНОГО аккаунта назначения.

    Один аккаунт, а не все сразу, потому что профиль медиа и профиль рендера
    разрешаются под конкретную цель (`66_CONTENT_RENDER_PROFILES`): один и тот
    же исходник даёт разные производные ассеты для разных площадок, и
    сваливать их в одну ревизию нельзя.
    """
    content_type = selection.content_type.value
    checkpoints: list[str] = []
    base = {"project_id": project.id, "account_id": account_id,
            "content_type": content_type}

    # --- шаг 5: план рендера — разрешить профили под эту цель
    try:
        media_profile = profiles.media_profile(content_type)
        render_profile = profiles.render_profile(content_type)
    except ProfileError as exc:
        return PipelineResult(
            outcome=PipelineOutcome.NEEDS_HUMAN, **base,
            error=ProviderError.of("FAIL_PROVIDER_RULE_UNKNOWN",
                                   safe_detail=str(exc)[:500]))
    checkpoints.append("RENDER_PLANNED")

    # --- ассеты обязаны лежать у нас, и это проверяется, а не предполагается.
    # Именно здесь отсекается всё, что пытается миновать хранилище: ассет с
    # выдуманной ссылкой не имеет содержимого, которое сойдётся с суммой.
    for asset in selection.assets:
        try:
            store.verify(asset)
        except ChecksumMismatch as exc:
            return PipelineResult(
                outcome=PipelineOutcome.REJECTED, **base,
                error=ProviderError.of("FAIL_CORRUPT", safe_detail=str(exc)[:500]))
        except MediaStorageError as exc:
            return PipelineResult(
                outcome=PipelineOutcome.REJECTED, **base,
                error=ProviderError.of("FAIL_MEDIA_MISSING", safe_detail=str(exc)[:500]))
    checkpoints.append("ASSETS_VERIFIED")

    # --- шаги 6–7: преобразование и валидация, ДО ревизии (решение C9)
    final: list[MediaAsset] = []
    reports: list[AssetReport] = []
    unknown: list[str] = []
    blocking: ProviderError | None = None
    needs_human = False

    for asset in selection.assets:
        try:
            verdict = validate_stored_asset(store, asset, media_profile)
        except ProbeUnavailable as exc:
            return _not_supported(**base, detail=str(exc),
                                  checkpoints=tuple(checkpoints))
        except UnprobedAsset as exc:
            return _not_supported(**base, detail=str(exc),
                                  checkpoints=tuple(checkpoints))
        except ChecksumMismatch as exc:
            return PipelineResult(
                outcome=PipelineOutcome.REJECTED, **base,
                error=ProviderError.of("FAIL_CORRUPT", safe_detail=str(exc)[:500]))

        if verdict.outcome is ValidationOutcome.PASS:
            final.append(asset)
            reports.append(AssetReport(asset.id, asset.id, verdict,
                                       note="подходит без преобразования"))
            continue

        if verdict.outcome is ValidationOutcome.PASS_WITH_TRANSFORM:
            plan = plan_transform(asset, verdict, render_profile)
            if not allow_transform:
                return PipelineResult(
                    outcome=PipelineOutcome.REJECTED, **base,
                    reports=tuple(reports),
                    error=ProviderError.of(
                        "FAIL_UNSUPPORTED",
                        safe_detail="требуется преобразование, но оно запрещено"))
            try:
                derived = transcode(store, asset, plan, media_profile=media_profile)
            except TransformUnavailable as exc:
                return _not_supported(**base, detail=str(exc),
                                  checkpoints=tuple(checkpoints))
            except TransformFailed as exc:
                return PipelineResult(
                    outcome=PipelineOutcome.REJECTED, **base, reports=tuple(reports),
                    error=ProviderError.of("FAIL_UNSUPPORTED",
                                           safe_detail=str(exc)[:500]))
            final.append(derived)
            reports.append(AssetReport(asset.id, derived.id, verdict, plan=plan,
                                       transformed=True,
                                       note="создан производный ассет; "
                                            "исходник не изменён"))
            continue

        # Дальше — отказы.
        reports.append(AssetReport(asset.id, None, verdict))
        if verdict.outcome is ValidationOutcome.FAIL_PROVIDER_RULE_UNKNOWN:
            # G16: не выдумываем правило. Состав сохраняем, решает человек.
            needs_human = True
            unknown.extend(verdict.unknown_rules)
            final.append(asset)
            continue
        blocking = blocking or verdict.to_error()

    if blocking is not None:
        return PipelineResult(outcome=PipelineOutcome.REJECTED, **base,
                              reports=tuple(reports), checkpoints=tuple(checkpoints),
                              error=blocking, profile_ref=media_profile.ref,
                              render_profile_ref=render_profile.ref)

    # Производные ассеты на месте и валидны — вот теперь чекпоинт заслужен.
    checkpoints.append("MEDIA_RENDERED")

    # --- шаг 8: неизменяемая ревизия ИЗ ФИНАЛЬНОГО состава
    revision = ContentRevision(
        id=new_id(f"{project.id}:rev"), project_id=project.id,
        revision_no=revision_no, caption=selection.caption,
        assets=tuple(a.hash_entry() for a in final),
        target_account_ids=(account_id,), schedule_at=selection.schedule_at,
        supersedes_revision_id=supersedes_revision_id, created_at=utc_now(),
        metadata={"content_type": content_type,
                  "media_profile_ref": media_profile.ref,
                  "render_profile_ref": render_profile.ref,
                  "capability": selection.capability})
    checkpoints.append("REVISION_CREATED")

    # --- шаг 9: одобрение привязывается к хешу этой ревизии
    outcome = PipelineOutcome.NEEDS_HUMAN if needs_human else PipelineOutcome.READY
    error = None
    if needs_human:
        error = ProviderError.of(
            "FAIL_PROVIDER_RULE_UNKNOWN",
            safe_detail=f"непроверенные правила: {', '.join(sorted(set(unknown)))}",
            user_action="Правила провайдера для этого типа контента не "
                        "подтверждены. Автоматическая публикация заблокирована — "
                        "подтвердите публикацию вручную.")
    return PipelineResult(
        outcome=outcome, **base, revision=revision, assets=tuple(final),
        reports=tuple(reports), checkpoints=tuple(checkpoints), error=error,
        profile_ref=media_profile.ref, render_profile_ref=render_profile.ref,
        unknown_rules=tuple(sorted(set(unknown))))


def run_for_targets(project: ContentProject, selection: Selection, *,
                    store: MediaStore, profiles: ProfileBundle,
                    **kwargs: Any) -> dict[str, PipelineResult]:
    """Прогнать конвейер по всем аккаунтам выбора.

    Отдельная ревизия на аккаунт — следствие того же `66_CONTENT_RENDER_PROFILES`:
    производные ассеты специфичны для цели, а ревизия несёт их состав.
    """
    return {account_id: run_pipeline(project, selection, account_id=account_id,
                                     store=store, profiles=profiles, **kwargs)
            for account_id in selection.target_account_ids}


def pipeline_diagnostics() -> dict[str, object]:
    """Что конвейер может в этой установке. Для `social-farm check` и отчёта."""
    status = dict(toolchain_status())
    status["video_pipeline"] = ("доступен" if status["transform_supported"]
                                else "NOT_SUPPORTED: нет ffmpeg/ffprobe")
    return status


__all__ = ["AssetReport", "PipelineOutcome", "PipelineResult", "SelectionError",
           "pipeline_diagnostics", "run_for_targets", "run_pipeline"]
