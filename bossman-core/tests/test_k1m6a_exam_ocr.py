"""K1m6a exam OCR protocol tests: isolating header OCR effect from lesson effect."""
from __future__ import annotations

import hashlib
import json

import pytest

from bossman.trading_learning import k1m6a_exam as ex

STOP = "8wItpyVUi2k"


def queue(n_after=10, *, dates=True):
    rows = [{"video_id": f"old{i}", "status": "completed"} for i in range(3)]
    rows.append({"video_id": STOP, "status": "processed"})
    for i in range(n_after):
        rows.append({"video_id": f"v{i:02d}", "status": "pending",
                     "upload_date": f"202508{10 + i:02d}" if dates else ""})
    return {"items": rows}


def pinned_split():
    return ex.split_batch(ex.pin_batch(queue(), after_video_id=STOP))


def _situations(tmp_path, split):
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"\xff\xd8fakejpeg")
    sha = hashlib.sha256(frame.read_bytes()).hexdigest()
    sits = []
    for vid in split["train"] + split["validation"] + split["test"]:
        for k in range(3):
            sits.append(ex.Situation(f"{vid}-{k}", vid, 60.0 * (k + 1), "f.jpg", sha, "context"))
    return sits


ANSWER = {"fields": {"price": "114573.28", "cvd": "UNKNOWN"}, "structure": "range", "scenarios": ["a", "b"],
          "confirm": "c", "invalidate": "i", "predicted_comment": "p", "unknowns": ["cvd"],
          "need_more": False, "abstain": False}


def test_build_messages_for_baseline_ocr_without_ocr_text_raises_exam_error(tmp_path):
    split = pinned_split()
    sits = _situations(tmp_path, split)[:1]
    with pytest.raises(ex.ExamError):
        ex.build_messages(sits[0], "BASELINE_OCR", ocr_text=None)


def test_build_messages_for_baseline_with_ocr_text_raises_exam_error(tmp_path):
    split = pinned_split()
    sits = _situations(tmp_path, split)[:1]
    with pytest.raises(ex.ExamError):
        ex.build_messages(sits[0], "BASELINE", ocr_text="OCR text")


def test_build_messages_for_lessons_ocr_includes_ocr_block_before_lessons(tmp_path):
    split = pinned_split()
    sits = _situations(tmp_path, split)[:1]
    ocr_text = "OCR шапки графика: symbol=BTCUSD exchange=COINBASE timeframe=30m O=1 H=2 L=1 C=2 (last_close = C)"
    lessons = ["Lesson 1", "Lesson 2"]
    messages = ex.build_messages(sits[0], "LESSONS_OCR", lessons=lessons, ocr_text=ocr_text)
    assert messages[0]["content"] == ex.SYSTEM_OCR
    user_text = messages[1]["content"][0]["text"]
    assert ocr_text in user_text
    assert "Lesson 1" in user_text
    assert user_text.index(ocr_text) < user_text.index("Lesson 1")


def test_build_messages_for_baseline_does_not_include_ocr_block(tmp_path):
    split = pinned_split()
    sits = _situations(tmp_path, split)[:1]
    messages = ex.build_messages(sits[0], "BASELINE")
    assert messages[0]["content"] == ex.SYSTEM
    assert "OCR" not in messages[1]["content"][0]["text"]


def test_ask_in_baseline_ocr_with_fake_student_includes_ocr_in_row(tmp_path):
    split = pinned_split()
    sits = _situations(tmp_path, split)[:1]
    ocr_text = "OCR шапки графика: symbol=BTCUSD exchange=COINBASE timeframe=30m"
    messages_received = []

    def fake_student(messages):
        messages_received.append(messages)
        return json.dumps({"fields": {"symbol": "BTCUSD", "exchange": "COINBASE", "timeframe": "30m", "last_close": "UNKNOWN"},
                           "structure": "UNKNOWN", "scenarios": [], "confirm": "UNKNOWN", "invalidate": "UNKNOWN",
                           "predicted_comment": "UNKNOWN", "unknowns": [], "need_more": False, "abstain": False})

    rows = ex.ask(sits, split, mode="BASELINE_OCR", student=fake_student, seal_sha="s", criteria_sha="c", base=tmp_path,
                  model_meta={"model": "m"}, ocr_for=lambda s: ocr_text)
    assert rows[0]["ocr"] == ocr_text
    assert ocr_text in messages_received[0][1]["content"][0]["text"]


def test_ask_in_baseline_ocr_without_ocr_for_raises_exam_error(tmp_path):
    split = pinned_split()
    sits = _situations(tmp_path, split)[:1]
    with pytest.raises(ex.ExamError):
        ex.ask(sits, split, mode="BASELINE_OCR", student=lambda m: json.dumps(ANSWER), seal_sha="s",
               criteria_sha="c", base=tmp_path, model_meta={"model": "m"})


def _full_exam(tmp_path, *, baseline, lessons, restart, baseline_ocr, lessons_ocr, restart_ocr,
               baseline_price="114573.28", lessons_ocr_price="114573.28"):
    split = pinned_split()
    crit = ex.pin_criteria({"min_situations": 30, "min_locked": 6})
    sits = _situations(tmp_path, split)
    refs = {s.situation_id: {"fields": {"price": "114573.28"}} for s in sits}
    answers, scores = [], []
    for s in sits:
        for mode, total in (("BASELINE", baseline), ("LESSONS", lessons), ("RESTART_TRANSFER", restart),
                            ("BASELINE_OCR", baseline_ocr), ("LESSONS_OCR", lessons_ocr), ("RESTART_TRANSFER_OCR", restart_ocr)):
            price = baseline_price if mode in ("BASELINE", "LESSONS", "RESTART_TRANSFER") else lessons_ocr_price
            ans = {**ANSWER, "fields": {"price": price, "cvd": "UNKNOWN"}}
            answers.append({"situation_id": s.situation_id, "mode": mode, "seal_sha256": "S",
                            "criteria_sha256": crit["sha256"], "status": "OK", "answer": ans})
            dims = dict(zip(ex.DIMENSIONS, [2, 2, 2, 2, 2]))
            short = 10 - total
            for d in ex.DIMENSIONS:
                take = min(short, 2)
                dims[d] -= take
                short -= take
            scores.append({"situation_id": s.situation_id, "mode": mode, "dims": dims, "grader": "claude"})
    return ex.aggregate(split=split, pinned_criteria=crit, seal_sha="S", situations=sits, references=refs,
                        answers=answers, scores=scores)


def test_aggregate_includes_ocr_effect_in_decomposition_and_fields_by_mode(tmp_path):
    s = _full_exam(tmp_path, baseline=6, lessons=7, restart=7, baseline_ocr=8, lessons_ocr=9, restart_ocr=9,
                   baseline_price="UNKNOWN", lessons_ocr_price="114573.28")
    assert s["decomposition"]["ocr_effect"]["mean"] == 2.0
    assert s["decomposition"]["lessons_effect_with_ocr"]["mean"] == 1.0
    assert s["decomposition"]["lessons_effect_without_ocr"]["mean"] == 1.0
    assert s["decomposition"]["total_fixed_vs_baseline"]["mean"] == 3.0
    assert s["decomposition"]["restart_drop_ocr"]["mean"] == 0.0
    assert s["fields_by_mode"]["BASELINE"] == 0.0
    assert s["fields_by_mode"]["LESSONS_OCR"] == 1.0
    assert s["verdict"] == "FAIL"
    assert s["verdict_ocr"] == "PASS_PRELIMINARY"
    assert "reasons_ocr" in s
