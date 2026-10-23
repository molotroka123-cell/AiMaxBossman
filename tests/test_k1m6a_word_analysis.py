"""tools/k1m6a_word_analysis.py: windows, measured coverage, model chain, idle gate, resume, two videos at a time.

Synthetic captions and a fake Bossman API only: no network, no model, no owner data.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "command-center"))
import k1m6a_word_analysis as kw  # noqa: E402


def _json3(path: Path, sentences: int = 60, words_each: int = 6) -> list[str]:
    """A caption file whose every sentence has ``words_each`` words; returns the sentence texts."""
    events, texts, t = [], [], 0
    for s in range(sentences):
        ws = [f"w{s}x{k}" for k in range(words_each)]
        ws[-1] += "."
        texts.append(" ".join(ws))
        events.append({"tStartMs": t, "segs": [{"utf8": ("" if k == 0 else " ") + w, "tOffsetMs": k * 100}
                                               for k, w in enumerate(ws)]})
        t += 3000
    path.write_text(json.dumps({"events": events}), encoding="utf-8")
    return texts


def test_words_keep_order_and_timing(tmp_path):
    f = tmp_path / "video.en.json3"
    _json3(f, sentences=2, words_each=3)
    words = kw.load_words(f)
    assert [w for _, w in words] == ["w0x0", "w0x1", "w0x2.", "w1x0", "w1x1", "w1x2."]
    assert words[1][0] == 0.1 and words[3][0] == 3.0


def test_windows_are_cut_only_at_sentence_ends_and_lose_no_word(tmp_path):
    f = tmp_path / "video.en.json3"
    _json3(f, sentences=60, words_each=6)
    words = kw.load_words(f)
    wins = kw.make_windows(words, size=50)
    assert sum(len(w) for w in wins) == len(words), "no word may be dropped"
    assert all(w[-1][1].endswith(".") for w in wins[:-1])
    assert all(len(w) >= 50 for w in wins[:-1])


def test_coverage_is_measured_not_trusted():
    window = [(0.0, "a"), (0.1, "b"), (0.2, "c."), (0.3, "d"), (0.4, "e.")]
    full = [{"text": "a b c."}, {"text": "d e."}]
    half = [{"text": "a b c."}]
    assert kw.coverage(full, window)["coverage"] == 1.0
    assert kw.coverage(half, window)["coverage"] == 0.6
    assert kw.coverage([], window)["coverage"] == 0.0


def test_extract_items_tolerates_fences_and_rejects_garbage():
    assert kw.extract_items('```json\n[{"i":1,"text":"x"}]\n```\nthanks') == [{"i": 1, "text": "x"}]
    assert kw.extract_items("no json here") == []
    assert kw.extract_items("[not valid json}") == []
    assert kw.extract_items('{"i":1}') == []


class FakeApi:
    """Records calls; ``script`` maps agent id -> function(prompt) -> (status, answer, cost)."""

    def __init__(self, script, queue=None):
        self.script = script
        self.queue = queue or {"running": None, "waiting": 0}
        self.tasks, self.calls, self.lock = {}, [], threading.Lock()

    def __call__(self, method, path, body=None, **kw_):
        if method == "POST" and path == "/api/tasks":
            status, answer, cost = self.script[body["agent_id"]](body["prompt"])      # the "model" runs outside the lock
            with self.lock:
                tid = len(self.tasks) + 1
                self.tasks[tid] = {"task": {"status": status}, "result": answer, "runs": [{"cost_usd": cost}]}
                self.calls.append(body["agent_id"])
            return {"task": {"id": tid}}
        with self.lock:
            if path.startswith("/api/tasks/"):
                return self.tasks[int(path.rsplit("/", 1)[1])]
            if path == "/api/direct-gen/status":
                return {"queue": self.queue}
        raise AssertionError((method, path))


def _echo(prompt: str):
    """A perfect model: one item per sentence found between the code fences."""
    body = prompt.split("```")[1]
    text = " ".join(t for t in body.split() if not t.startswith("["))
    sents = [s.strip() + "." for s in text.split(".") if s.strip()]
    return "completed", json.dumps([{"i": i, "text": s, "type": "smalltalk", "asr_suspect": ["w0x0"] if i == 0 else []}
                                    for i, s in enumerate(sents)]), 0.0


def _video(root: Path, vid: str, sentences: int = 40) -> None:
    (root / vid).mkdir(parents=True)
    _json3(root / vid / "video.en.json3", sentences=sentences)


def test_a_video_is_analysed_with_full_measured_coverage_and_no_paid_call(tmp_path):
    _video(tmp_path, "vidAAAAAAAA")
    api = FakeApi({25: _echo, 3: _echo, 44: _echo})
    s = kw.analyze_video("vidAAAAAAAA", tmp_path, api, chain=(25, 3, 44), window_words=60, header="H\n", marker="\nM\n")
    assert s["coverage"] == 1.0 and s["windows_ok"] == s["windows"] and s["paid_windows"] == 0
    assert set(api.calls) == {25} and s["status"] == "UNVERIFIED" and s["cost_usd"] == 0
    assert (tmp_path / "vidAAAAAAAA" / "word_analysis" / "summary.json").is_file()


def test_the_chain_falls_back_to_the_next_agent_and_marks_the_paid_window(tmp_path):
    _video(tmp_path, "vidBBBBBBBB", sentences=12)

    def empty(prompt):                       # a reasoning model that burned its budget on thinking
        return "failed", "", 0.004

    def paid(prompt):
        status, answer, _ = _echo(prompt)
        return status, answer, 0.002
    api = FakeApi({25: empty, 3: empty, 44: paid})
    s = kw.analyze_video("vidBBBBBBBB", tmp_path, api, chain=(25, 3, 44), window_words=200, header="H\n", marker="\nM\n")
    assert api.calls[:3] == [25, 3, 44] and s["paid_windows"] == s["windows"] and s["windows_ok"] == s["windows"]
    assert s["cost_usd"] > 0 and s["agents"] == [44]


def test_an_unparseable_answer_is_recorded_as_failed_not_as_coverage(tmp_path):
    _video(tmp_path, "vidCCCCCCCC", sentences=8)
    api = FakeApi({25: lambda p: ("completed", "I think the market will go up!", 0.0)})
    s = kw.analyze_video("vidCCCCCCCC", tmp_path, api, chain=(25,), window_words=200, header="H\n", marker="\nM\n")
    assert s["coverage"] == 0.0 and s["windows_ok"] == 0
    w1 = json.loads((tmp_path / "vidCCCCCCCC" / "word_analysis" / "window-01.json").read_text(encoding="utf-8"))
    assert w1["sentences"] == [] and "market" in w1["raw_answer_if_unparsed"]


def test_rerun_resumes_from_cached_windows_and_makes_no_new_calls(tmp_path):
    _video(tmp_path, "vidDDDDDDDD")
    api = FakeApi({25: _echo})
    kw.analyze_video("vidDDDDDDDD", tmp_path, api, chain=(25,), window_words=60, header="H\n", marker="\nM\n")
    first = len(api.calls)
    kw.analyze_video("vidDDDDDDDD", tmp_path, api, chain=(25,), window_words=60, header="H\n", marker="\nM\n")
    assert first > 0 and len(api.calls) == first


def test_idle_gate_waits_for_the_generation_queue_and_the_owner_marker(tmp_path):
    busy = FakeApi({}, queue={"running": "j1", "waiting": 2})
    idle, why = kw.bossman_is_idle(busy, tmp_path)
    assert idle is False and "generation queue" in why
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        busy.queue = {"running": None, "waiting": 0}          # the queue drains while we wait
    assert kw.wait_idle(busy, sleep=fake_sleep, data_dir=tmp_path) is True and sleeps == [20.0]
    from bcc import owner_activity
    owner_activity.mark("test", data_dir=tmp_path, ttl=600)
    quiet = FakeApi({})
    idle, why = kw.bossman_is_idle(quiet, tmp_path)
    assert idle is False and why, "a live owner marker blocks background analysis"
    assert kw.wait_idle(quiet, sleep=lambda s: None, data_dir=tmp_path, max_wait_s=40) is False


def test_rerun_retries_a_window_that_came_back_empty(tmp_path):
    _video(tmp_path, "vidFFFFFFFF", sentences=8)
    bad = FakeApi({25: lambda p: ("completed", "The user wants me to parse the window [1s]...", 0.0)})
    s1 = kw.analyze_video("vidFFFFFFFF", tmp_path, bad, chain=(25,), window_words=200, header="H\n", marker="\nM\n")
    assert s1["windows_ok"] == 0
    good = FakeApi({25: _echo})
    s2 = kw.analyze_video("vidFFFFFFFF", tmp_path, good, chain=(25,), window_words=200, header="H\n", marker="\nM\n")
    assert good.calls and s2["windows_ok"] == s2["windows"] and s2["coverage"] == 1.0


def test_the_gate_is_asked_before_every_new_window(tmp_path):
    _video(tmp_path, "vidEEEEEEEE")
    api = FakeApi({25: _echo})
    asked = []
    kw.analyze_video("vidEEEEEEEE", tmp_path, api, chain=(25,), window_words=60, header="H\n", marker="\nM\n",
                     gate=lambda: asked.append(len(api.calls)))
    assert asked == list(range(len(asked))) and len(asked) == len(api.calls)


def test_batch_takes_manifest_order_skips_done_videos_and_runs_two_at_a_time(tmp_path):
    ids = [f"vid{n:08d}" for n in range(5)]
    manifest = {"videos": [{"video_id": i} for i in ids]}
    (tmp_path / ids[0] / "word_analysis").mkdir(parents=True)
    (tmp_path / ids[0] / "word_analysis" / "summary.json").write_text("{}", encoding="utf-8")
    assert [v["video_id"] for v in kw.pending_videos(manifest, tmp_path)] == ids[1:]
    assert [v["video_id"] for v in kw.pending_videos(manifest, tmp_path, limit=2)] == ids[1:3]
    live, peak, started = [0], [0], []
    lock = threading.Lock()

    def slow_echo(prompt):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        out = _echo(prompt)
        with lock:
            live[0] -= 1
        return out

    def fetch(vid, root):
        started.append(vid)
        if not (Path(root) / vid / "video.en.json3").exists():
            (Path(root) / vid).mkdir(parents=True, exist_ok=True)
            _json3(Path(root) / vid / "video.en.json3", sentences=10)

    api = FakeApi({25: slow_echo})
    state = kw.run_batch(manifest, tmp_path, api, parallel=2, limit=4, chain=(25,), window_words=200, header="H\n",
                         marker="\nM\n", fetch=fetch)
    assert len(state["done"]) == 4 and state["failed"] == [] and peak[0] == 2
    assert started[:2] == ids[1:3], "the next videos in manifest order go first"
    assert json.loads((tmp_path / "batch_state.json").read_text(encoding="utf-8"))["total_pending"] == 4


def test_one_failing_video_does_not_stop_the_batch(tmp_path):
    manifest = {"videos": [{"video_id": "vidBAD00000"}, {"video_id": "vidGOOD0000"}]}

    def fetch(vid, root):
        if vid == "vidBAD00000":
            raise FileNotFoundError("no English auto-captions")
        _video(Path(root), vid, sentences=6)
    state = kw.run_batch(manifest, tmp_path, FakeApi({25: _echo}), parallel=2, chain=(25,), window_words=200,
                         header="H\n", marker="\nM\n", fetch=fetch)
    assert [d["video_id"] for d in state["done"]] == ["vidGOOD0000"]
    assert state["failed"][0]["video_id"] == "vidBAD00000" and "auto-captions" in state["failed"][0]["error"]
