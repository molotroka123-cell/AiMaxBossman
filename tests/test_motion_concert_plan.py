"""Motion concert planner: CPU-only, no ffmpeg/GPU needed. Synthetic analysis.json -> full plan checks."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "motion_concert"))
import concert  # noqa: E402

DURATION = 168.829388


def _synthetic_analysis() -> dict:
    cuts = [0.0, 1.02, 13.0, 25.9, 37.9, 48.5, 55.6, 62.7, 69.8, 77.4, 87.6, 94.7, 120.0, 131.9, 139.0, 146.1,
            155.0, 166.1, 168.79]
    energy = ["low", "low", "mid", "low", "mid", "high", "high", "low", "mid", "mid", "high", "high", "low", "mid",
              "high", "high", "mid", "low"]
    sections = [{"index": i, "start_s": a, "end_s": b, "dur_s": b - a, "mean_rms": 0.1 + 0.05 * (e == "high"),
                 "boundary_confidence": 0.5, "energy": e}
                for i, (a, b, e) in enumerate(zip(cuts, cuts[1:], energy))]
    return {"tempo_bpm": 136.0, "beats": 379, "first_beat_s": 0.093, "sections": sections}


def _plan(tmp_path: Path) -> dict:
    an = tmp_path / "analysis.json"
    an.write_text(json.dumps(_synthetic_analysis()), encoding="utf-8")
    refs = tmp_path / "refs"
    refs.mkdir()
    for f in concert.REFS.values():
        (refs / f).write_bytes(b"fake png " + f.encode())
    rc = concert.main(["--project", str(tmp_path), "--analysis", str(an), "--refs", str(refs),
                       "--audio", str(tmp_path / "missing.mp3"), "--duration", str(DURATION), "plan"])
    assert rc == 0
    return json.loads((tmp_path / "pipeline" / "work" / "manifest.json").read_text(encoding="utf-8"))


def test_plan_covers_full_timeline_without_overlap(tmp_path):
    shots = sorted(_plan(tmp_path)["shots"], key=lambda s: s["start_s"])
    assert shots[0]["start_s"] == 0.0
    assert abs(shots[-1]["end_s"] - DURATION) < 1e-6
    for a, b in zip(shots, shots[1:]):
        assert abs(a["end_s"] - b["start_s"]) < 1e-6, (a["id"], b["id"])   # no gap, no overlap
        assert a["start_frame"] < b["start_frame"]
    assert abs(sum(s["end_s"] - s["start_s"] for s in shots) - DURATION) < 1e-4
    for s in shots:
        assert 0 < s["end_s"] - s["start_s"] <= concert.S2V_MAX_S + 1e-6
        assert s["reference"]["sha256"] and s["seed"] and s["status"] == "planned"


def test_plan_split_is_about_70_30_with_rotating_inserts(tmp_path):
    m = _plan(tmp_path)
    shots = m["shots"]
    s2v = sum(s["end_s"] - s["start_s"] for s in shots if s["kind"] == "s2v")
    assert 0.65 <= s2v / DURATION <= 0.75
    assert {s["kind"] for s in shots} == {"s2v", "i2v"}
    assert all(s["reference"]["name"] == "singer" for s in shots if s["kind"] == "s2v")
    inserts = [s["reference"]["name"] for s in sorted(shots, key=lambda s: s["start_s"]) if s["kind"] == "i2v"]
    assert inserts[:4] == concert.INSERTS
    for s in shots:
        if s["kind"] == "i2v":
            n = s["params"]["video_frames"]
            assert n % 4 == 1 and n / 24 >= s["end_s"] - s["start_s"]


def test_assemble_copies_original_audio_and_marks_gaps(tmp_path, capsys):
    _plan(tmp_path)
    capsys.readouterr()
    rc = concert.main(["--project", str(tmp_path), "--audio", str(tmp_path / "missing.mp3"), "assemble", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    mux = [line for line in out.splitlines() if "-map 1:a:0" in line]
    assert len(mux) == 1
    assert "-c:a copy" in mux[0] and "-shortest" not in mux[0]
    cmd = concert.mux_cmd(Path("v.mp4"), Path("a.mp3"), Path("o.mp4"))
    i = cmd.index("-c:a")
    assert cmd[i + 1] == "copy" and "-shortest" not in cmd and "-t" not in cmd
    # nothing generated yet -> every slot is a visible gap slate, never a looped clip
    assert "gaps=" in out and "color=c=0x3a0000" in out
