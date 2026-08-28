"""Перекодирование: новый ассет, нетронутый исходник, честный отказ без ffmpeg.

Два инварианта, которые здесь доказываются, независимы друг от друга:

1. результат преобразования — НОВЫЙ ассет с новой суммой и ссылкой на
   родителя, а исходник остаётся ровно таким, каким был;
2. без ffmpeg/ffprobe преобразование не «пропускается» и не подделывается — оно
   отказывает с `NOT_SUPPORTED`, и файл не уходит в публикацию.

Второй проверяется в этой среде по-настоящему: ffmpeg здесь нет.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from social_farm.media.ingest import ingest_bytes
from social_farm.media.probe import ffmpeg_available, ffprobe_available
from social_farm.media.profiles import ProviderMediaProfile, load_provider
from social_farm.media.rules import ValidationOutcome, validate_stored_asset
from social_farm.media.transform import (RenderPlan, TransformUnavailable,
                                         build_ffmpeg_args, plan_transform,
                                         toolchain_ready, transcode)

from conftest import make_png

needs_toolchain = pytest.mark.skipif(
    not toolchain_ready(),
    reason="NOT RUN: нет ffmpeg и/или ffprobe — настоящее перекодирование "
           "не выполнялось. Проверено только поведение при их отсутствии.")


def _reel_plan() -> RenderPlan:
    return RenderPlan(asset_id="ast", render_profile_ref="test:REEL:render:v1",
                      media_profile_ref="test:REEL:v1", target_width=1080,
                      target_height=1920, video_codec="h264", audio_codec="aac",
                      container="mp4", operations=("перекодировать",))


# --------------------------------------------------------- без инструментов

def test_transcode_without_ffmpeg_says_not_supported(store, png):
    """Честный отказ вместо тихого пропуска."""
    if toolchain_ready():
        pytest.skip("инструменты есть — этот путь проверяется только без них")
    asset = ingest_bytes(store, png)
    profile = load_provider("instagram").media_profile("IMAGE")
    with pytest.raises(TransformUnavailable, match="NOT_SUPPORTED|нет ffmpeg"):
        transcode(store, asset, _reel_plan(), media_profile=profile)


def test_the_missing_tool_is_named(store, png):
    if toolchain_ready():
        pytest.skip("инструменты есть — этот путь проверяется только без них")
    asset = ingest_bytes(store, png)
    profile = load_provider("instagram").media_profile("IMAGE")
    with pytest.raises(TransformUnavailable) as caught:
        transcode(store, asset, _reel_plan(), media_profile=profile)
    assert "ffmpeg" in str(caught.value) or "ffprobe" in str(caught.value)


def test_a_failed_transform_does_not_touch_the_original(store, png):
    """Отказ преобразования оставляет исходник ровно таким, каким он был."""
    if toolchain_ready():
        pytest.skip("инструменты есть — этот путь проверяется только без них")
    asset = ingest_bytes(store, png)
    profile = load_provider("instagram").media_profile("IMAGE")
    with pytest.raises(TransformUnavailable):
        transcode(store, asset, _reel_plan(), media_profile=profile)
    store.verify(asset)
    assert store.read(asset.storage_ref) == png


def test_toolchain_readiness_needs_both_programs():
    """Одного ffmpeg мало: результат надо ещё измерить."""
    assert toolchain_ready() is (ffmpeg_available() and ffprobe_available())


# --------------------------------------------------------- команда ffmpeg

def test_the_command_never_stretches_the_picture(tmp_path):
    """«Never silently stretch aspect ratio» (`14_MEDIA_TRANSFORM_PIPELINE`).

    Проверяется сама команда: растянуть кадр этот код не может, потому что
    масштабирование всегда идёт связкой `force_original_aspect_ratio=decrease`
    и `pad`. Такой команды здесь просто нет.
    """
    if not ffmpeg_available():
        pytest.skip("NOT RUN: нет ffmpeg — команда не собирается")
    args = build_ffmpeg_args(Path("in.mp4"), Path("out.mp4"), _reel_plan())
    joined = " ".join(args)
    assert "force_original_aspect_ratio=decrease" in joined
    assert "pad=1080:1920" in joined


def test_the_command_shape_is_stable_even_without_ffmpeg(tmp_path, monkeypatch):
    """Сборку команды можно проверить и без установленного ffmpeg."""
    monkeypatch.setattr("social_farm.media.transform.ffmpeg_path",
                        lambda: "/usr/bin/ffmpeg")
    args = build_ffmpeg_args(Path("in.mp4"), Path("out.mp4"), _reel_plan())
    joined = " ".join(args)
    assert "force_original_aspect_ratio=decrease" in joined
    assert "-c:v libx264" in joined and "-c:a aac" in joined
    assert "+faststart" in joined
    assert "setsar" not in joined and "scale=1080:1920 " not in joined


def test_plan_is_built_from_the_verdict_and_the_render_profile(store, png):
    """План говорит, что собирались сделать, — и остаётся в отчёте."""
    bundle = load_provider("instagram")
    asset = ingest_bytes(store, png)
    verdict = validate_stored_asset(store, asset, bundle.media_profile("IMAGE"))
    assert verdict.outcome is ValidationOutcome.PASS_WITH_TRANSFORM
    plan = plan_transform(asset, verdict, bundle.render_profile("IMAGE"))
    assert plan.asset_id == asset.id
    assert (plan.target_width, plan.target_height) == (1080, 1350)
    assert plan.operations, "план без операций ничего не объясняет"
    assert plan.empty is False


# --------------------------------------------------------- настоящее перекодирование

@needs_toolchain
def test_transcode_creates_a_new_asset_and_leaves_the_original_untouched(store):
    """Настоящий ffmpeg: PNG → JPEG под профиль ленты Instagram."""
    source_bytes = make_png(1080, 1350)
    asset = ingest_bytes(store, source_bytes)
    bundle = load_provider("instagram")
    media_profile = bundle.media_profile("IMAGE")
    verdict = validate_stored_asset(store, asset, media_profile)
    plan = plan_transform(asset, verdict, bundle.render_profile("IMAGE"))

    derived = transcode(store, asset, plan, media_profile=media_profile)

    assert derived.id != asset.id
    assert derived.checksum_sha256 != asset.checksum_sha256
    assert derived.parent_asset_id == asset.id
    assert derived.mime == "image/jpeg"
    # Исходник цел во всех смыслах.
    assert asset.checksum_sha256 == __import__(
        "social_farm.media.asset", fromlist=["checksum_of"]).checksum_of(source_bytes)
    store.verify(asset)
    assert store.read(asset.storage_ref) == source_bytes
    # И результат действительно проходит профиль — это приёмка, а не надежда.
    assert validate_stored_asset(store, derived, media_profile).outcome \
        is ValidationOutcome.PASS


@needs_toolchain
def test_transcode_result_is_rejected_when_it_still_does_not_fit(store):
    """Перекодировали не значит получилось.

    Профиль требует того, чего план не даёт, — преобразование обязано
    признать неудачу, а не отдать «почти подошло».
    """
    from social_farm.media.transform import TransformFailed
    asset = ingest_bytes(store, make_png(1080, 1350))
    impossible = ProviderMediaProfile.from_dict({
        "provider": "t", "content_type": "IMAGE", "verified_at": "x",
        "mime_allowlist": ["image/jpeg"], "container_allowlist": ["jpeg", "image2"],
        "codec_allowlist": ["mjpeg"], "audio_codec_allowlist": None,
        "max_bytes": 128,                      # заведомо недостижимо
        "min_width": 100, "max_width": 4000, "min_height": 100, "max_height": 4000,
        "duration_min_s": None, "duration_max_s": None, "aspect_rules": ["4:5"],
        "allow_transcode": True, "allow_downscale": True})
    plan = RenderPlan(asset_id=asset.id, render_profile_ref="t:render",
                      media_profile_ref=impossible.ref, target_width=1080,
                      target_height=1350, video_codec="mjpeg", container="jpeg",
                      operations=("перекодировать",))
    with pytest.raises(TransformFailed, match="всё ещё не проходит"):
        transcode(store, asset, plan, media_profile=impossible)
    store.verify(asset)
