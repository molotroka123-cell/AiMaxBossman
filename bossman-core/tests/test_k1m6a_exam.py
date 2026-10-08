"""K1m6a exam protocol: the harness refuses every shortcut the 08.10 plan forbids."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

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


def test_pin_takes_the_next_ten_after_the_stop_point_and_refuses_to_guess():
    batch = ex.pin_batch(queue(), after_video_id=STOP)
    assert batch["status"] == "PINNED" and [i["video_id"] for i in batch["items"]][:2] == ["v00", "v01"]
    with pytest.raises(ex.ExamError):
        ex.pin_batch(queue(), after_video_id="nope")
    short = ex.pin_batch(queue(5), after_video_id=STOP)
    assert short["status"] == "BATCH_INCOMPLETE" and len(short["items"]) == 5


def test_split_is_whole_videos_chronological_and_test_is_the_latest():
    split = pinned_split()
    assert len(split["train"]) == 6 and len(split["validation"]) == 2 and split["test"] == ["v08", "v09"]
    ex.verify_split(split)
    split["test"] = ["v00", "v09"]
    with pytest.raises(ex.ExamError):
        ex.verify_split(split)                      # edited after pinning


def test_criteria_cannot_change_after_pinning():
    pinned = ex.pin_criteria()
    assert ex.criteria_of(pinned)["locked_mean_min"] == 8.0
    pinned["criteria"]["locked_mean_min"] = 5.0
    with pytest.raises(ex.ExamError):
        ex.criteria_of(pinned)


def test_seal_detects_a_reference_edited_after_sealing(tmp_path):
    ref = tmp_path / "refs.jsonl"
    ref.write_text('{"situation_id": "s1", "fields": {"price": "114573.28"}}\n', encoding="utf-8")
    sealed = ex.seal([ref])
    assert ex.verify_seal(sealed, tmp_path) == []
    ref.write_text('{"situation_id": "s1", "fields": {"price": "1"}}\n', encoding="utf-8")
    assert ex.verify_seal(sealed, tmp_path) == ["refs.jsonl"]


def test_future_speech_in_the_prompt_is_found():
    segs = [{"start": 10, "end": 20, "text": "value area low holds so we look for a reclaim of the open"},
            {"start": 40, "end": 50, "text": "and now we lost the single prints so shorts are in control here"}]
    before = ex.context_until(segs, 30)
    assert "single prints" not in before
    assert ex.leaked_future(before, segs, 30) == []
    assert ex.leaked_future(before + " we lost the single prints so shorts are in control", segs, 30) == ["40.0s"]


def test_stock_phrase_said_before_the_cutoff_is_not_a_future_leak():
    # The author repeats stock phrases; a 6-word overlap with a later segment that already
    # occurs in the permitted past speech proves nothing about the future (real run: ALaeGhKHWIs-00617).
    segs = [{"start": 0, "end": 10, "text": "honestly i don't know if you were here yesterday for the plan"},
            {"start": 40, "end": 50, "text": "i don't know if you were here and now shorts are in control"}]
    before = ex.context_until(segs, 30)
    assert ex.leaked_future(before, segs, 30) == []
    # a genuinely new future sentence is still caught
    assert ex.leaked_future(before + " were here and now shorts are in control", segs, 30) == ["40.0s"]


def test_lessons_from_test_videos_are_contaminated_and_rejected():
    split = pinned_split()
    good = {"lesson_id": "L1", "WHEN": "price returns into value after a failed breakdown",
            "OBSERVE": "CVD rising while OI is flat", "CONFIRM": "acceptance above the open",
            "INVALIDATE": "loss of the session low", "UNKNOWN": "funding not visible",
            "COUNTEREXAMPLE": "news spikes", "provenance": [{"video_id": "v00", "t_s": 120}]}
    assert ex.validate_lesson(good, split)["status"] == "ACTIVE"
    leaked = {**good, "lesson_id": "L2", "provenance": [{"video_id": "v09", "t_s": 5}]}
    assert ex.validate_lesson(leaked, split)["status"] == "REJECTED"
    assert ex.contaminated([good, leaked], split) == ["L2"]
    dated = {**good, "lesson_id": "L3", "CONFIRM": "reclaim of 114,573"}
    assert ex.validate_lesson(dated, split)["status"] == "ARCHIVE_ONLY"
    bare = {**good, "lesson_id": "L4", "WHEN": "buy now"}
    assert ex.validate_lesson(bare, split)["status"] == "REJECTED"


def test_retrieval_drops_anything_that_names_a_test_video():
    split = pinned_split()
    store = ["K1m6a lesson L1\nWHEN: x\nPROVENANCE: v00@120", "K1m6a lesson L9\nPROVENANCE: v09@5",
             "an unrelated note"]
    kept, dropped = ex.retrieve_lessons(lambda q, k: store, "q", split)
    assert kept == [store[0]] and len(dropped) == 1


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


def test_a_cut_off_answer_is_retried_then_recorded_as_bad_json(tmp_path):
    split = pinned_split()
    sits = _situations(tmp_path, split)[:1]
    calls = []

    def cut(messages):
        calls.append(1)
        return '{"fields": {"price": "1145'
    rows = ex.ask(sits, split, mode="BASELINE", student=cut, seal_sha="s", criteria_sha="c", base=tmp_path,
                  model_meta={"model": "m"})
    assert rows[0]["status"] == "BAD_JSON" and rows[0]["attempts"] == ex.MAX_ANSWER_ATTEMPTS
    assert "answer" not in rows[0] and len(calls) == ex.MAX_ANSWER_ATTEMPTS

    ok = ex.ask(sits, split, mode="BASELINE", student=lambda m: json.dumps(ANSWER), seal_sha="s",
                criteria_sha="c", base=tmp_path, model_meta={"model": "m"})
    assert ok[0]["status"] == "OK" and ok[0]["answer"]["fields"]["price"] == "114573.28"


def test_the_student_cannot_answer_before_the_seal_or_see_lessons_in_baseline(tmp_path):
    split = pinned_split()
    sits = _situations(tmp_path, split)[:1]
    with pytest.raises(ex.ExamError):
        ex.ask(sits, split, mode="BASELINE", student=lambda m: "{}", seal_sha="", criteria_sha="c",
               base=tmp_path, model_meta={})
    with pytest.raises(ex.ExamError):
        ex.build_messages(sits[0], "BASELINE", lessons=["L"])


def test_a_number_where_the_reference_says_unknown_is_a_fabrication():
    fm = ex.field_match({"price": "114573.28", "cvd": "UNKNOWN", "oi": "up"},
                        {"price": "114,573.28", "cvd": "1.2", "oi": "UNKNOWN"})
    assert fm["matched"] == 1 and fm["fabricated_numbers"] == 1 and fm["unknown_by_student"] == 1


def _full_exam(tmp_path, *, baseline, lessons, restart):
    split = pinned_split()
    crit = ex.pin_criteria({"min_situations": 30, "min_locked": 6})
    sits = _situations(tmp_path, split)
    refs = {s.situation_id: {"fields": {"price": "114573.28"}} for s in sits}
    answers, scores = [], []
    for s in sits:
        for mode, total in (("BASELINE", baseline), ("LESSONS", lessons), ("RESTART_TRANSFER", restart)):
            answers.append({"situation_id": s.situation_id, "mode": mode, "seal_sha256": "S",
                            "criteria_sha256": crit["sha256"], "status": "OK", "answer": ANSWER})
            dims = dict(zip(ex.DIMENSIONS, [2, 2, 2, 2, 2]))
            short = 10 - total
            for d in ex.DIMENSIONS:
                take = min(short, 2)
                dims[d] -= take
                short -= take
            scores.append({"situation_id": s.situation_id, "mode": mode, "dims": dims, "grader": "claude"})
    return ex.aggregate(split=split, pinned_criteria=crit, seal_sha="S", situations=sits, references=refs,
                        answers=answers, scores=scores)


def test_a_real_gain_with_a_held_restart_is_only_preliminary(tmp_path):
    s = _full_exam(tmp_path, baseline=6, lessons=9, restart=9)
    assert s["verdict"] == "PASS_PRELIMINARY" and s["gain"]["mean"] == 3.0 and s["situations_locked"] == 6


def test_no_gain_and_a_restart_drop_are_named(tmp_path):
    assert _full_exam(tmp_path, baseline=9, lessons=9, restart=9)["verdict"] == "NO_MEASURED_GAIN"
    dropped = _full_exam(tmp_path, baseline=6, lessons=9, restart=7)
    assert dropped["verdict"] == "FAIL" and any("restart" in r for r in dropped["reasons"])


def test_too_few_locked_situations_is_insufficient_not_a_pass(tmp_path):
    split = pinned_split()
    crit = ex.pin_criteria()                       # default: 30 situations, 10 locked; here only 6 locked
    sits = _situations(tmp_path, split)
    s = ex.aggregate(split=split, pinned_criteria=crit, seal_sha="S", situations=sits, references={},
                     answers=[], scores=[])
    assert s["verdict"] == "EXAM_INSUFFICIENT"


def test_an_answer_made_under_other_criteria_is_a_protocol_violation(tmp_path):
    split = pinned_split()
    crit = ex.pin_criteria()
    sits = _situations(tmp_path, split)
    answers = [{"situation_id": sits[0].situation_id, "mode": "BASELINE", "seal_sha256": "S",
                "criteria_sha256": "older", "status": "OK", "answer": ANSWER}]
    s = ex.aggregate(split=split, pinned_criteria=crit, seal_sha="S", situations=sits, references={},
                     answers=answers, scores=[])
    assert s["verdict"] == "PROTOCOL_VIOLATION"


def test_unsupervised_batch_needs_measured_hours_no_hints_and_verified_results():
    t0 = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    log = {"started_at": t0.isoformat(), "ended_at": (t0 + timedelta(hours=2, minutes=5)).isoformat(),
           "teacher_hints": False, "stop_tested": True, "stop_respected": True,
           "items": [{"video_id": "a", "status": "completed", "verified": True, "retries": 1},
                     {"video_id": "b", "status": "missing_source"}]}
    assert ex.unsupervised_verdict(log)["verdict"] == "UNSUPERVISED_WORKFLOW_PASS"
    assert ex.unsupervised_verdict({**log, "teacher_hints": True})["verdict"] == "FAIL"
    assert ex.unsupervised_verdict({**log, "items": [{"video_id": "a", "status": "completed"}]})["verdict"] == "FAIL"
    assert ex.unsupervised_verdict({"items": []})["verdict"] == "UNVERIFIED"


def test_cli_pin_split_criteria_round_trip(tmp_path, capsys):
    q = tmp_path / "queue.json"
    q.write_text(json.dumps(queue()), encoding="utf-8")
    assert ex.main(["pin", "--queue", str(q), "--after", STOP, "--out", str(tmp_path / "b.json")]) == 0
    assert ex.main(["split", "--batch", str(tmp_path / "b.json"), "--out", str(tmp_path / "s.json")]) == 0
    assert ex.main(["criteria", "--out", str(tmp_path / "c.json")]) == 0
    assert json.loads((tmp_path / "s.json").read_text())["test"] == ["v08", "v09"]


def test_situations_come_from_the_archive_with_speech_strictly_before_the_frame(tmp_path):
    batch = ex.pin_batch(queue(), after_video_id=STOP)
    vid = batch["items"][0]["video_id"]
    vdir = tmp_path / "arch" / "raw" / vid
    (vdir / "frames").mkdir(parents=True)
    words = " ".join(f"word{i}" for i in range(80))
    (vdir / "asr.segments.json").write_text(json.dumps({"segments": [
        {"start": 0, "end": 50, "text": words}, {"start": 61, "end": 70, "text": "future secret comment"}]}),
        encoding="utf-8")
    for t in (55, 60, 90):
        (vdir / "frames" / f"f{t}.jpg").write_bytes(b"jpg" + bytes([t]))
    (vdir / "smart_frames.json").write_text(json.dumps({"frames": [
        {"t": 55, "file": "frames/f55.jpg"}, {"t": 60, "file": "frames/f60.jpg"}, {"t": 90, "file": "frames/f90.jpg"}]}),
        encoding="utf-8")
    sits, segs = ex.build_situations(tmp_path / "arch", batch, base=tmp_path)
    assert [s.t_cutoff_s for s in sits] == [55.0, 90.0]            # 60 is within 20 s of 55: same situation
    assert "future secret" not in sits[0].context_before and "future secret" in sits[1].context_before
    assert ex.leaked_future(sits[0].context_before, segs[vid], sits[0].t_cutoff_s) == []
    assert sits[0].frame_file == f"arch/raw/{vid}/frames/f55.jpg"


def test_vtt_is_used_when_there_is_no_local_asr(tmp_path):
    (tmp_path / "subs.x.en.vtt").write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.500\n<c>hello</c> there\n",
                                           encoding="utf-8")
    assert ex.read_segments(tmp_path) == [{"start": 1.0, "end": 2.5, "text": "hello there"}]
    with pytest.raises(ex.ExamError):
        ex.read_segments(tmp_path / "missing")
