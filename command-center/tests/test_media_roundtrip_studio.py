"""Настоящий медиа-оборот обеих студий: заготовка -> правка -> рендер -> ffprobe.

Почему тесты здесь, а не в корневом `tests/`: корневой CI ставит только
`pytest pytest-timeout psutil httpx pyyaml`, а оборот Image Studio идёт через
настоящее приложение Command Center (фикстура `env` -> sqlalchemy/aiosqlite).
Это ровно тот дефект BL-085, который уже один раз стоил нам красного корня.
FFmpeg в этом наборе гарантирован шагом «FFmpeg для Video Studio» в
.github/workflows/command-center-ci.yml, поэтому skip/xfail здесь нет:
отсутствие бинаря обязано быть красным, а не зелёным.
"""
from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("media_roundtrip", ROOT / "tools" / "media_roundtrip.py")
roundtrip = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(roundtrip)

MediaEvidenceError = roundtrip.MediaEvidenceError


def test_media_binaries_are_mandatory_not_optional():
    """Нет ffmpeg/ffprobe — падаем с именем бинаря, а не «пропускаем»."""
    tools = roundtrip.binaries()
    assert tools["ffmpeg"] and tools["ffprobe"]


def test_video_fixture_is_deterministic(tmp_path):
    """Заготовка генерируется, а не лежит бинарём в репозитории, и повторяется побайтово."""
    proof = roundtrip.fixture_is_deterministic(roundtrip.video_fixture, tmp_path, ".mp4", seconds=1)
    assert proof["deterministic"] and proof["bytes"] > 1024
    again = roundtrip.video_fixture(tmp_path / "third.mp4", seconds=1)
    assert again["sha256"] == proof["sha256"]


def test_image_fixture_is_deterministic(tmp_path):
    proof = roundtrip.fixture_is_deterministic(roundtrip.image_fixture, tmp_path, ".png",
                                               width=320, height=240)
    assert proof["deterministic"] and proof["bytes"] > 64


def test_video_studio_roundtrip_produces_a_measured_real_file(tmp_path):
    """Полный оборот продуктовым путём: apply_command -> render_project -> ffprobe."""
    result = roundtrip.video_roundtrip(tmp_path, seconds=2, fps=25, width=160, height=90)
    assert result["status"] == "PASS"
    assert result["edited_through"] == "bcc.video_studio.commands.apply_command"
    assert result["rendered_through"] == "bcc.video_studio.render.render_project"

    output = result["output"]
    assert output["passed"] and output["failures"] == []
    assert output["bytes"] > 1024                        # файл существует и ненулевой
    assert output["measured_format"] == "mov,mp4,m4a,3gp,3g2,mj2"
    assert output["measured_duration_s"] == pytest.approx(1.0, abs=0.06)
    assert output["decoded_video_frames"] == "25"
    assert output["tracks"] == {"video": 1, "audio": 1}
    assert output["full_decode"] is True
    codecs = {s["codec_type"]: s["codec_name"] for s in output["streams"]}
    assert codecs == {"video": "h264", "audio": "aac"}
    video = next(s for s in output["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (160, 90)


def test_bossman_edit_is_visible_in_the_measured_output(tmp_path):
    """Правка идёт ЧЕРЕЗ BOSSMAN и обязана менять измеримый выход, а не только проект.

    `clip.split` + `clip.ripple_delete` режут двухсекундный исходник пополам,
    а `effect.apply(eq.brightness)` осветляет реальные пиксели. И то и другое
    измеряется снаружи: длительность и средний цвет кадра.
    """
    result = roundtrip.video_roundtrip(tmp_path, seconds=2, fps=25)
    assert result["source_duration_s"] == 2
    assert result["output"]["measured_duration_s"] == pytest.approx(1.0, abs=0.06)
    assert "clip.split" in result["edit_chain"] and "effect.apply" in result["edit_chain"]
    edited, baseline = result["mean_rgb"]["edited"], result["mean_rgb"]["baseline"]
    assert sum(edited) > sum(baseline) + 3, (edited, baseline)


def test_render_does_not_mutate_its_own_source_fixture(tmp_path):
    fixture = roundtrip.video_fixture(tmp_path / "source.mp4", seconds=1)
    result = roundtrip.video_roundtrip(tmp_path / "work", seconds=1, fps=25)
    assert result["fixture"]["sha256"] == fixture["sha256"]
    assert roundtrip.digest(tmp_path / "source.mp4") == fixture["sha256"]


def test_image_storage_roundtrip_produces_a_measured_real_file(tmp_path):
    """Оборот Image Studio через продуктовое хранилище ImageStorage."""
    result = roundtrip.image_roundtrip(tmp_path)
    assert result["status"] == "PASS"
    assert result["stored_through"] == "bcc.v2.images_runtime.ImageStorage"
    assert result["import"]["measured_format"] == "png_pipe"
    assert result["import"]["sha256"] == result["fixture"]["sha256"]
    export = result["export"]
    assert export["passed"] and export["measured_format"] == "png_pipe"
    assert export["bytes"] > 64 and export["full_decode"] is True
    video = next(s for s in export["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (160, 120)
    # Улика честности: генерация картинок в продукте — мок, а не провайдер.
    assert result["provider_gap"]["generation_is_real"] is False


async def test_image_studio_api_roundtrip_serves_a_measured_real_png(env, tmp_path):
    """Импорт -> правка -> экспорт через НАСТОЯЩИЙ HTTP-контур Image Studio."""
    fixture = roundtrip.image_fixture(tmp_path / "fixture.png", width=320, height=240)
    raw = Path(fixture["path"]).read_bytes()

    collection = await env.client.post("/api/images/collections", json={"name": "Оборот"})
    assert collection.status_code == 200
    created = await env.client.post("/api/images/assets/import", json={
        "filename": "roundtrip.png",
        "data_base64": base64.b64encode(raw).decode(),
        "title": "Заготовка оборота",
        "collection_id": collection.json()["id"],
        "tags": ["roundtrip"],
    })
    assert created.status_code == 200
    asset = created.json()
    assert asset["file_bytes"] == len(raw)

    patched = await env.client.patch(f"/api/images/assets/{asset['id']}",
                                     json={"favorite": True, "tags": ["roundtrip", "proof"]})
    assert patched.status_code == 200 and patched.json()["favorite"] is True

    served = await env.client.get(asset["file_url"])
    assert served.status_code == 200
    exported = tmp_path / "exported.png"
    exported.write_bytes(served.content)

    evidence = roundtrip.verify_artifact(exported, {"tracks": {"video": 1}, "video_codec": "png",
                                                    "width": 320, "height": 240,
                                                    "sha256": fixture["sha256"], "min_bytes": 64},
                                         label="images api export")
    assert evidence["passed"] and evidence["measured_format"] == "png_pipe"
    assert evidence["bytes"] == len(raw) and evidence["full_decode"] is True


async def test_html_page_named_png_is_not_a_real_image_asset(env, tmp_path):
    """Байты, которые отдаёт студия, обязаны выдерживать измерение типа.

    Сам эндпоинт импорта сегодня доверяет расширению имени, а не измеренному
    типу (bcc/features/images.py — не наша зона, правка за владельцем). Тест
    честен к обоим исходам: если эндпоинт отказал — хорошо; если принял, то
    отданный файл всё равно не является картинкой, и это здесь доказано.
    """
    page = roundtrip.ERROR_PAGE
    created = await env.client.post("/api/images/assets/import", json={
        "filename": "screenshot.png",
        "data_base64": base64.b64encode(page).decode(),
        "title": "Страница ошибки",
    })
    if created.status_code != 200:
        assert created.status_code in (415, 422)
        return
    served = await env.client.get(created.json()["file_url"])
    assert served.status_code == 200
    path = tmp_path / "screenshot.png"
    path.write_bytes(served.content)
    with pytest.raises(MediaEvidenceError, match="does not match the name"):
        roundtrip.verify_artifact(path, {"tracks": {"video": 1}}, label="images api export")


# --------------------------------------------------------------------------
# отрицательный контроль: без него проверка выше не значит ничего
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def good_export(tmp_path_factory):
    directory = tmp_path_factory.mktemp("good-export")
    return Path(roundtrip.video_fixture(directory / "good.mp4", seconds=1)["path"])


GOOD_EXPECT = {"tracks": {"video": 1, "audio": 1}, "duration_s": 1.0, "min_bytes": 1024}


def test_a_legitimate_export_passes(good_export):
    """Пара к отрицательному контролю: законный случай обязан проходить."""
    evidence = roundtrip.verify_artifact(good_export, GOOD_EXPECT, label="good export")
    assert evidence["passed"] and evidence["measured_format"] == "mov,mp4,m4a,3gp,3g2,mj2"
    assert evidence["measured_duration_s"] == pytest.approx(1.0, abs=0.06)


def test_error_page_named_scene_mp4_is_rejected_for_type_mismatch(tmp_path):
    """Прямое требование владельца: отказ ИМЕННО из-за несовпадения типа с именем."""
    scene = tmp_path / "scene.mp4"
    scene.write_bytes(roundtrip.ERROR_PAGE)
    with pytest.raises(MediaEvidenceError) as caught:
        roundtrip.verify_artifact(scene, GOOD_EXPECT, label="fake render")
    reason = "; ".join(caught.value.failures)
    assert "does not match the name 'scene.mp4'" in reason
    assert "not a recognisable media container" in reason


def test_zero_byte_output_is_rejected(tmp_path):
    empty = tmp_path / "scene.mp4"
    empty.write_bytes(b"")
    with pytest.raises(MediaEvidenceError) as caught:
        roundtrip.verify_artifact(empty, GOOD_EXPECT, label="empty render")
    assert "zero-byte output" in caught.value.failures


def test_truncated_output_survives_metadata_but_fails_decoding(good_export, tmp_path):
    """Метаданные обрезанного файла всё ещё врут про длительность и дорожки —
    отказ даёт только полное декодирование."""
    truncated = tmp_path / "truncated.mp4"
    truncated.write_bytes(good_export.read_bytes()[:4096])
    measured = roundtrip.measured_type(truncated)
    assert measured["format"] == "mov,mp4,m4a,3gp,3g2,mj2"        # метаданные «в порядке»
    assert measured["duration_s"] == pytest.approx(1.0, abs=0.06)  # и длительность тоже
    with pytest.raises(MediaEvidenceError) as caught:
        roundtrip.verify_artifact(truncated, GOOD_EXPECT, label="truncated render")
    assert "full decode failed" in caught.value.failures


def test_missing_audio_track_is_rejected(good_export, tmp_path):
    silent = tmp_path / "silent.mp4"
    roundtrip.run([roundtrip.binaries()["ffmpeg"], "-hide_banner", "-v", "error", "-nostdin", "-y",
                   "-i", str(good_export), "-map", "0:v", "-c", "copy", *roundtrip.BITEXACT, str(silent)])
    with pytest.raises(MediaEvidenceError) as caught:
        roundtrip.verify_artifact(silent, GOOD_EXPECT, label="silent render")
    assert any("audio track count 0" in f for f in caught.value.failures)


def test_wrong_duration_claim_is_rejected(good_export):
    with pytest.raises(MediaEvidenceError) as caught:
        roundtrip.verify_artifact(good_export, {**GOOD_EXPECT, "duration_s": 5.0}, label="wrong length")
    assert any("duration 1.0s != expected 5.0s" in f for f in caught.value.failures)


def test_wrong_frame_count_is_rejected(good_export):
    with pytest.raises(MediaEvidenceError) as caught:
        roundtrip.verify_artifact(good_export, {**GOOD_EXPECT, "frames": 99}, label="wrong frames")
    assert any("decoded frame count" in f for f in caught.value.failures)


def test_matroska_named_mp4_is_rejected_for_type_mismatch(good_export, tmp_path):
    """Содержимое читается, но контейнер не тот, что обещает имя."""
    mislabelled = tmp_path / "scene.mp4"
    roundtrip.run([roundtrip.binaries()["ffmpeg"], "-hide_banner", "-v", "error", "-nostdin", "-y",
                   "-i", str(good_export), "-c", "copy", *roundtrip.BITEXACT,
                   "-f", "matroska", str(mislabelled)])
    with pytest.raises(MediaEvidenceError) as caught:
        roundtrip.verify_artifact(mislabelled, GOOD_EXPECT, label="mislabelled render")
    assert any("measured type 'matroska,webm' does not match the name" in f
               for f in caught.value.failures)


def test_h264_stream_named_png_is_rejected(tmp_path):
    named = tmp_path / "render.png"
    roundtrip.run([roundtrip.binaries()["ffmpeg"], "-hide_banner", "-v", "error", "-nostdin", "-y",
                   "-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=1",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", *roundtrip.BITEXACT,
                   "-f", "h264", str(named)])
    with pytest.raises(MediaEvidenceError) as caught:
        roundtrip.verify_artifact(named, {"tracks": {"video": 1}, "video_codec": "png"},
                                  label="h264 named png")
    assert any("measured type 'h264' does not match the name" in f for f in caught.value.failures)


def test_unmeasurable_artifact_is_refused_rather_than_certified(tmp_path):
    """Неизмеримый тип — это INSUFFICIENT_EVIDENCE, а не зелёный PASS."""
    svg = tmp_path / "mock.svg"
    svg.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"></svg>')
    with pytest.raises(MediaEvidenceError) as caught:
        roundtrip.verify_artifact(svg, {"tracks": {"video": 1}}, label="svg mock")
    assert any("no measured-type contract" in f for f in caught.value.failures)


def test_missing_output_file_is_rejected(tmp_path):
    with pytest.raises(MediaEvidenceError) as caught:
        roundtrip.verify_artifact(tmp_path / "never-rendered.mp4", GOOD_EXPECT, label="absent render")
    assert "file does not exist" in caught.value.failures


def test_negative_control_battery_rejects_every_bad_artifact(tmp_path, good_export):
    """Батарея из CLI: каждый плохой выход обязан быть отвергнут."""
    cases = roundtrip.negative_controls(tmp_path, good_video=good_export)
    accepted = [c["case"] for c in cases if not c["rejected"]]
    assert accepted == [], accepted
    assert len(cases) >= 8
