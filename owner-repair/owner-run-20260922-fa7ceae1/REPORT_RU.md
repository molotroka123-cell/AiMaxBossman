# Bossman 1.0-RC (6) — CI-сертификат и прогон на машине владельца, 2026-09-22

Итог: **сборка сертифицирована в CI и проверена на машине владельца. OWNER_HARDWARE_CERTIFIED не объявляется**:
видео T2V/I2V, отмена/рестарт медиа (шаг 6) и шаги 2–5, 7, 8 из `START_TOMORROW_RU.md` не завершены.

| | |
|---|---|
| FINAL_BRANCH | `claude/bossman-1-0-rc-owner-ready-cfesui` (PR #71 → `release/bossman-owner`) |
| TESTED_SHA | `fa7ceae105c92c3eee718381efdd0333ddcb5e35` (кандидат `rc-2026-09-22-bossman-1.0-rc-owner-ready-6`) |
| CI | `EXACT_SHA_CI=CERTIFIED final=True`, все 10 обязательных workflow PASS (`exact-sha-certificate.json`) |
| Windows ZIP | артефакт `bossman-windows-fa7ceae1…` (run «One-download Windows application»), `BOSSMAN-Windows-x64-fa7ceae105c9.zip` |
| SHA-256 ZIP | `21b8362cabbc98664250ca26fa77144cd122f5f75cf48eaa473517758db7860b` — совпадает с `bundle-acceptance.json` |
| Вне сертификата | `Intelligence Preservation`: FAIL = INSUFFICIENT_EVIDENCE (нет `docs/benchmark/intelligence-preservation-current.json`, нужен замер на реальной модели; не подделан) |

## Что вошло в кандидат 6 (поверх 554ad48a)
DESK-EDGE-RELAUNCH, OpenCode v2 (R12), детерминизм CI (флейк `objective_cas`, из-за которого кандидат 5 не был сертифицирован), гайд критических ошибок, обновлённый SKIPS_REGISTRY.
Не перенесены (механизм уже есть в RC, см. `POST_FREEZE_BACKLOG.md`): MEDIA-RESTART слой 1 (Job Object), R6 stop-epoch. Telegram-компаньон — после 1.0.

## Машина владельца (AMD Ryzen AI Max+ 395 / Radeon 8060S, 120 ГБ, Windows 11)
Распаковано в новую папку `C:\Bossman-Test\fa7ceae1`, отдельный data dir (живые данные не тронуты).

| шаг | результат | улика |
|---|---|---|
| 0 хэш ZIP | PASS | см. выше |
| 1 doctor | WARN: 16 PASS, 5 WARN, 0 BLOCKED (нет облачных ключей, OpenHands, Ollama без моделей, :8800 занят другим backend) | `doctor.json` |
| 1 models | PASS: MAIN :8081 Qwen3.8-27B, FAST :8082 Qwen3.6-35B-A3B отвечают | `models.json` |
| 1 media | PASS: манифест и 6 файлов сверены по sha256 (хэши совпали с записанными при скачивании с закреплённых ревизий HF) | `owner-run-media.md`, `media-MANIFEST.json` |
| 1 diagnostics | PASS: diagnostics.zip, секреты вырезаны | `owner-run-doctor-models-diag.md` |
| 1 Evening-Test --full | OWNER_REQUIRED: приёмка PASS (продукт вернул SHA fa7ceae1), installed_ui_sweep PASS, live_openrouter — нужен ключ | `evening-test.log` |
| 6 изображение | PASS: `sdcpp:z-image-turbo` 1024×1024 seed 7, 171 с; PNG декодируется; `mock=false`; engine_trace: backend Vulkan OBSERVED по логу движка, sha256 бинарника и 3 файлов модели expected==observed, generation→transcode→import с хэшами | `step6-media.json`, `step6-media.log` |
| 6 T2V | НЕ ЗАВЕРШЁН: `sdcpp:wan2.2-ti2v-5b` 832×480, 49 кадров, 20 шагов; через 33 мин progress 0.05, остановлен владельцем для разгрузки ПК. Одновременно были загружены MAIN+FAST (~50 ГБ общей памяти). Эталон handoff — 293 с для варианта A. | `step6-media.log` |
| 6 I2V / отмена / рестарт | NOT_RUN | — |
| 2–5, 7, 8 | NOT_RUN (сценарии владельца) | — |

## Открытые вопросы (не блокеры CI)
* **T2V-скорость на этой машине**: повторить шаг 6 при выгруженных LLM (MAIN/FAST), затем A/B `app-support\media_ab_preset.py` (разрешение / шаги / vae-tiling / diffusion-fa). Пока клип не получен, пресет Wan нельзя объявлять рабочим.
* `provenance.harness.repository_sha = NOT_CAPTURED:development` в установленной сборке — SHA сборки не штампуется в provenance Studio (P3).
* Локальный root-набор на Windows-checkout с `core.autocrlf=true` даёт 22 failed / 23 errors (CRLF-дайджесты, блокировки файлов, cp1251); в CI (Ubuntu) и в Windows-заданиях CI — зелено. Два теста `test_ux2_desktop` падают на этом хосте и на базе 554ad48a одинаково (окружение).

## Три шага запуска для владельца
1. Скачать ZIP с TESTED_SHA, `Get-FileHash … -Algorithm SHA256` = `21b8362c…7860b`, распаковать в новую папку.
2. Запустить llama-server (`start-models.ps1 both`), затем `Owner-Run.cmd` и `Media-Setup.cmd configure --sdcpp-bin … --models-dir …`.
3. `Start-Bossman.cmd` и сценарии `START_TOMORROW_RU.md` 2–8 (для шага 6 — при выгруженных MAIN/FAST).
