# Licence texts the Windows bundle copies

`tools/build_windows_bundle.py` runs offline (no network at build time). The licence texts it
has to copy, and cannot read from a package it installs, live here, byte for byte.
`tools/windows_bundle_notices.py` is the only reader.

| file | text | taken from | sha256 |
|---|---|---|---|
| `Apache-2.0.txt` | Apache License 2.0 (canonical, 11358 bytes) | `python_multipart-0.0.32.dist-info/licenses/LICENSE.txt` of the 12.10 bundle | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| `GPL-3.0.txt` | GNU General Public License version 3 | `LICENSE.txt` of the BtbN FFmpeg build `ffmpeg-n8.1-11-g75d37c499d-win64-gpl-8.1.zip` | `8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903` |
| `CC-BY-4.0.txt` | Creative Commons Attribution 4.0 International Public License | `pypdfium2-5.13.0.dist-info/licenses/LICENSES/CC-BY-4.0.txt` of the 12.10 bundle, CRLF line ends converted to LF (the only change) | `419896aea50c15d6e40c5b4baf4bd346f78223b9a354c7357ad00262afdc08ec` |

`.gitattributes` marks this folder `-text` so a checkout never rewrites line ends.

## What the builder does with them

* `Apache-2.0.txt` / `GPL-3.0.txt` are attached as `<dist-info>/licenses/LICENSE` to an installed
  Python distribution that ships no licence file **and** whose own METADATA (and bundled SBOM)
  declares exactly that licence (for example MouseInfo `GPLv3+`, flatbuffers `Apache 2.0`,
  tokenizers `Apache-2.0`). The decision is listed per distribution in
  `LICENSES/python-distributions.md` of the archive.
* `CC-BY-4.0.txt` goes to `LICENSES/codicon/` next to the attribution for the VS Code codicon font.

## What is deliberately not here

A licence text with a per-project copyright line (MIT, BSD) is never composed from a template.
Where the real file is not available offline the archive says `NOT RECORDED`:

* Silero VAD (MIT, `snakers4/silero-vad`): put the upstream `LICENSE` of the model version
  shipped by faster-whisper at `packaging/licenses/silero-vad/LICENSE` and the builder picks it up.
* Python distributions that declare MIT/BSD and ship no file (ctranslate2, latex2mathml,
  PyGetWindow at the 12.10 lock): they are listed as `NOT RECORDED` in
  `LICENSES/python-distributions.md`.

The Chromium licence/credits file and Playwright's own `LICENSE` are copied at build time from the
browser and the Playwright package that are already inside the output tree; optional fallbacks are
`packaging/licenses/chromium/LICENSE.chromium` and `packaging/licenses/playwright/LICENSE`.
