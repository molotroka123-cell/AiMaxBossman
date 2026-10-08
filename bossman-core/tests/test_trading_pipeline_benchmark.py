"""Пайплайн, адаптеры и бенчмарк: честный статус вместо красивого отчёта."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bossman.trading_learning import adapters, frames as frames_mod
from bossman.trading_learning.benchmark import BenchmarkMode, run_benchmark
from bossman.trading_learning.ingest import (IngestError, approval_subject, ingest_video,
                                             write_manifest)
from bossman.trading_learning.routes import pipeline_status
from bossman.trading_learning.safety import (EvidenceClass, OwnerApproval,
                                             OwnerApprovalRequired, utcnow)

# OpenCV нужен ТОЛЬКО кадрам. Раньше importorskip стоял на уровне модуля, и без
# cv2 (в CI его нет) молча пропускались все 22 теста — приём, CLI, бенчмарк.
try:
    import cv2
    import numpy
except ImportError:
    cv2 = numpy = None
needs_cv2 = pytest.mark.skipif(cv2 is None, reason="opencv (cv2) + numpy not installed")


def approval(subject: str, stage: str = "historical_analysis") -> OwnerApproval:
    return OwnerApproval(subject=subject, stage=stage, granted_by="Timur",
                         granted_at=utcnow())


def make_source(path: Path) -> Path:
    """Источник для приёма: настоящий mp4, если есть cv2, иначе непрозрачные байты.

    Приём только хеширует байты и никогда не декодирует видео, поэтому оба
    варианта — честный вход; так тесты приёма идут и там, где OpenCV нет.
    """
    if cv2 is not None:
        return make_video(path)
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 4)
    return path


def make_video(path: Path, frames: int = 30, fps: float = 10.0) -> Path:
    """Настоящий mp4 средствами cv2 — не фикстура-заглушка, а реальный файл."""
    if cv2 is None:
        pytest.skip("opencv (cv2) + numpy not installed")
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 48))
    assert writer.isOpened(), "cv2 VideoWriter unavailable"
    for i in range(frames):
        writer.write(numpy.full((48, 64, 3), (i * 8) % 255, numpy.uint8))
    writer.release()
    assert path.exists() and path.stat().st_size > 0
    return path


# ------------------------------------------------------------------- ingest
def test_ingest_requires_owner_approval(tmp_path):
    video = make_source(tmp_path / "v.mp4")
    with pytest.raises(OwnerApprovalRequired):
        ingest_video(str(video), approval=None)


def test_ingest_hashes_the_source_immutably(tmp_path):
    video = make_source(tmp_path / "v.mp4")
    rec = ingest_video(str(video), approval=approval(str(video.resolve())))
    again = ingest_video(str(video), approval=approval(str(video.resolve())))
    assert rec.video_hash and len(rec.video_hash) == 64
    assert rec.video_hash == again.video_hash and rec.source_id == again.source_id
    assert rec.evidence_class == EvidenceClass.REAL_SANDBOX.value
    manifest = write_manifest(rec, tmp_path / "manifests")
    assert json.loads(manifest.read_text(encoding="utf-8"))["video_hash"] == rec.video_hash


def test_tampered_video_gets_a_different_hash(tmp_path):
    video = make_source(tmp_path / "v.mp4")
    first = ingest_video(str(video), approval=approval(str(video.resolve())))
    video.write_bytes(video.read_bytes() + b"tampered")
    second = ingest_video(str(video), approval=approval(str(video.resolve())))
    assert first.video_hash != second.video_hash
    assert first.source_id != second.source_id


def test_url_ingest_is_blocked_not_faked():
    url = "https://example.com/stream.mp4"
    rec = ingest_video(url, approval=approval(url))
    assert rec.evidence_class == EvidenceClass.BLOCKED.value
    assert rec.video_hash == ""
    assert "nothing was fetched" in rec.notes


def test_ingest_rejects_non_https_and_missing_files(tmp_path):
    with pytest.raises(IngestError):
        ingest_video("http://example.com/x.mp4", approval=approval("http://example.com/x.mp4"))
    missing = tmp_path / "nope.mp4"
    with pytest.raises(IngestError):
        ingest_video(str(missing), approval=approval(str(missing.resolve())))


# ----------------------------------------------------------------- адаптеры
def test_absent_technology_reports_blocked_not_success():
    """ffmpeg/ASR/OCR отсутствуют — статус BLOCKED и список недостающего."""
    for probe in (adapters.probe_audio, adapters.probe_asr, adapters.probe_ocr):
        cap = probe()
        if not cap.available:
            assert cap.missing, f"{cap.name} must name what is missing"
            assert cap.detail


def test_extract_audio_without_ffmpeg_is_blocked_and_writes_nothing(tmp_path):
    if adapters.probe_audio().available:
        pytest.skip("ffmpeg present in this environment")
    out = tmp_path / "a.wav"
    result = adapters.extract_audio(str(tmp_path / "v.mp4"), str(out))
    assert result.status == "BLOCKED"
    assert result.evidence_class is EvidenceClass.BLOCKED
    assert not out.exists()
    assert "ffmpeg" in result.missing


def test_transcribe_without_asr_is_blocked(tmp_path):
    if adapters.probe_asr().available:
        pytest.skip("ASR engine present in this environment")
    result = adapters.transcribe(str(tmp_path / "a.wav"))
    assert result.status == "BLOCKED" and result.payload is None


def test_chart_ocr_without_engine_is_blocked():
    if adapters.probe_ocr().available:
        pytest.skip("OCR engine present in this environment")
    result = adapters.chart_ocr(["x.png"])
    assert result.status == "BLOCKED"
    assert result.evidence_class is EvidenceClass.BLOCKED


# ------------------------------------------------------------------- кадры
@needs_cv2
def test_frame_extraction_is_real_and_produces_files(tmp_path):
    video = make_video(tmp_path / "v.mp4", frames=30, fps=10.0)
    result = frames_mod.extract_frames(str(video), str(tmp_path / "frames"),
                                       timestamps=[0.0, 1.0, 2.0])
    assert result.status == "OK"
    assert result.evidence_class is EvidenceClass.REAL_SANDBOX
    assert result.payload, "no frames extracted"
    for ref in result.payload:
        assert Path(ref.path).exists() and Path(ref.path).stat().st_size > 0
        assert len(ref.sha256) == 64 and len(ref.dhash) == 16


@needs_cv2
def test_identical_frames_are_deduplicated(tmp_path):
    """Одинаковые кадры не сохраняются дважды — токены и место экономятся."""
    path = tmp_path / "flat.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (64, 48))
    for _ in range(30):
        writer.write(numpy.full((48, 64, 3), 128, numpy.uint8))
    writer.release()
    result = frames_mod.extract_frames(str(path), str(tmp_path / "f2"),
                                       timestamps=[0.0, 0.5, 1.0, 1.5, 2.0])
    assert result.status == "OK"
    assert len(result.payload) == 1
    assert "deduplicated=" in result.reason


@needs_cv2
def test_missing_video_is_an_error_not_an_empty_success(tmp_path):
    result = frames_mod.extract_frames(str(tmp_path / "nope.mp4"), str(tmp_path / "o"))
    assert result.status == "ERROR" and not result.ok


def test_frames_without_opencv_are_blocked_not_an_empty_success(tmp_path):
    """Без OpenCV шаг кадров — BLOCKED с названной причиной и без файлов."""
    if adapters.probe_frames().available:
        pytest.skip("opencv present in this environment")
    video = make_source(tmp_path / "v.mp4")
    out = tmp_path / "frames"
    result = frames_mod.extract_frames(str(video), str(out), timestamps=[0.0, 1.0])
    assert result.status == "BLOCKED" and not result.ok
    assert result.evidence_class is EvidenceClass.BLOCKED
    assert "opencv-python" in result.missing
    assert not result.payload and not out.exists()


# ------------------------------------------------------------------ статус
def test_pipeline_status_reports_blocked_steps_honestly():
    status = pipeline_status()
    assert status["safety"]["trading_execution"] == "OFF"
    assert status["safety"]["paper_trading_only"] is True
    assert status["safety"]["external_write_actions"] == "DENY"
    names = {s["step"] for s in status["steps"]}
    for required in ("ingest_video", "extract_audio", "transcribe", "extract_frames",
                     "chart_ocr", "extract_claims", "normalize_strategy", "verify_claims",
                     "compile_backtest", "run_backtest", "paper_trade", "lesson_builder",
                     "trading_benchmark"):
        assert required in names
    blocked = set(status["blocked_steps"])
    if not adapters.probe_audio().available:
        assert "extract_audio" in blocked
        assert status["pipeline_complete"] is False
        assert status["badge"] == "BLOCKED"


# ---------------------------------------------------------------- бенчмарк
def test_benchmark_never_reports_ready_while_capabilities_are_blocked():
    """Ложный READY — сам по себе дефект. Здесь он невозможен."""
    report = run_benchmark()
    blocked = [n for n, c in report.capabilities.items() if not c["available"]]
    if blocked:
        assert report.verdict == "NOT_READY"
        assert any("BLOCKED" in b for b in report.blockers)


def test_every_benchmark_case_passes_on_its_own_merits():
    report = run_benchmark()
    failed = [r.case_id for r in report.rows if not r.passed]
    assert not failed, f"failing benchmark cases: {failed}"


def test_benchmark_covers_all_four_modes():
    report = run_benchmark()
    modes = {r.mode for r in report.rows}
    assert modes == set(BenchmarkMode)


def test_no_benchmark_row_claims_live_proven():
    report = run_benchmark()
    assert all(r.evidence_class is not EvidenceClass.LIVE_PROVEN for r in report.rows)


def test_sealed_holdout_cannot_be_enumerated():
    from bossman.learning_guard.holdout import SecretHoldout
    sealed = SecretHoldout.seal(["hold_1"])
    assert not hasattr(sealed, "list")
    assert sealed.is_holdout("hold_1") and not sealed.is_holdout("dev_1")


# --------------------------------------------------------------------- CLI
def test_cli_blocked_steps_exit_with_the_blocked_code(capsys):
    from bossman.trading_learning.cli import EXIT_BLOCKED, main
    assert main(["paper_trade"]) == EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "BLOCKED"
    assert payload["trading_execution"] == "OFF"


def test_cli_ingest_without_approval_fails_loudly(capsys, tmp_path):
    from bossman.trading_learning.cli import EXIT_ERROR, main
    video = make_source(tmp_path / "v.mp4")
    assert main(["ingest_video", str(video)]) == EXIT_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "OwnerApprovalRequired"


def test_cli_benchmark_exit_code_matches_the_verdict(capsys):
    from bossman.trading_learning.cli import EXIT_BLOCKED, EXIT_OK, main
    code = main(["trading_benchmark"])
    payload = json.loads(capsys.readouterr().out)
    assert code == (EXIT_OK if payload["verdict"] == "READY" else EXIT_BLOCKED)


def test_cli_status_and_seed_are_readable(capsys):
    from bossman.trading_learning.cli import EXIT_OK, main
    assert main(["status"]) == EXIT_OK
    capsys.readouterr()
    assert main(["seed"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert "SCREENSHOT_OBSERVED" in payload["labels"]


# ------------------------------------- CLI: одобренный относительный путь
# Регрессия: CLI строил одобрение на строку аргумента («clip.mp4»), а приём
# требовал развёрнутый путь — одобренный владельцем относительный путь всегда
# падал с «approval mismatch». Документированный путь владельца (routes.py:
# «... then CLI ingest_video») из рабочей папки был недостижим.
@pytest.mark.parametrize("typed", ["clip.mp4", "./clip.mp4", "sub/../clip.mp4", "~/clip.mp4"])
def test_cli_ingest_with_approval_accepts_the_path_as_typed(capsys, tmp_path, monkeypatch, typed):
    import hashlib
    from bossman.trading_learning.cli import EXIT_OK, main
    (tmp_path / "sub").mkdir()
    video = make_source(tmp_path / "clip.mp4")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert main(["ingest_video", typed, "--approved-by", "Timur"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["locator"] == str(video.resolve())
    assert payload["video_hash"] == hashlib.sha256(video.read_bytes()).hexdigest()
    assert payload["approved_by"] == "Timur"
    assert payload["evidence_class"] == EvidenceClass.REAL_SANDBOX.value


def test_cli_ingest_url_stays_blocked_and_drops_query_secrets(capsys):
    from bossman.trading_learning.cli import EXIT_BLOCKED, main
    url = "https://example.com/stream.mp4?token=SECRET"
    assert main(["ingest_video", url, "--approved-by", "Timur"]) == EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["evidence_class"] == EvidenceClass.BLOCKED.value
    assert payload["video_hash"] == "" and "SECRET" not in json.dumps(payload)


def test_cli_ingest_missing_relative_file_is_an_error_not_ok(capsys, tmp_path, monkeypatch):
    from bossman.trading_learning.cli import EXIT_ERROR, main
    monkeypatch.chdir(tmp_path)
    assert main(["ingest_video", "absent.mp4", "--approved-by", "Timur"]) == EXIT_ERROR
    assert json.loads(capsys.readouterr().out)["error"] == "IngestError"


# Негативные контроли: строгость самого приёма не ослаблена.
def test_library_still_refuses_approval_for_a_different_file(tmp_path):
    approved = make_source(tmp_path / "approved.mp4")
    other = tmp_path / "other.mp4"
    other.write_bytes(b"a different clip")
    with pytest.raises(OwnerApprovalRequired):
        ingest_video(str(other), approval=approval(str(approved.resolve())))


def test_library_still_refuses_an_unresolved_approval_subject(tmp_path, monkeypatch):
    make_source(tmp_path / "clip.mp4")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(OwnerApprovalRequired):
        ingest_video("clip.mp4", approval=approval("clip.mp4"))
    # Каноничный предмет из approval_subject — ровно то, что приём примет.
    rec = ingest_video("clip.mp4", approval=approval(approval_subject("clip.mp4")))
    assert rec.locator == str((tmp_path / "clip.mp4").resolve())


def test_approval_subject_keeps_urls_verbatim():
    url = "https://example.com/a.mp4?x=1"
    assert approval_subject(url) == url
