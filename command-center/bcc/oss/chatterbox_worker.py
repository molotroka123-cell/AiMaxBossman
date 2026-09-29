"""Private one-shot Chatterbox worker, run by the existing Bossman backend."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) != 6:
        return 2
    try:
        exaggeration, cfg_weight = map(float, sys.argv[4:6])
    except ValueError:
        return 2
    if not 0.37 <= exaggeration <= 0.67 or not 0.46 <= cfg_weight <= 0.55:
        return 2
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    text = sys.stdin.buffer.read(1201).decode("utf-8")
    if not 0 < len(text.strip()) <= 280 or len(text.encode("utf-8")) > 1200:
        return 2

    import torch
    import torchaudio
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    torch.set_num_threads(6)
    torch.set_num_interop_threads(2)
    model = ChatterboxMultilingualTTS.from_local(
        Path(sys.argv[1]), device="cpu", t3_model="v3")
    with torch.inference_mode():
        audio = model.generate(text, language_id="ru", audio_prompt_path=sys.argv[2],
                               exaggeration=exaggeration, cfg_weight=cfg_weight)
    torchaudio.save(sys.argv[3], audio.cpu(), model.sr,
                    encoding="PCM_S", bits_per_sample=16)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
