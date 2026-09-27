"""English voice-over lines for the promo, synthesized locally with Kokoro TTS (kokoro-onnx).

Needs kokoro-v1.0.onnx + voices-v1.0.bin from the kokoro-onnx GitHub release in MODELS, and
ESPEAK_DATA_PATH pointing at espeakng_loader's espeak-ng-data via a SHORT path (espeak-ng fails
silently on long data paths). Usage: python voice.py MODELS WORK  ->  WORK/voice/segNN.wav + seg.json
"""
import json, os, sys
from pathlib import Path
import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro, EspeakConfig
MODELS, WORK = Path(sys.argv[1]), Path(sys.argv[2]); OUT = WORK / "voice"; OUT.mkdir(parents=True, exist_ok=True)
k = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"),
           espeak_config=EspeakConfig(data_path=os.environ["ESPEAK_DATA_PATH"]))
SEG = ["August twenty-seventh.", "The first commit.", "Thirty-two days.", "Nearly twenty-five hundred commits.",
       "Agents.", "Memory.", "Computer use.", "Video studio.", "Jeff, on Telegram.",
       "Over five thousand tests.", "On your own machine.", "This is Bossman.",
       # 15-22 s: projection chapter
       "Next: Jeff hears you, and talks back.", "One point eight.", "One point nine.",
       "Two point oh.", "Three point oh, by winter.", "To be continued."]
out = []
for i, line in enumerate(SEG):
    a, sr = k.create(line, voice="am_fenrir", speed=1.05, lang="en-us")
    idx = np.where(np.abs(a) > 0.012)[0]; a = a[max(idx[0]-240, 0):idx[-1]+1500]
    sf.write(OUT / f"seg{i:02d}.wav", a, sr)
    out.append({"i": i, "text": line, "dur": round(len(a)/sr, 3), "peak": round(float(np.abs(a).max()), 3)})
json.dump(out, open(OUT / "seg.json", "w"), indent=1); [print(o) for o in out]
