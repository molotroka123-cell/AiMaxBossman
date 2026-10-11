import json

import cv2
import numpy as np
import pytest

from pokervision.recording_dataset import export_reviewed, prepare, validate_truth
from pokervision.eval.dataset import load_session


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "source.avi"
    w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 96))
    assert w.isOpened()
    for i in range(20):
        w.write(np.full((96, 64, 3), i * 10, dtype=np.uint8))
    w.release()
    return p


def truth():
    return {"hero_cards": [{"card": "As"}, {"card": "Kd"}], "board": [],
            "street": "preflop", "pot": 75, "hero_stack": 1000}


def pack(video, tmp_path, **kw):
    return prepare(video, tmp_path / "private", game=kw.pop("game", "cash"),
                   split=kw.pop("split", "train"), session_group="session_one", **kw)


def review(directory):
    p = directory / "annotations.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    rows[0].update(reviewed=True, hand_idx=0, truth=truth())
    p.write_text("".join(json.dumps(x) + "\n" for x in rows))


def test_prepare_local_frames_unknown_labels(video, tmp_path):
    result = pack(video, tmp_path)
    assert result["frame_count"] == 2
    assert not result["strategy_training_ready"]
    assert not result["sampling_stopped_at_limit"]
    rows = [json.loads(x) for x in (__import__('pathlib').Path(result['directory']) / 'annotations.jsonl').read_text().splitlines()]
    assert [x["t_ms"] for x in rows] == [0, 1000]
    assert all(x["truth"] is None and not x["reviewed"] for x in rows)
    assert "source.avi" not in json.dumps(result)


def test_limit_honest_and_no_duplicate_overwrite(video, tmp_path):
    assert pack(video, tmp_path, max_frames=1)["sampling_stopped_at_limit"]
    with pytest.raises(ValueError, match="already imported"):
        pack(video, tmp_path)


def test_session_cannot_leak_between_splits(video, tmp_path):
    pack(video, tmp_path)
    # Same recording with different bytes still belongs to the original session.
    with video.open("ab") as f:
        f.write(b"different-container-metadata")
    with pytest.raises(ValueError, match="Session leakage"):
        pack(video, tmp_path, game="tournament", split="test")


def test_no_labels_no_training(video, tmp_path):
    from pathlib import Path
    d = Path(pack(video, tmp_path)["directory"])
    with pytest.raises(ValueError, match="No human-reviewed"):
        export_reviewed(d)
    assert not (d / "labels.jsonl").exists()


def test_review_export_compatible_with_existing_loader(video, tmp_path):
    from pathlib import Path
    d = Path(pack(video, tmp_path)["directory"])
    review(d)
    assert export_reviewed(d)["reviewed_frames"] == 1
    loaded = load_session(d.parent, d.name)
    assert len(loaded) == 1 and loaded[0].truth == truth()
    with pytest.raises(FileExistsError):
        export_reviewed(d)


@pytest.mark.parametrize("change", [
    {"pot": float("nan")}, {"hero_stack": -1}, {"pot": True},
    {"street": "river"}, {"hero_cards": [{"card": "As"}, {"card": "As"}]},
    {"hero_cards": [{"card": "ZZ"}, {"card": "Kd"}]},
])
def test_bad_truth_refused(change):
    with pytest.raises(ValueError):
        validate_truth({**truth(), **change})


def test_frame_tamper_refused(video, tmp_path):
    from pathlib import Path
    d = Path(pack(video, tmp_path)["directory"])
    review(d)
    (d / "frames/000000.png").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="identity mismatch"):
        export_reviewed(d)


def test_private_data_cannot_be_written_inside_git(video, tmp_path):
    (tmp_path / ".git").write_text("gitdir: /somewhere")
    with pytest.raises(ValueError, match="outside Git"):
        pack(video, tmp_path)


@pytest.mark.parametrize("kw", [{"sample_fps": float("inf")}, {"max_frames": 0}, {"game": "invalid"}])
def test_invalid_import_options(video, tmp_path, kw):
    with pytest.raises(ValueError):
        pack(video, tmp_path, **kw)
