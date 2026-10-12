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

## Upstream files of projects with a per-project copyright line (fetched 12.10.2026, byte for byte)

A licence text with a per-project copyright line (MIT, BSD) is never composed from a template. These four are the
projects' OWN files, downloaded from their repositories and kept unchanged:

| file here | project (licence) | fetched from | bytes | sha256 |
|---|---|---|---|---|
| `silero-vad/LICENSE` | Silero VAD (MIT) | `https://raw.githubusercontent.com/snakers4/silero-vad/master/LICENSE` | 1075 | `2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b` |
| `dist/ctranslate2/LICENSE` | ctranslate2 (MIT) | `https://raw.githubusercontent.com/OpenNMT/CTranslate2/master/LICENSE` | 1115 | `54aa79d9fe3c09e67a16dcd95b9e88676405a6ec174efda31036983cf7672ecb` |
| `dist/latex2mathml/LICENSE` | latex2mathml (MIT) | `https://raw.githubusercontent.com/roniemartinez/latex2mathml/master/LICENSE` | 1071 | `4ac1e1da07f6b343d54191c5e1716840534e08591b3f9eaec90d6800cdc47543` |
| `dist/pygetwindow/LICENSE` | PyGetWindow (BSD-3-Clause) | `https://raw.githubusercontent.com/asweigart/PyGetWindow/master/LICENSE.txt` | 1482 | `f8c2193c7e92df1a8335548a69653fa56b0ea3536586d93932f8c79c206df9fd` |

* `silero-vad/LICENSE` is copied to `LICENSES/silero-vad/` next to its notice.
* `dist/<normalised distribution name>/LICENSE*` is attached as `<dist-info>/licenses/LICENSE` (RECORD updated) to the
  installed Python distribution of that name when it ships no licence file of its own.

## What is deliberately not here

Where the real file is not available the archive says `NOT RECORDED` (nothing is composed):

* any other Python distribution that declares MIT/BSD and ships no file: it is listed as `NOT RECORDED` in
  `LICENSES/python-distributions.md`; to record it, add its upstream `LICENSE` under `dist/<name>/` and rebuild.

The Chromium licence/credits file and Playwright's own `LICENSE` are copied at build time from the
browser and the Playwright package that are already inside the output tree; optional fallbacks are
`packaging/licenses/chromium/LICENSE.chromium` and `packaging/licenses/playwright/LICENSE`.
