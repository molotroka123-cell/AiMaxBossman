import importlib.util
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dataset(root, name="a", sha="a" * 64):
    folder = root / name
    folder.mkdir(parents=True)
    image = np.full((100, 120, 3), 255, np.uint8)
    cv2.putText(image, "123", (5, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    cv2.imwrite(str(folder / "frame.png"), image)
    (folder / "manifest.json").write_text(json.dumps({"frame_count": 1, "source_sha256": sha}))
    (folder / "annotations.jsonl").write_text(json.dumps({"frame": "frame.png", "t_ms": 0,
        "sha256": hashlib.sha256((folder / "frame.png").read_bytes()).hexdigest()}) + "\n")
    return folder


def test_ocr_numeric_only_and_no_overwrite(tmp_path):
    module = load("local_recording_ocr")
    folder = dataset(tmp_path)
    calls = []

    def ocr(im):
        calls.append(1)
        box = [[0, 0], [100, 0], [100, 80], [0, 80]]
        return [(box, "123", .99), (box, "private nickname", .99)], None

    report = module.run(tmp_path, ocr=ocr)
    row = json.loads((folder / "numeric-ocr-drafts.jsonl").read_text())
    assert [t["text"] for t in row["numeric_tokens"]] == ["123"]
    assert row["reviewed"] is False and row["usable_for_training"] is False
    assert report["cloud_calls"] == 0
    with pytest.raises(FileExistsError):
        module.run(tmp_path, ocr=ocr)
    assert len(calls) == 1


def test_ocr_empty_or_tampered_input_is_not_completed(tmp_path):
    module = load("local_recording_ocr")
    with pytest.raises(ValueError):
        module.run(tmp_path, ocr=lambda im: ([], None))
    folder = dataset(tmp_path)
    (folder / "frame.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash changed"):
        module.run(tmp_path, ocr=lambda im: ([], None))
    assert not (folder / "numeric-ocr-drafts.jsonl").exists()
    assert not (tmp_path / "LOCAL_OCR_REPORT.json").exists()


def test_candidate_requires_explicit_distinct_recordings(tmp_path):
    module = load("fit_numeric_candidate")
    a = dataset(tmp_path, "a", "a" * 64)
    b = dataset(tmp_path, "b", "b" * 64)
    assert module.select_datasets(tmp_path, "a" * 64, "b" * 64) == (a, b)
    with pytest.raises(ValueError, match="must differ"):
        module.select_datasets(tmp_path, "a" * 64, "a" * 64)
    with pytest.raises(ValueError, match="absent"):
        module.select_datasets(tmp_path, "a" * 64, "c" * 64)


def test_private_output_refused_inside_git(tmp_path):
    (tmp_path / ".git").mkdir()
    for name in ("local_recording_ocr", "fit_numeric_candidate"):
        module = load(name)
        with pytest.raises(ValueError, match="outside Git"):
            if name == "fit_numeric_candidate":
                module.run(tmp_path, train_sha="a", eval_sha="b")
            else:
                module.run(tmp_path, ocr=lambda im: ([], None))


def test_video_recorder_builds_real_offline_segment_and_unreviewed_dataset(tmp_path, monkeypatch):
    module = load("record_live_training")
    status = {"running": True, "error": None, "session": {
        "id": "synthetic", "mode": "window", "act": False, "adapter": "ton_poker"}}
    windows = {"sources": [{"title": "AirPlay Video Stream", "id": "synthetic",
                            "rect": [0, 0, 120, 100]}]}
    monkeypatch.setattr(module, "get", lambda path: status if path.endswith("status") else windows)
    image = np.full((100, 120, 3), 100, np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    sequence = iter(range(100))

    class Response:
        def __init__(self):
            self.headers = {"X-Frame-Seq": str(next(sequence))}

        def read(self):
            return encoded.tobytes()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: Response())
    dest = tmp_path / "recording"
    module.run(dest, seconds=.8)
    report = json.loads((dest / "progress.json").read_text())
    assert report["video_frames"] > 0 and len(report["segments"]) == 1
    assert report["training"] == "NOT_RUN" and report["profile_activation"] is False
    video = cv2.VideoCapture(str(dest / report["segments"][0]["file"]))
    assert video.read()[0]
    video.release()
    annotations = list((dest / "datasets").glob("*/annotations.jsonl"))
    assert len(annotations) == 1
    rows = [json.loads(line) for line in annotations[0].read_text().splitlines()]
    assert rows and all(r["reviewed"] is False and r["truth"] is None for r in rows)
