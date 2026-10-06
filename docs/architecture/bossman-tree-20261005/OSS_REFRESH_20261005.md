# Обновление старых OSS-решений: что новее и что внедрено (правило ≥ 9/10)

Date: 2026-10-05. Шкала та же, что в `OSS_ADOPTION_SCORECARD_20261005.md`:
G пробел · P скорость · D цена зависимости · S безопасность · V проверено здесь. Каждый пункт 0–2.

## Внедрено

| Изменение | G | P | D | S | V | Σ |
|---|---|---|---|---|---|---|
| `astra_security_gate.py --component windows-bundle`: pip-audit по **поставляемому** hash-lock, а не по окружению Linux-раннера | 2 | 2 | 2 | 2 | 2 | **10** |

Замер: все 118 пакетов `tools/windows_bundle_lock.txt` проверены. Найдена 1 уязвимость, которую текущий
CI-шлюз не видел: **pyjwt 2.14.0 → PYSEC-2026-4141 / GHSA-42vr-xj54-vc7v**, исправлено в 2.15.0.
Пакет тянет `mcp`; Bossman не импортирует jwt. Уязвимый `PyJWKClient` не вызывается ни в Bossman, ни в mcp 1.28 →
пути атаки сейчас нет. Lock пересобирается только на Windows-раннере (`windows_bundle_lock.py record`),
поэтому руками не правился. **Шаг ночной сборки:** перезаписать lock (pyjwt ≥ 2.15.0), затем
`python tools/astra_security_gate.py --component windows-bundle` должен дать PASS. В обязательный CI
режим не подключён, пока lock не пересобран: иначе каждый PR был бы красным из-за того, что PR не может исправить.

## Минимальные версии в pyproject (установка из исходников, не ZIP)

PyPI vulnerabilities для нижних границ: pypdf ≥ 6.0 — 95 записей (исправления до 6.7.x+), Pillow ≥ 10.0 — 34
(до 12.2.0), через fastapi ≥ 0.111 — starlette 0.37 (14, до 1.3.1), h11 0.14 (2). Поставляемый ZIP закреплён
выше (pypdf 6.19.0, pillow 12.3.0, starlette 1.6.0, h11 0.16.0) — для владельца это не дыра.
Поднятие нижних границ — отдельный PR с полным CI (оценка 8: V=1, полный прогон наборов тут не делался).

## Новее, чем в старых документах — нужен замер на машине владельца (V = 0 здесь)

| Было в документах | Новее | Почему не внедрено сейчас | Команда замера |
|---|---|---|---|
| llama-swap (отдельный процесс) | **llama-server router mode**: `--models-dir`, `--models-max 1`, `--models-autoload` — переключение моделей без перезапуска внутри самого llama.cpp | Strix Halo нужен для замера времени переключения и памяти; есть открытый issue про autoload (ggml-org/llama.cpp#18035) | одинаковые 20 запросов с чередованием двух моделей: llama-swap vs router, p50/p95 переключения, пик памяти |
| faster-whisper 1.2.1 | **Parakeet TDT 0.6B v3** (25 языков, есть ru и cs) через `onnx-asr` (MIT, CPU/ONNX) | Hugging Face закрыт в этом окружении; WER для ru не опубликован; лицензия модели — проверить | 30 мин русской + чешской речи с эталоном: WER и real-time factor против faster-whisper int8 |
| docling (PDF, таблицы) | остаётся лучшим по таблицам (≈ 0.88 против ≈ 0.27 у MarkItDown по публичным сравнениям) | замена не нужна; docling-slim 2.127 → 2.133 — обычное обновление в следующей сборке | — |
| browser-use: REFERENCE_ONLY | Skyvern 2.0 и другие; WebVoyager-цифры несопоставимы (разные наборы задач) | собственный `browser_runtime.py` с takeover/fencing сохраняется; новых фактов против решения нет | — |
| qdrant-client 1.15.1 | 1.19.1 | известных уязвимостей у 1.15.1 нет; обновление — в следующей сборке | — |

Источники: northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks;
danilchenko.dev/posts/markitdown-vs-docling-vs-marker; dev.to/rosgluk/llama-server-router-mode-dynamic-model-switching-without-restarts-1h0j;
github.com/ggml-org/llama.cpp/issues/18035; aimultiple.com/open-source-web-agents; PyPI JSON API (versions, vulnerabilities).

North Star: `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT` без изменений.
