#!/usr/bin/env python3
"""Настоящий медиа-оборот: заготовка -> правка ЧЕРЕЗ BOSSMAN -> рендер -> ffprobe.

Здесь нет ни одного синтетического пути, дающего PASS. Заготовка генерируется
ffmpeg-ом с фиксированными битэкзактными параметрами (детерминизм проверяется
повторной генерацией и сравнением sha256), правка идёт через настоящий командный
слой Video Studio (`bcc.video_studio.commands.apply_command`), рендер — через
настоящий продуктовый рендер (`bcc.video_studio.render.render_project`), а
приёмка делается НЕЗАВИСИМЫМ оракулом на ffprobe/ffmpeg, а не отчётом продукта
о самом себе.

Ключевое правило оракула: ТИП ИЗМЕРЯЕТСЯ, А НЕ ЧИТАЕТСЯ ИЗ ИМЕНИ. ffprobe сам
подбирает демуксер в том числе по расширению, поэтому файл отдаётся ему через
симлинк БЕЗ расширения — формат определяется только по содержимому, и лишь
потом сравнивается с обещанием имени. Страница ошибки, названная `scene.mp4`,
отвергается именно из-за этого несовпадения.

    python tools/media_roundtrip.py                    # оба оборота + отрицательный контроль
    python tools/media_roundtrip.py --output x.json    # то же + улика на диск
    python tools/media_roundtrip.py --only video
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "command-center"

# Обещание имени -> форматы, которые ffprobe обязан ИЗМЕРИТЬ по содержимому.
# Расширения нет в списке -> файл не сертифицируется вообще (честный отказ,
# а не молчаливый пропуск): неизмеримый тип не является доказательством.
NAME_CONTRACT = {
    ".mp4": {"mov,mp4,m4a,3gp,3g2,mj2"},
    ".m4v": {"mov,mp4,m4a,3gp,3g2,mj2"},
    ".mov": {"mov,mp4,m4a,3gp,3g2,mj2"},
    ".mkv": {"matroska,webm"},
    ".webm": {"matroska,webm"},
    ".wav": {"wav"},
    ".flac": {"flac"},
    ".png": {"png_pipe", "image2"},
    ".jpg": {"jpeg_pipe", "image2"},
    ".jpeg": {"jpeg_pipe", "image2"},
}

# Битэкзактные флаги: без них муксер кладёт в файл строку версии сборки и
# заготовка перестаёт быть детерминированной между прогонами.
BITEXACT = ["-fflags", "+bitexact", "-flags", "+bitexact"]


class MediaEvidenceError(RuntimeError):
    """Отказ приёмки. `failures` — измеренные причины, а не общие слова."""

    def __init__(self, message, failures=None, evidence=None):
        super().__init__(message)
        self.failures = list(failures or [])
        self.evidence = evidence or {}


def binaries() -> dict:
    """Нет бинаря — красный отказ, а не зелёный skip (как в media_ci_preflight)."""
    found = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    missing = [name for name, value in found.items() if not value]
    if missing:
        raise MediaEvidenceError("required media tools missing: " + ", ".join(missing),
                                 [f"{name} unavailable" for name in missing])
    return found


def run(argv, *, timeout=600, check=True):
    result = subprocess.run([str(x) for x in argv], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if check and result.returncode:
        tail = result.stderr.decode("utf-8", "replace")[-2000:]
        raise MediaEvidenceError(f"media process failed ({result.returncode}): {tail}")
    return result


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


# --------------------------------------------------------------------------
# независимый оракул типа/содержимого
# --------------------------------------------------------------------------

def measured_type(path: Path, *, count_frames=False) -> dict:
    """Формат и дорожки ПО СОДЕРЖИМОМУ: имя файла ffprobe-у не показываем.

    ffprobe выбирает демуксер в том числе по расширению, поэтому HTML-страница
    с именем `scene.mp4` пробуется как MOV. Симлинк без расширения убирает эту
    подсказку: остаётся только сигнатура содержимого.
    """
    tools = binaries()
    path = Path(path)
    if not path.is_file():
        raise MediaEvidenceError(f"artifact missing: {path}", ["file does not exist"])
    with tempfile.TemporaryDirectory(prefix="roundtrip-probe-") as directory:
        anonymous = Path(directory) / "probe"
        try:
            os.symlink(path.resolve(), anonymous)
        except (OSError, NotImplementedError):      # ФС без симлинков — копируем
            shutil.copyfile(path, anonymous)
        entries = "stream=codec_type,codec_name,width,height,sample_rate,channels"
        if count_frames:
            entries += ",nb_read_frames"
        argv = [tools["ffprobe"], "-v", "error"]
        if count_frames:
            argv += ["-count_frames"]
        argv += ["-show_entries", "format=format_name,duration", "-show_entries", entries,
                 "-of", "json", str(anonymous)]
        result = run(argv, timeout=600, check=False)
        if result.returncode:
            tail = result.stderr.decode("utf-8", "replace").strip()[-500:]
            raise MediaEvidenceError(f"ffprobe could not measure a media type for {path.name}",
                                     [f"measured type unavailable: {tail}"])
        try:
            data = json.loads(result.stdout or b"{}")
        except ValueError as exc:
            raise MediaEvidenceError("ffprobe returned unparsable evidence", [str(exc)]) from exc
    fmt = (data.get("format") or {}).get("format_name")
    streams = data.get("streams") or []
    if not fmt or not streams:
        raise MediaEvidenceError(f"ffprobe could not measure a media type for {path.name}",
                                 ["measured type unavailable: no format/stream recognised"])
    raw_duration = (data.get("format") or {}).get("duration")
    try:
        duration = float(raw_duration) if raw_duration is not None else None
    except (TypeError, ValueError):
        duration = None
    if duration is not None and (not math.isfinite(duration) or duration < 0):
        raise MediaEvidenceError("measured duration is not a finite non-negative number",
                                 ["invalid measured duration"])
    return {"format": fmt, "duration_s": duration, "streams": streams}


def full_decode(path: Path) -> bool:
    """Полное декодирование. Метаданных мало: обрезанный файл всё ещё
    рапортует ffprobe-у корректную длительность и обе дорожки."""
    tools = binaries()
    result = run([tools["ffmpeg"], "-hide_banner", "-v", "error", "-xerror", "-nostdin",
                  "-i", str(path), "-map", "0:v?", "-map", "0:a?", "-f", "null", "-"],
                 timeout=1800, check=False)
    return result.returncode == 0


def verify_artifact(path, expect=None, *, label="artifact") -> dict:
    """Единственная точка приёмки. Возвращает улику или бросает MediaEvidenceError.

    Проверяется: файл существует и ненулевой; ИЗМЕРЕННЫЙ тип совпадает с
    обещанием имени; длительность в допуске; есть ровно столько дорожек,
    сколько заявлено; кодеки/размер совпадают; файл полностью декодируется.
    """
    expect = dict(expect or {})
    path = Path(path)
    failures: list[str] = []
    evidence: dict = {"label": label, "path": str(path), "name_suffix": path.suffix.lower()}

    if not path.is_file():
        raise MediaEvidenceError(f"{label}: output file does not exist", ["file does not exist"], evidence)
    size = path.stat().st_size
    evidence["bytes"] = size
    if size <= 0:
        raise MediaEvidenceError(f"{label}: output file is empty", ["zero-byte output"], evidence)
    minimum = int(expect.get("min_bytes", 1))
    if size < minimum:
        failures.append(f"output smaller than {minimum} bytes (measured {size})")

    suffix = path.suffix.lower()
    if suffix not in NAME_CONTRACT:
        raise MediaEvidenceError(
            f"{label}: '{suffix or '<no suffix>'}' has no measurable type contract; "
            "an unmeasurable artifact is not evidence",
            [f"no measured-type contract for '{suffix}'"], evidence)

    try:
        measurement = measured_type(path, count_frames="frames" in expect)
    except MediaEvidenceError as exc:
        # Имя обещает медиаконтейнер, а содержимое не опознаётся вообще —
        # это ровно тот случай «страница ошибки с именем scene.mp4».
        reason = (f"measured type does not match the name '{path.name}': content is not a "
                  f"recognisable media container at all, while '{suffix}' promises "
                  f"{sorted(NAME_CONTRACT[suffix])}")
        evidence.update(measured_format=None, failures=[reason, *exc.failures], passed=False)
        raise MediaEvidenceError(f"{label}: {reason}", [reason, *exc.failures], evidence) from exc
    evidence.update(measured_format=measurement["format"], measured_duration_s=measurement["duration_s"],
                    streams=measurement["streams"])

    # Улика владельца: тип ИЗМЕРЕН, имя лишь обещает. Несовпадение — отказ.
    if measurement["format"] not in NAME_CONTRACT[suffix]:
        failures.append(f"measured type '{measurement['format']}' does not match the name '{path.name}' "
                        f"(expected one of {sorted(NAME_CONTRACT[suffix])})")

    tracks = {"video": 0, "audio": 0}
    for stream in measurement["streams"]:
        kind = stream.get("codec_type")
        if kind in tracks:
            tracks[kind] += 1
    evidence["tracks"] = tracks
    for kind, wanted in (expect.get("tracks") or {}).items():
        if tracks.get(kind, 0) != wanted:
            failures.append(f"{kind} track count {tracks.get(kind, 0)} != expected {wanted}")

    for kind, key in (("video", "video_codec"), ("audio", "audio_codec")):
        if key not in expect:
            continue
        found = [s.get("codec_name") for s in measurement["streams"] if s.get("codec_type") == kind]
        if expect[key] not in found:
            failures.append(f"{kind} codec {found or 'none'} != expected {expect[key]}")

    video = next((s for s in measurement["streams"] if s.get("codec_type") == "video"), {})
    for key in ("width", "height"):
        if key in expect and video.get(key) != expect[key]:
            failures.append(f"{key} {video.get(key)} != expected {expect[key]}")

    if "duration_s" in expect:
        tolerance = float(expect.get("tolerance_s", 0.06))
        measured = measurement["duration_s"]
        if measured is None:
            failures.append("duration expected but not measurable")
        elif abs(measured - float(expect["duration_s"])) > tolerance:
            failures.append(f"duration {measured}s != expected {expect['duration_s']}s (+-{tolerance})")

    if "frames" in expect:
        raw = video.get("nb_read_frames")
        evidence["decoded_video_frames"] = raw
        if not isinstance(raw, str) or not raw.isdigit():
            failures.append("decoded frame count unavailable")
        elif int(raw) != int(expect["frames"]):
            failures.append(f"decoded frame count {raw} != expected {expect['frames']}")

    decoded = full_decode(path)
    evidence["full_decode"] = decoded
    if not decoded:
        failures.append("full decode failed")

    evidence["sha256"] = digest(path)
    if expect.get("sha256") and expect["sha256"] != evidence["sha256"]:
        failures.append("content hash mismatch")

    evidence["failures"] = failures
    evidence["passed"] = not failures
    if failures:
        raise MediaEvidenceError(f"{label}: " + "; ".join(failures), failures, evidence)
    return evidence


# --------------------------------------------------------------------------
# детерминированные заготовки
# --------------------------------------------------------------------------

def video_fixture(path, *, seconds=2, fps=25, width=160, height=90, tone_hz=440) -> dict:
    """testsrc + sine с фиксированными параметрами. Никаких бинарей в репозитории."""
    tools = binaries()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    run([tools["ffmpeg"], "-hide_banner", "-v", "error", "-nostdin", "-y", "-threads", "1",
         "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=48000:duration={seconds}",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-ar", "48000", "-ac", "2",
         "-shortest", *BITEXACT, "-movflags", "+faststart", str(path)])
    return {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size,
            "seconds": seconds, "fps": fps, "width": width, "height": height}


def image_fixture(path, *, width=320, height=240) -> dict:
    """Один детерминированный кадр testsrc в PNG."""
    tools = binaries()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    run([tools["ffmpeg"], "-hide_banner", "-v", "error", "-nostdin", "-y", "-threads", "1",
         "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate=1:duration=1",
         "-frames:v", "1", *BITEXACT, str(path)])
    return {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size,
            "width": width, "height": height}


def fixture_is_deterministic(builder, directory, suffix, **kwargs) -> dict:
    """Заготовка обязана совпадать побайтово при повторной генерации."""
    directory = Path(directory)
    first = builder(directory / ("determinism-a" + suffix), **kwargs)
    second = builder(directory / ("determinism-b" + suffix), **kwargs)
    identical = first["sha256"] == second["sha256"]
    if not identical:
        raise MediaEvidenceError("fixture is not deterministic across identical invocations",
                                 [f"{first['sha256']} != {second['sha256']}"])
    return {"deterministic": True, "sha256": first["sha256"], "bytes": first["bytes"]}


def mean_rgb(path) -> list:
    """Средний цвет кадра — содержательный оракул: доказывает, что правка
    действительно дошла до пикселей, а не осталась в метаданных проекта."""
    tools = binaries()
    raw = run([tools["ffmpeg"], "-hide_banner", "-v", "error", "-nostdin", "-i", str(path),
               "-frames:v", "1", "-vf", "scale=16:16", "-pix_fmt", "rgb24",
               "-f", "rawvideo", "pipe:1"], timeout=120).stdout
    if len(raw) != 16 * 16 * 3:
        raise MediaEvidenceError("sample frame unavailable", ["sample frame missing"])
    return [round(sum(raw[i::3]) / 256, 3) for i in range(3)]


# --------------------------------------------------------------------------
# оборот Video Studio: правка ЧЕРЕЗ BOSSMAN, рендер продуктовым путём
# --------------------------------------------------------------------------

def _studio():
    """Ленивый импорт: корневые инструменты не тащат зависимости Command Center."""
    if str(STUDIO) not in sys.path:
        sys.path.insert(0, str(STUDIO))
    from bcc.video_studio import commands, model, render
    return commands, model, render


def build_edited_project(media, *, width, height, fps, keep_frames, brightness):
    """Правка идёт ТОЛЬКО через apply_command — настоящий командный слой студии.

    add -> split -> ripple_delete -> effect.apply. Итог измерим снаружи:
    длительность падает вдвое, а яркость кадра растёт.
    """
    commands, model, _ = _studio()
    project = model.new_project("media-roundtrip", "Медиа-оборот")
    sequence = project["sequences"][0]
    sequence.update(width=width, height=height, fps=fps)
    project = commands.apply_command(project, {"type": "media.import", "media": media})[0]
    track_id = sequence["tracks"][0]["id"]
    project = commands.apply_command(project, {"type": "clip.add", "track_id": track_id,
                                               "clip": {"id": "roundtrip-clip", "media_id": media["id"]}})[0]
    project = commands.apply_command(project, {"type": "clip.split", "clip_id": "roundtrip-clip",
                                               "frame": keep_frames})[0]
    tail = [c["id"] for c in project["sequences"][0]["tracks"][0]["clips"] if c["id"] != "roundtrip-clip"]
    if len(tail) != 1:
        raise MediaEvidenceError("split did not produce exactly one tail clip",
                                 [f"tail clips: {tail}"])
    project = commands.apply_command(project, {"type": "clip.ripple_delete", "clip_id": tail[0]})[0]
    project = commands.apply_command(project, {"type": "effect.apply", "clip_id": "roundtrip-clip",
                                               "effect": {"id": "roundtrip-eq", "type": "eq",
                                                          "params": {"brightness": brightness}}})[0]
    model.validate_project(project)
    return project


def video_roundtrip(workdir, *, seconds=2, fps=25, width=160, height=90, brightness=0.35) -> dict:
    """Заготовка -> импорт -> правка через BOSSMAN -> рендер -> независимый ffprobe."""
    import asyncio

    commands, model, render = _studio()
    from bcc.video_studio.media import MediaLibrary

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    determinism = fixture_is_deterministic(video_fixture, workdir / "determinism", ".mp4",
                                           seconds=seconds, fps=fps, width=width, height=height)
    source = video_fixture(workdir / "fixture.mp4", seconds=seconds, fps=fps,
                           width=width, height=height)
    rate = {"num": fps, "den": 1}
    keep_frames = (seconds * fps) // 2

    async def execute():
        media = await MediaLibrary(workdir).import_file(Path(source["path"]))
        edited = build_edited_project(media, width=width, height=height, fps=rate,
                                      keep_frames=keep_frames, brightness=brightness)
        plain = build_edited_project(media, width=width, height=height, fps=rate,
                                     keep_frames=keep_frames, brightness=0.0)
        bright_out = workdir / "render" / "edited.mp4"
        plain_out = workdir / "render" / "baseline.mp4"
        for path in (bright_out, plain_out):
            if path.exists():
                path.unlink()
        product_bright = await render.render_project(edited, workdir, bright_out)
        product_plain = await render.render_project(plain, workdir, plain_out)
        return media, product_bright, product_plain, bright_out, plain_out

    media, product_bright, product_plain, bright_out, plain_out = asyncio.run(execute())

    expect = {"tracks": {"video": 1, "audio": 1}, "video_codec": "h264", "audio_codec": "aac",
              "width": width, "height": height, "duration_s": keep_frames / fps,
              "frames": keep_frames, "min_bytes": 1024}
    evidence = verify_artifact(bright_out, expect, label="video studio export")
    baseline = verify_artifact(plain_out, expect, label="video studio baseline export")

    edited_rgb, baseline_rgb = mean_rgb(bright_out), mean_rgb(plain_out)
    if not sum(edited_rgb) > sum(baseline_rgb) + 3:
        raise MediaEvidenceError("BOSSMAN effect did not change the rendered pixels",
                                 [f"edited {edited_rgb} vs baseline {baseline_rgb}"])
    if digest(Path(source["path"])) != source["sha256"]:
        raise MediaEvidenceError("render mutated its own source fixture", ["source fixture changed"])

    return {"status": "PASS", "fixture": source, "fixture_determinism": determinism,
            "source_duration_s": seconds, "edit_chain": ["media.import", "clip.add", "clip.split",
                                                         "clip.ripple_delete", "effect.apply"],
            "edited_through": "bcc.video_studio.commands.apply_command",
            "rendered_through": "bcc.video_studio.render.render_project",
            "output": evidence, "baseline_output": baseline,
            "mean_rgb": {"edited": edited_rgb, "baseline": baseline_rgb},
            "product_self_report": {"passed": product_bright["verification"]["passed"],
                                    "decoded_video_frames": product_bright["verification"].get("decoded_video_frames"),
                                    "baseline_passed": product_plain["verification"]["passed"]},
            "media_id": media["id"]}


# --------------------------------------------------------------------------
# оборот Image Studio
# --------------------------------------------------------------------------

def image_roundtrip(workdir) -> dict:
    """Заготовка -> хранилище Image Studio -> преобразование -> экспорт -> ffprobe.

    Улика честная и неполная в одном месте: генерация картинок в продукте
    держится на MockImageProvider (детерминированный SVG), реальный провайдер
    не подключён и ffprobe SVG не измеряет. Поэтому здесь доказывается
    НАСТОЯЩИЙ растровый оборот через продуктовое хранилище, а мок-провайдер
    помечен как адаптер CI, а не выдан за генерацию.
    """
    if str(STUDIO) not in sys.path:
        sys.path.insert(0, str(STUDIO))
    from bcc.v2.images_runtime import ImageStorage, MockImageProvider, safe_filename

    tools = binaries()
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    determinism = fixture_is_deterministic(image_fixture, workdir / "determinism", ".png",
                                           width=320, height=240)
    source = image_fixture(workdir / "fixture.png", width=320, height=240)

    storage = ImageStorage(workdir / "images")
    imported = storage.save(f"imports/{safe_filename('media-roundtrip.png')}",
                            Path(source["path"]).read_bytes())
    import_evidence = verify_artifact(imported, {"tracks": {"video": 1}, "video_codec": "png",
                                                 "width": 320, "height": 240,
                                                 "sha256": source["sha256"]},
                                      label="image studio import")

    # Преобразование тем же ffmpeg: собственного image-transform у студии нет.
    transformed = workdir / "transformed.png"
    run([tools["ffmpeg"], "-hide_banner", "-v", "error", "-nostdin", "-y", "-threads", "1",
         "-i", str(storage.resolve_existing(str(imported))),
         "-vf", "crop=160:120:0:0,scale=160:120", "-frames:v", "1", *BITEXACT, str(transformed)])
    saved = storage.save("derived/media-roundtrip-160x120.png", transformed.read_bytes())

    exported = workdir / "export.png"
    exported.write_bytes(storage.resolve_existing(str(saved)).read_bytes())
    export_evidence = verify_artifact(exported, {"tracks": {"video": 1}, "video_codec": "png",
                                                 "width": 160, "height": 120,
                                                 "sha256": digest(saved), "min_bytes": 64},
                                      label="image studio export")

    outside = workdir / "outside-root.png"
    outside.write_bytes(b"x")
    escaped = None
    try:
        storage.resolve_existing(str(outside))
    except PermissionError as exc:
        escaped = str(exc)
    if escaped is None:
        raise MediaEvidenceError("image storage accepted a path outside its media root",
                                 ["media root boundary not enforced"])

    return {"status": "PASS", "fixture": source, "fixture_determinism": determinism,
            "stored_through": "bcc.v2.images_runtime.ImageStorage",
            "import": import_evidence, "export": export_evidence,
            "transform": {"engine": "ffmpeg crop+scale",
                          "note": "у Image Studio нет собственного image-transform; "
                                  "преобразование сделано тем же ffmpeg"},
            "media_root_boundary": escaped,
            "provider_gap": {"provider": MockImageProvider.name,
                             "generation_is_real": False,
                             "note": "генерация картинок в продукте — детерминированный SVG-мок "
                                     "(адаптер CI); реальный провайдер не подключён, ffprobe SVG "
                                     "не измеряет, поэтому оборот доказан на настоящем PNG"}}


# --------------------------------------------------------------------------
# отрицательный контроль
# --------------------------------------------------------------------------

ERROR_PAGE = (b"<!doctype html><html><head><title>502 Bad Gateway</title></head>"
              b"<body><h1>502 Bad Gateway</h1><p>render provider unavailable</p></body></html>\n")


def negative_controls(workdir, *, good_video=None) -> list:
    """Без этого проверка не значит ничего: плохой выход обязан ДАВАТЬ ОТКАЗ."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    tools = binaries()
    if good_video is None:
        good_video = Path(video_fixture(workdir / "good.mp4", seconds=1)["path"])
    good_video = Path(good_video)
    good_expect = {"tracks": {"video": 1, "audio": 1}, "duration_s": 1.0, "min_bytes": 1024}

    cases = []

    def case(name, path, expect, why):
        try:
            verify_artifact(path, expect, label=name)
        except MediaEvidenceError as exc:
            cases.append({"case": name, "rejected": True, "why": why, "failures": exc.failures})
            return
        cases.append({"case": name, "rejected": False, "why": why,
                      "failures": ["ACCEPTED A BAD ARTIFACT"]})

    error_page = workdir / "scene.mp4"
    error_page.write_bytes(ERROR_PAGE)
    case("error page named scene.mp4", error_page, good_expect,
         "измеренный тип не совпал с обещанием имени .mp4")

    empty = workdir / "empty.mp4"
    empty.write_bytes(b"")
    case("zero-byte scene.mp4", empty, good_expect, "нулевой размер")

    truncated = workdir / "truncated.mp4"
    truncated.write_bytes(good_video.read_bytes()[:4096])
    case("truncated export", truncated, good_expect,
         "метаданные ещё врут про длительность, ловит полное декодирование")

    silent = workdir / "silent.mp4"
    run([tools["ffmpeg"], "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", str(good_video),
         "-map", "0:v", "-c", "copy", *BITEXACT, str(silent)])
    case("export without the audio track", silent, good_expect, "пропала звуковая дорожка")

    case("wrong duration claim", good_video, {**good_expect, "duration_s": 5.0},
         "длительность не та, что обещана")

    h264_as_png = workdir / "render.png"
    run([tools["ffmpeg"], "-hide_banner", "-v", "error", "-nostdin", "-y", "-f", "lavfi",
         "-i", "testsrc=size=64x64:rate=25:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         *BITEXACT, "-f", "h264", str(h264_as_png)])
    case("h264 stream named render.png", h264_as_png, {"tracks": {"video": 1}, "video_codec": "png"},
         "измеренный тип h264 не совпал с обещанием имени .png")

    mislabelled = workdir / "matroska-named.mp4"
    run([tools["ffmpeg"], "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", str(good_video),
         "-c", "copy", *BITEXACT, "-f", "matroska", str(mislabelled)])
    case("matroska container named .mp4", mislabelled, good_expect,
         "содержимое читается, но измеренный контейнер не тот, что обещает имя")

    svg = workdir / "mock.svg"
    svg.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"></svg>')
    case("unmeasurable .svg artifact", svg, {"tracks": {"video": 1}},
         "неизмеримый тип не является доказательством")

    return cases


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def roundtrip(only="all") -> dict:
    with tempfile.TemporaryDirectory(prefix="bossman-media-roundtrip-") as directory:
        directory = Path(directory)
        result: dict = {"status": "PASS", "ffmpeg": {}, "sections": {}}
        tools = binaries()
        for name, path in tools.items():
            result["ffmpeg"][name] = run([path, "-version"]).stdout.decode("utf-8", "replace").splitlines()[0]
        if only in ("all", "video"):
            result["sections"]["video_studio"] = video_roundtrip(directory / "video")
        if only in ("all", "image"):
            result["sections"]["image_studio"] = image_roundtrip(directory / "image")
        controls = negative_controls(directory / "negative")
        result["negative_controls"] = controls
        accepted = [c["case"] for c in controls if not c["rejected"]]
        if accepted:
            result["status"] = "FAIL"
            result["reason"] = "negative control accepted a bad artifact: " + ", ".join(accepted)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, help="куда положить JSON-улику")
    parser.add_argument("--only", choices=("all", "video", "image"), default="all")
    args = parser.parse_args()
    try:
        result = roundtrip(args.only)
    except (MediaEvidenceError, OSError, ValueError, subprocess.SubprocessError) as exc:
        failures = list(getattr(exc, "failures", []) or [])
        result = {"status": "FAIL", "reason": str(exc), "failures": failures}
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
