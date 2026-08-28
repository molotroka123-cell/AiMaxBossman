"""Преобразование медиа через ffmpeg. Исходник не трогается никогда.

Этапы из `14_MEDIA_TRANSFORM_PIPELINE`: проба → целостность → профиль рендера →
масштаб/поля → перекодирование → повторная проба → контрольная сумма. Две вещи
в этом списке важнее остальных.

**«Never silently stretch aspect ratio».** Масштабирование всегда идёт связкой
`scale=…:force_original_aspect_ratio=decrease` + `pad`. Кадр вписывается в
целевой прямоугольник и добивается полями. Растянуть кадр этот код не может —
не потому что мы этого не хотим, а потому что такой команды здесь нет.

**«Original remains untouched».** Результат — НОВЫЙ ассет с собственной
контрольной суммой и ссылкой на родителя. Файл исходника лежит по пути, в
который входит его сумма, и открыт только на чтение; перезаписать его
преобразование не может физически.

Отдельно — приёмка результата. Перекодировали не значит получилось: результат
измеряется заново и заново проверяется профилем. Если после преобразования он
всё ещё не проходит, преобразование считается неудавшимся, и наружу идёт
отказ, а не «почти подошло».
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .asset import MediaAsset
from .ingest import ingest_derived
from .probe import (CorruptMedia, ProbeUnavailable, ffmpeg_available, ffmpeg_path,
                    ffprobe_available, probe)
from .profiles import ProviderMediaProfile, RenderProfile
from .rules import MediaFacts, MediaValidation, ValidationOutcome, validate
from .store import MediaStore

#: Потолок на перекодирование. Видео кодируется долго, но не бесконечно:
#: работа, висящая на ffmpeg, держит аренду и не даёт очереди двигаться.
TRANSCODE_TIMEOUT_SECONDS = 900


class TransformUnavailable(RuntimeError):
    """Преобразовывать нечем: нет ffmpeg или ffprobe. Честный `NOT_SUPPORTED`."""


class TransformFailed(RuntimeError):
    """ffmpeg отработал, но результат не годится. Это отказ, а не предупреждение."""


@dataclass(frozen=True, slots=True)
class RenderPlan:
    """Что именно надо сделать с ассетом под конкретную цель.

    План строится ДО работы и остаётся в отчёте: по нему видно, что конвейер
    собирался сделать, даже если сделать не удалось.
    """

    asset_id: str
    render_profile_ref: str
    media_profile_ref: str
    target_width: int | None = None
    target_height: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    container: str | None = None
    operations: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        """Нечего делать — ассет уже годится как есть."""
        return not self.operations


def plan_transform(asset: MediaAsset, validation: MediaValidation,
                   render: RenderProfile) -> RenderPlan:
    """Собрать план из вердикта валидации и профиля рендера."""
    return RenderPlan(
        asset_id=asset.id, render_profile_ref=render.ref,
        media_profile_ref=validation.profile_ref,
        target_width=render.target_width, target_height=render.target_height,
        video_codec=render.target_video_codec, audio_codec=render.target_audio_codec,
        container=render.target_container,
        operations=tuple(validation.transforms))


def toolchain_ready() -> bool:
    """Обе программы на месте. Одной ffmpeg мало: результат надо ещё измерить."""
    return ffmpeg_available() and ffprobe_available()


def _require_toolchain(asset: MediaAsset) -> None:
    missing = [name for name, ok in (("ffmpeg", ffmpeg_available()),
                                     ("ffprobe", ffprobe_available())) if not ok]
    if missing:
        raise TransformUnavailable(
            f"преобразовать ассет {asset.id} нечем: в системе нет "
            f"{', '.join(missing)}. NOT_SUPPORTED — файл не будет опубликован "
            f"без проверенного преобразования")


def build_ffmpeg_args(source: Path, target: Path, plan: RenderPlan) -> list[str]:
    """Команда ffmpeg по плану. Растягивания здесь нет по построению."""
    args = [str(ffmpeg_path()), "-y", "-nostdin", "-v", "error", "-i", str(source)]
    if plan.target_width and plan.target_height:
        width, height = int(plan.target_width), int(plan.target_height)
        # decrease + pad: кадр вписывается целиком и добивается полями.
        # Ни одна из этих операций не меняет пропорции изображения.
        args += ["-vf",
                 f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"]
    if plan.video_codec:
        args += ["-c:v", {"h264": "libx264", "hevc": "libx265",
                          "mjpeg": "mjpeg"}.get(plan.video_codec, plan.video_codec)]
    if plan.audio_codec:
        args += ["-c:a", plan.audio_codec]
    if plan.container == "mp4":
        args += ["-movflags", "+faststart"]
    if plan.container in ("jpeg", "jpg"):
        args += ["-frames:v", "1"]
    args.append(str(target))
    return args


def _suffix_for(plan: RenderPlan) -> str:
    return {"mp4": ".mp4", "mov": ".mov", "jpeg": ".jpg", "jpg": ".jpg",
            "png": ".png", "webp": ".webp"}.get(str(plan.container), ".bin")


def transcode(store: MediaStore, asset: MediaAsset, plan: RenderPlan, *,
              media_profile: ProviderMediaProfile) -> MediaAsset:
    """Перекодировать ассет по плану и вернуть НОВЫЙ ассет.

    Исходный `asset` возвращается неизменным во всех смыслах: его запись не
    меняется, его файл не переписывается, его контрольная сумма остаётся
    прежней. Новый ассет ссылается на него полем `parent_asset_id`.
    """
    _require_toolchain(asset)
    source = store.path_of(asset.storage_ref)     # со сверкой суммы
    with tempfile.TemporaryDirectory(prefix="social-farm-render-") as workdir:
        target = Path(workdir) / f"render{_suffix_for(plan)}"
        args = build_ffmpeg_args(source, target, plan)
        try:
            completed = subprocess.run(args, capture_output=True, check=False,
                                       timeout=TRANSCODE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise TransformFailed(
                f"ffmpeg не уложился в {TRANSCODE_TIMEOUT_SECONDS} с "
                f"на ассете {asset.id}") from exc
        except OSError as exc:
            raise TransformUnavailable(f"ffmpeg не запустился: {exc}") from exc
        if completed.returncode != 0 or not target.is_file():
            raise TransformFailed(
                f"ffmpeg отказался преобразовывать ассет {asset.id}: "
                f"{completed.stderr.decode('utf-8', 'replace').strip()[:300]}")

        # Приёмка: измерить заново и заново проверить профилем.
        try:
            result = probe(target)
        except CorruptMedia as exc:
            raise TransformFailed(
                f"результат преобразования ассета {asset.id} не читается: {exc}") from exc
        except ProbeUnavailable as exc:
            raise TransformUnavailable(str(exc)) from exc
        verdict = validate(MediaFacts.from_probe(result), media_profile)
        if verdict.outcome is not ValidationOutcome.PASS:
            raise TransformFailed(
                f"после преобразования ассет {asset.id} всё ещё не проходит "
                f"проверку: {verdict.outcome.value} — {'; '.join(verdict.reasons)}")

        data = target.read_bytes()

    derived = ingest_derived(
        store, data, parent=asset, render_profile_ref=plan.render_profile_ref,
        provenance={"transform": "ffmpeg", "operations": list(plan.operations),
                    "media_profile_ref": plan.media_profile_ref})
    # Родитель обязан остаться на месте и сойтись — иначе преобразование его
    # всё-таки задело, и это надо увидеть здесь, а не при публикации.
    store.verify(asset)
    return derived


def cleanup_workdir(path: str | Path) -> None:
    shutil.rmtree(Path(path), ignore_errors=True)


__all__ = ["RenderPlan", "TRANSCODE_TIMEOUT_SECONDS", "TransformFailed",
           "TransformUnavailable", "build_ffmpeg_args", "cleanup_workdir",
           "plan_transform", "toolchain_ready", "transcode"]
