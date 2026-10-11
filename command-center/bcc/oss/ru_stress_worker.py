"""One-shot accentizer worker. Runs in the separate RUAccent environment.

argv: <model_dir> <output_path>; text arrives on stdin (UTF-8). Offline only:
the model files must be provisioned beforehand (see docs/owner/VOICE_STRESS_20261011.md).
Writes the text with ``+`` before stressed vowels. Never logs the text.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Owner-specific names and words the accentizer is known to get wrong.
CUSTOM_DICT = {
    "боссман": "б+оссман", "боссмана": "б+оссмана", "боссману": "б+оссману",
    "боссманом": "б+оссманом", "боссмане": "б+оссмане",
    "джефф": "дж+ефф", "джеффа": "дж+еффа", "джеффу": "дж+еффу",
    "пчела": "пчел+а", "пчелы": "пчел+ы",
}
_MAX_BYTES = 4000


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    model_dir, output = Path(sys.argv[1]), Path(sys.argv[2])
    if not model_dir.is_dir():
        return 2
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    raw = sys.stdin.buffer.read(_MAX_BYTES + 1)
    if not raw or len(raw) > _MAX_BYTES:
        return 2
    text = raw.decode("utf-8")
    from ruaccent import RUAccent  # type: ignore[import-not-found]

    accentizer = RUAccent()
    accentizer.load(omograph_model_size="turbo3.1", use_dictionary=True,
                    custom_dict=CUSTOM_DICT, device="CPU", workdir=str(model_dir))
    # Sentence by sentence keeps the omograph model's context short and bounded.
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    stressed = " ".join(accentizer.process_all(part) for part in parts if part)
    output.write_text(stressed, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
