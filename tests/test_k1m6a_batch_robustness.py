"""K1m6a YouTube batch: one stuck video does not end the batch, and only evidence counts as evidence.

Found by the night-2026-09-30 audit of tools/k1m6a_youtube_batch.py:
* `subprocess.TimeoutExpired` from the per-video ingest was not caught, so one slow video crashed the whole
  batch and no summary was written;
* `independent_episodes = len(videos) - duplicates` counted FAIL and NOT_RUN videos as independent evidence.
No network, no yt-dlp, no ingest process: discovery and the subprocess runner are replaced.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import k1m6a_youtube_batch as batch  # noqa: E402


def rows(*ids: str) -> list[dict]:
    return [{"video_id": i, "url": f"https://example.invalid/watch?v={i}", "upload_date": "2026-08-15",
             "title": i, "duration": 60, "channel": "k", "channel_id": "c", "availability": "public",
             "learning_status": "UNVERIFIED"} for i in ids]


class Ingest:
    """Stand-in for `_run` of the per-video ingest script. `behaviour[video_id]` = "pass" | "fail" | "timeout";
    `content[video_id]` = the transcript text that decides the episode fingerprint."""

    def __init__(self, behaviour: dict[str, str], content: dict[str, str] | None = None):
        self.behaviour = behaviour
        self.content = content or {}
        self.calls: list[str] = []
        self.timeouts: list[int] = []

    def __call__(self, cmd, *, timeout):
        url = cmd[2]
        video_id = url.rsplit("=", 1)[1]
        inbox = Path(cmd[cmd.index("--output-root") + 1])
        self.calls.append(video_id)
        self.timeouts.append(timeout)
        how = self.behaviour.get(video_id, "pass")
        if how == "timeout":
            raise subprocess.TimeoutExpired(cmd, timeout, output=b"frames 3/360 ...", stderr=None)
        if how == "fail":
            return subprocess.CompletedProcess(cmd, 1, "", "asr model missing")
        (inbox / video_id).mkdir(parents=True, exist_ok=True)
        (inbox / video_id / "candidate_cases.jsonl").write_text(
            json.dumps({"timestamp_seconds": 1, "transcript_excerpt": self.content.get(video_id, video_id),
                        "observation": {"price": 1}}) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"ok": True, "video": video_id}) + "\n", "")


def run_batch(monkeypatch, tmp_path, ids, ingest, *extra):
    monkeypatch.setattr(batch, "discover", lambda *a, **k: rows(*ids))
    monkeypatch.setattr(batch, "_run", ingest)
    code = batch.main(["--root", str(tmp_path), *extra])
    manifest = json.loads((tmp_path / "batch-manifest.json").read_text(encoding="utf-8"))
    return code, manifest


def test_one_timed_out_video_is_recorded_as_fail_and_the_batch_goes_on(monkeypatch, tmp_path, capsys):
    ingest = Ingest({"b": "timeout"}, {"a": "alpha", "b": "beta", "c": "gamma"})
    code, manifest = run_batch(monkeypatch, tmp_path, ["a", "b", "c"], ingest)
    assert ingest.calls == ["a", "b", "c"]                            # the video after the stuck one still ran
    assert code == 1                                                  # a failure is reported, not hidden
    by_id = {v["video_id"]: v for v in manifest["videos"]}
    assert by_id["a"]["ingest"]["status"] == "PASS" and by_id["c"]["ingest"]["status"] == "PASS"
    stuck = by_id["b"]["ingest"]
    assert stuck["status"] == "FAIL" and stuck["timed_out"] is True and stuck["returncode"] is None
    assert "timed out" in stuck["reason"] and "frames 3/360" in stuck["stdout_tail"]
    assert manifest["summary"] == {"discovered": 3, "ingested_pass": 2, "failed": 1, "duplicates": 0,
                                   "independent_episodes": 2, "not_run": 0}
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["ok"] is False and printed["failed"] == 1 and printed["independent_episodes"] == 2


def test_the_manifest_is_written_after_every_video_even_when_a_later_one_times_out(monkeypatch, tmp_path):
    ingest = Ingest({"c": "timeout"})
    code, manifest = run_batch(monkeypatch, tmp_path, ["a", "b", "c"], ingest)
    assert [v["video_id"] for v in manifest["videos"]] == ["a", "b", "c"]
    assert "summary" in manifest and code == 1


def test_a_rerun_reuses_pass_rows_and_retries_only_the_failed_video(monkeypatch, tmp_path):
    first = Ingest({"b": "timeout"}, {"a": "alpha", "b": "beta", "c": "gamma"})
    run_batch(monkeypatch, tmp_path, ["a", "b", "c"], first)
    second = Ingest({}, {"a": "alpha", "b": "beta", "c": "gamma"})
    code, manifest = run_batch(monkeypatch, tmp_path, ["a", "b", "c"], second)
    assert second.calls == ["b"] and code == 0
    assert manifest["summary"]["independent_episodes"] == 3 and manifest["summary"]["failed"] == 0


def test_independent_episodes_count_only_passed_unique_evidence(monkeypatch, tmp_path):
    # a: PASS, b: a re-upload of a (same content), c: FAIL, d: PASS with its own content
    ingest = Ingest({"c": "fail"}, {"a": "same", "b": "same", "d": "other"})
    code, manifest = run_batch(monkeypatch, tmp_path, ["a", "b", "c", "d"], ingest)
    by_id = {v["video_id"]: v for v in manifest["videos"]}
    assert by_id["b"]["learning_status"] == "DUPLICATE_EVIDENCE" and by_id["b"]["duplicate_of"] == "a"
    assert manifest["summary"] == {"discovered": 4, "ingested_pass": 3, "failed": 1, "duplicates": 1,
                                   "independent_episodes": 2, "not_run": 0}   # was 3: the FAIL counted as evidence
    assert code == 1


def test_discover_only_claims_no_independent_episodes(monkeypatch, tmp_path):
    ingest = Ingest({})
    code, manifest = run_batch(monkeypatch, tmp_path, ["a", "b"], ingest, "--discover-only")
    assert ingest.calls == [] and code == 0
    assert manifest["summary"]["not_run"] == 2 and manifest["summary"]["independent_episodes"] == 0


def test_a_clean_batch_still_counts_every_unique_passed_video(monkeypatch, tmp_path):
    """Negative control: nothing failed, nothing duplicated -> every video is an independent episode."""
    ingest = Ingest({}, {"a": "x", "b": "y", "c": "z"})
    code, manifest = run_batch(monkeypatch, tmp_path, ["a", "b", "c"], ingest)
    assert code == 0 and manifest["summary"]["independent_episodes"] == 3


def test_ingest_one_passes_its_timeout_to_the_runner_and_reports_it(monkeypatch, tmp_path):
    seen = {}

    def runner(cmd, *, timeout):
        seen["timeout"] = timeout
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(batch, "_run", runner)
    result = batch.ingest_one(rows("a")[0], inbox=tmp_path, frame_interval=30, max_frames=10, timeout=7)
    assert seen["timeout"] == 7 and result["status"] == "FAIL" and "7 s" in result["reason"]
    assert result["stdout_tail"] == "" and result["stderr_tail"] == ""
    assert batch.INGEST_TIMEOUT == 4 * 3600                              # the default did not change
