"""tools/jeff_voice_samples.py: one sentence per voice in its language, a broken voice is reported and does not hide the others."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import jeff_voice_samples as jv  # noqa: E402


def _model(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"x")
    Path(str(p) + ".json").write_text("{}", encoding="utf-8")
    return str(p)


def test_each_voice_gets_the_sentence_of_its_own_language_and_a_broken_one_is_reported(tmp_path, capsys):
    ru, en = _model(tmp_path, "ru_RU-irina-medium.onnx"), _model(tmp_path, "en_US-lessac-medium.onnx")
    spoken = []

    def synth(text, *, piper_executable, model_path, ffmpeg_executable):
        spoken.append((Path(model_path).name, text))
        if "lessac" in model_path and "FAIL" in text:
            raise RuntimeError("boom")
        return b"OggS" + b"\x00" * 40

    out = tmp_path / "out"
    code = jv.main(["--out", str(out), "--voice", f"ru-irina={ru}", "--voice", f"en-lessac={en}",
                    "--voice", f"en-missing={tmp_path / 'en_US-nope.onnx'}"], synth=synth)
    rows = {r["voice"]: r for r in json.loads((out / "samples.json").read_text(encoding="utf-8"))}
    assert code == 1                                                         # one voice has no model -> PARTIAL, not a silent success
    assert rows["ru-irina"]["status"] == "OK" and rows["en-lessac"]["status"] == "OK" and rows["en-missing"]["status"] == "MISSING_MODEL"
    assert (out / "ru-irina.ogg").read_bytes().startswith(b"OggS")
    assert spoken[0][1].startswith("Привет!") and spoken[1][1].startswith("Hi!")
    assert "VOICE_SAMPLES=PARTIAL" in capsys.readouterr().out


def test_a_voice_option_without_a_path_is_refused():
    for bad in ("nolabel", "=x.onnx", "a/b=x.onnx", "ok="):
        try:
            jv.parse_voice(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
