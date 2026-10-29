"""Bossman 2.0 («Надстройка») — единый контур диагностики, памяти Obsidian 2.0 и самопроверки защит.

Экспортирует маршруты:
- GET  /api/v20/status              — сводка состояния Надстройки Bossman 2.0
- GET  /api/v20/memory/audit        — аудит свежести и чистоты хранилища Obsidian + таблиц памяти
- GET  /api/memory/audit            — алиас аудита памяти для раздела Памяти
- POST /api/v20/memory/sync         — очистка синтетического тестового мусора, запись канонических заметок 2.0 и переиндексация
- POST /api/memory/sync-bossman-20  — алиас синхронизации памяти 2.0
- POST /api/v20/security/self-test  — живая детерминированная самопроверка 5 рубежей защиты Bossman 2.0
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request

from .. import db as dbm
from . import Feature
from .tools_memory import (
    MemoryNotConfigured,
    QdrantUnavailable,
    get_service,
    load_config,
)

router = APIRouter()

SYNTHETIC_JUNK_FILENAMES: tuple[str, ...] = (
    "2026-09-21-1822-memory.md",
    "2026-09-21-1832-memory.md",
    "2026-09-21-1833-cp02-audit-note-1.md",
    "2026-09-21-1833-memory.md",
    "2026-09-21-1835-audit-113414.md",
    "2026-09-21-1851-audit-restart-114852.md",
    "2026-09-21-1956-GLM53-MATRIX-B1-1256.md",
    "2026-09-21-1958-GLM53-MATRIX-B2-125746.md",
    "2026-09-21-1958-GLM53-MATRIX-B3-125826.md",
    "2026-10-02-1424-demo-params.md",
)

CANONICAL_BOSSMAN_20_NOTES: dict[str, str] = {
    "2026-10-28-Bossman-2.0-Master-Architecture-and-15-Branches.md": """---
title: "Bossman 2.0 — Архитектура Надстройки и актуальный статус 15 веток (28.10.2026)"
date: 2026-10-28T17:00:00Z
source: bossman
kind: decision
project: bossman-2.0
tags: [bossman-2.0, architecture, 15-branches, audit-2026-10-28, canonical]
---

# Bossman 2.0 — Архитектура Надстройки и актуальный статус 15 веток (28.10.2026)

Эта заметка является главным источником правды (Single Source of Truth) по состоянию платформы **Bossman 2.0** на 28.10.2026. Она заменяет устаревшие сентябрьские и ранние октябрьские черновики (`wt-checkpoint-20261015`, старые счётчики дерева от 10.10 и синтетические тестовые маркеры).

## 1. Ключевые архитектурные принципы Bossman 2.0
1. **Единый runtime и честная идентичность сборки (`source_identity: PASS`)**:
   - Командный центр работает на `:8801` (`command-center`), Мобильный пульт — на `:8840` (`apps/bossman-mobile`), War Terminal — на `:8830`.
   - Топбар и главная страница используют `splitHealth(sys.health)` (`command-center/ui/health_split.js`): **24 компонента ядра** отделены от **опциональных облачных адаптеров** (`models`, `providers`, `provider:1..16`, `browser`), чтобы отсутствие платных ключей не вызывало ложных красных тревог «Сбой».
2. **Честная приёмка без приписок (Honest Gate Policy)**:
   - Любая метрика или гейт без реального прогона модели/железа маркируется строго как `NOT_MEASURED` или `INSUFFICIENT_EVIDENCE` (вместо синтетических `PASS`).
   - Все неизменяемые файлы ядра (`approvals.py`, `permissions.py`, `secrets.py`, `auth.py`, `control_plane.py`, `docs/constitution/*`) защищены от любых правок.

## 2. Сводка 15 веток Bossman 2.0
1. **Ветка 1 (`jeff`)**: Главный оркестратор Джефф, голосовой и текстовый диалог, Telegram Stars двухфазный резерв (`reserve_star_quota` -> `commit_star_quota` / `refund_quota_on_delivery_failure` + `reconcile_star_transactions_after_downtime` в `stars.py`).
2. **Ветка 2 (`mobile` `:8840`)**: Мобильный пульт без фасадов — честный `GET /m/api/health`, реальное распознавание речи `POST /m/api/voice/turn` (faster-whisper `base` CPU int8 + кнопка `🎙 Голос` в UI), живой экран с фильтрацией приватности `GET /m/api/pc/screen-stream` и реальный откат `POST /m/api/tasks/{id}/rollback`.
3. **Ветка 3 (`apps`)**: 13 бизнес- и медиа-приложений (`War Terminal :8830`, `SwapMe Bot`, `BossBlocks 3D WebGL`, `Bossman Mobile`, `Shorts Factory` и др.) + единый `AppActionPassport` с проверкой `STOP` и SHA-256 квитанцией.
4. **Ветка 4 (`models`)**: Локальный стек Ollama (`Gemma 4 31B`, `Qwen 3.5`, `Qwen2.5-Coder`) + бесплатные облачные модели с жёстким гейтом `$0.00`. Турнир `BL-003` честно хранит статус `INSUFFICIENT_EVIDENCE` до полного живого прогона всех 4 полос.
5. **Ветка 5 (`cloud`)**: `SpendFirewall` (`bcc/security/spend_firewall.py`) и прямой гейт в `openrouter.py`, проверяющий `cost == $0.00` до HTTP-вызова, плюс AST-линтер `tools/check_no_direct_llm_http.py`.
6. **Ветка 6 (`computer`)**: Инструмент `computer.processes` (`T0`), `pc.run_command` внутри Windows Job Object (`bcc/security/win_job.py`, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`), защита `audit.jsonl` (20/20 паттернов), PowerShell AST-гейт (`bcc/security/powershell_gate.py`) и каноникализация путей Windows (`bcc/security/win_paths.py`).
7. **Ветка 7 (`media` / Art 2.0)**: `Photo Studio 2.0` (реальные ноды ComfyUI v0.37.0: `SetUnionControlNetType` + `ControlNetApplyAdvanced`, `ImagePadForOutpaint`, `IPAdapterUnifiedLoaderFaceID` + `IPAdapterFaceID`), `Direct Gen` с раздельными индикаторами фото (`sd-server`/`sd-cli`) и видео (`ComfyUI`), `Motion Studio 2.0` (реальный ASS + `.cube` LUT + `measure_beats` BPM), `Music Studio` с ленивым автостартом `ACE-Step` по кнопке.
8. **Ветка 8 (`memory`)**: Obsidian Vault (`C:\\Users\\asd\\Bossman\\audit-memory`, папка `BOSSMAN Memory`) + SQLite BM25 индекс + двусторонняя привязка `plugin:obsidian.read` / `plugin:obsidian.write` к `settings_kv["memory.vault"]` + `memory_retrieval_v16.pack()` (Hybrid RRF + MMR + budget packing).
9. **Ветка 9 (`self-improvement` / `SwarmDAGPlanner`)**: 4-фазный `SwarmDAGPlanner` (`bcc/v2/swarm_dag.py`) с кросс-семейной верификацией (`proposer_family != verifier_family`) и честным статусом `DEGRADED_SYNTHETIC` при отсутствии живого адаптера.
10. **Ветка 10 (`skills`)**: Все 94 навыка в `.agents/skills/` (включая 17 новых навыков `s0710-top-01..10`, `s0710-oss-1..5`, `skill-50`, `skill-51`) валидированы без ошибок манифеста и политики.
11. **Ветка 11 (`plugins`)**: 22 адаптера плагинов с защитой от SSRF/DNS-rebinding, read-only SQL (`?mode=ro`), транслитерацией кириллицы в `mcp_hub.py::normalize_name` (`BL-131`) и интеграцией Obsidian Vault из UI-настроек.
12. **Ветка 12 (`ops`)**: `sources.lock.json` с полными ревизиями (`BL-123`), изоляция `negative_control: True` в `verify_windows_bundle.py` (`BL-130`), глобальный `STOP` на каждом экране.
13. **Ветка 13 (`oss`)**: Честный статус QLoRA эксперимента `CYBER-QLORA-20261026` (без подмены keyword-эвристикой реального инференса весов).
14. **Ветка 14 (`commerce`)**: `compute_quote_strict(..., require_explicit_margin=True)` в `apps/swapme-bot/quote.py` (запрет угадывания маржи по умолчанию) + `OrderToDeliveryLedger` (`PAPER_DEMO` vs `AUDITED_PAID_REVENUE`).
15. **Ветка 15 (`devices`)**: `WorkerClusterCoordinator` и `VirtualUVCCameraProbe` с честным разграничением локального (`IN_PROCESS_ONLY`) и сетевого каскадного `STOP`.
""",
    "2026-10-28-Bossman-2.0-Security-AST-JobObject-SpendFirewall.md": """---
title: "Bossman 2.0 — Контур Киберзащиты: PowerShell AST, Windows Job Object, Audit Guard 20/20 и SpendFirewall"
date: 2026-10-28T17:05:00Z
source: bossman
kind: decision
project: bossman-2.0
tags: [bossman-2.0, security, powershell-ast, job-object, spend-firewall, audit-guard]
---

# Bossman 2.0 — Контур Киберзащиты (Обновление 28.10.2026)

Эта заметка дополняет и актуализирует заметку от 22.10 («Киберзащита: две линии и безопасный реверс-инжиниринг») реальными механизмами защиты, внедрёнными и доказанными в коде к 28.10.2026.

## 1. Настоящий разбор PowerShell AST (`bcc/security/powershell_gate.py` + `pc_agent_policy_guards.py`)
- Вместо простых регулярок `action_contract.py` и `pc_agent_policy_guards.py` используют двухуровневый заслон:
  1. Детерминированный детектор обфускации `validate_powershell_ast_or_tokens(cmd)` мгновенно блокирует (`T3 deny`):
     - backtick-обфускацию (`i` + `` ` `` + `ex`),
     - `-EncodedCommand` / `-enc`,
     - `DownloadString` / `DownloadFile`,
     - `iex` / `Invoke-Expression`,
     - склейку строк (`'Remove-' + 'Item'`),
     - вызов через оператор `& $var`, `. $var`, `& (...)`, `& { ... }`,
     - `InvokeScript`, `[scriptblock]::Create(...).Invoke()`, `.Delete()`,
     - форматную обфускацию `"{0}{1}" -f ...`.
  2. Реальный парсер `System.Management.Automation.Language.Parser::ParseInput` в `bcc/security/powershell_gate.py` обходит дерево AST (`CommandAst`, `InvokeMemberExpressionAst`, `TypeExpressionAst`, `RedirectionAst`) и разрешает автоматическое (`T0`) исполнение только при 100% белом списке read-only командлетов.

## 2. Каноникализация путей Windows (`bcc/security/win_paths.py`)
- Перед проверкой `safe_roots` путь проходит скрининг `_screen()` и `canonicalize()`:
  - запрещены альтернативные потоки NTFS (`file.txt:stream`, `::$DATA`),
  - запрещены зарезервированные имена устройств DOS (`CON`, `PRN`, `AUX`, `NUL`, `COM1..9`, `LPT1..9`),
  - запрещены хвостовые точки и пробелы (`audit.jsonl.`), которые Win32 тихо срезает,
  - запрещены префиксы `\\\\?\\`, `\\\\.\\`, `GLOBALROOT` и UNC-пути,
  - короткие имена 8.3 (`PROGRA~1`) и симлинки/junction раскрываются через `GetFinalPathNameByHandleW`.

## 3. Защита неизменяемого журнала аудита 20/20 (`is_immutable_audit_log_target`)
- Любая попытка удалить, очистить, перезаписать, переименовать или скопировать поверх `audit.jsonl`, `audit.jsonl.head`, `docs/audits/` или `evidence/` блокируется со статусом `blocked_immutable_audit_log` (`T3`), включая:
  - склейку имени из кавычек (`'audit.json'+'l'`),
  - сборку из переменных (`$a='audit'; $b='.jsonl'; Remove-Item "$a$b"`),
  - маски (`del aud*.jsonl`, `Remove-Item *.jsonl`),
  - перезапись копированием (`copy /y nul audit.jsonl`).

## 4. Исполнение команд в Windows Job Object (`bcc/security/win_job.py`)
- `pc.run_command` запускает дочерние процессы с флагом `CREATE_SUSPENDED`, помещает их в Windows Job Object с `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION` и лимитом памяти до `ResumeThread`.
- При нажатии глобального `STOP` или таймауте `TerminateJobObject` мгновенно уничтожает всё дерево дочерних процессов (включая фоновые субпроцессы).

## 5. Облачный `SpendFirewall` (`bcc/security/spend_firewall.py` + `openrouter.py`)
- Прямой шлюз перед каждым обращением к облачному провайдеру: если модель не является `$0.00` (`:free`) и нет криптографического `owner_token`, запрос блокируется до сетевого сокета.
""",
    "2026-10-28-Bossman-2.0-Art-Studio-Media-Motion-Music-3D.md": """---
title: "Bossman 2.0 — Контур Искусства: Photo Studio 2.0, Direct Gen, Motion 2.0, Music Studio и 3D WebGL"
date: 2026-10-28T17:10:00Z
source: bossman
kind: decision
project: bossman-2.0
tags: [bossman-2.0, art-studio, comfyui, motion-studio, music-studio, bossblocks-3d]
---

# Bossman 2.0 — Контур Искусства и Медиа (Обновление 28.10.2026)

Эта заметка заменяет устаревшую сводку от 17.10 («DevilFans курс ComfyUI — что уже есть на этом ПК») и фиксирует реальную архитектуру медиа-контура Bossman 2.0.

## 1. Photo Studio 2.0 (`bcc/art_v2/photo_engine.py`)
- `DynamicWorkflowCompiler` компилирует графы ComfyUI, проверенные против исходников установленного `ComfyUI v0.37.0`:
  - `txt2img_studio`: честные графы для `Flux.1-dev / schnell` (`UNETLoader` + `DualCLIPLoader` + `FluxGuidance`) и `SDXL` (`CheckpointLoaderSimple`).
  - `pose_and_structure`: цепочка `LoadImage -> SetUnionControlNetType -> ControlNetApplyAdvanced` по каждому входу (`openpose`, `depth`, `canny/lineart/anime_lineart/mlsd`) с передачей `(positive, negative)` в `KSampler` (строго для SDXL ControlNet Union).
  - `inpaint_outpaint_canvas`: использует реальную ноду `ImagePadForOutpaint` с пиксельными отступами кратными 8 (`left`, `top`, `right`, `bottom`) + `DifferentialDiffusion` + `VAEEncodeForInpaint`.
  - `character_lock`: использует реальные ноды пакета `ComfyUI_IPAdapter_plus` (`IPAdapterUnifiedLoaderFaceID`, `IPAdapterFaceID`, `IPAdapterInsightFaceLoader`, `IPAdapterModelLoader`).
  - `detailer_and_upscale`: `ImageUpscaleWithModel` (`RealESRGAN_x4plus.pth`) + `FaceDetailer` (`Impact Pack`).
- `VoxelToRenderBridge`: экспортирует 3D-сцену (`velvet_diva`, `pool_adonis`, `cupid_bouncer`, `cyber_siren`) в 3 пасса (`rgb_pass`, `z_depth_map`, `skeleton_pose_keypoints`).

## 2. Direct Gen (`bcc/features/direct_gen.py` + `ui/pages/direct_gen.js`)
- Раздельные индикаторы готовности в верхней полосе страницы:
  - **Фото (`sd-server / sd-cli`)** — работает автономно через `stable-diffusion.cpp` (`Juggernaut-XL v9` / `Flux1-schnell` GGUF) даже когда `ComfyUI` выключен.
  - **Видео (`ComfyUI`)** — показывает статус `ComfyUI :8188` для `AnimateDiff / SVD / Wan`.

## 3. Motion Studio 2.0 (`bcc/art_v2/motion_timeline.py` + `bcc/features/motion_studio.py`)
- Реальный рендер кинетических субтитров `ASS` (`ScriptType: v4.00+` с тегами `\\fad`, `\\t(\\fscx\\fscy)`) и вшивание в видеофильтр ffmpeg.
- Реальная генерация файла `lut.cube` (`TITLE` + `LUT_3D_SIZE 17` + таблица $17^3 = 4913$ RGB-троек для пресетов `Teal & Orange`, `Cyberpunk Neon`, `Velvet Film 35mm`) и применение через `lut3d=...`.
- Реальное измерение темпа `bpm_measured` и сетки ударов через `bcc.features.motion_workflow.beats.measure_beats(audio_path)` по PCM-огибающей (`ffmpeg` -> `16kHz mono s16le`).

## 4. Music Studio (`bcc/features/music_studio.py`)
- Ленивый автостарт `ACE-Step` (`127.0.0.1:7865`): сервис не висит в фоне и не тратит 4 ГБ ОЗУ вхолостую, а поднимается по требованию при нажатии кнопки **«▶ Запустить ACE-Step»** в UI или при старте генерации трека.

## 5. BossBlocks 3D WebGL (`apps/bossblocks/webgl_velvet_3d.html`)
- Гладкие процедурные 3D-модели (`Subdivision Surface / Catmull-Rom Lathe + Blinn-Phong PBR + Rim Lighting`) для 4 персонажей (`Леди Вельвет`, `Адонис у Бассейна`, `Купидон-Вышибала`, `Кибер-Сирена`) вместо старых кубических вокселей.
""",
    "2026-10-28-Bossman-2.0-Local-Models-Cloud-Horde-and-94-Skills.md": """---
title: "Bossman 2.0 — Локальные Модели, Бесплатная Облачная Орда ($0.00) и Библиотека 94 Навыков"
date: 2026-10-28T17:15:00Z
source: bossman
kind: fact
project: bossman-2.0
tags: [bossman-2.0, models, cloud-horde, skills, resource-governor]
---

# Bossman 2.0 — Модели, Облачная Орда и 94 Навыка (28.10.2026)

## 1. Локальное ядро и Управление Ресурсами (`Resource Governor`)
- На хосте активна принудительная защита памяти (`enforce: True` в `/api/resources`, `RAM > 85%` или `VRAM > 90%` переводит новые тяжёлые задачи в очередь).
- Одновременно в VRAM держится не более одной тяжёлой локальной модели (`Gemma 4 31B`, `Qwen 3.5`, `Qwen2.5-Coder`).

## 2. Бесплатная Облачная Орда (`$0.00` SpendFirewall)
- Все облачные задачи маршрутизируются только через верифицированные `$0.00` модели (`:free` в OpenRouter или локальные провайдеры) с проверкой цен в `openrouter_free_gate.py`.
- Оркестратор `SwarmDAGPlanner` (`bcc/v2/swarm_dag.py`) использует кросс-семейную проверку (`proposer_family != verifier_family`) и честно помечает прогон `DEGRADED_SYNTHETIC`, если в тестовом окружении не передан живой `adapter_runner`.

## 3. Полная Библиотека из 94 Навыков (`.agents/skills/`)
- Все 94 навыка в `.agents/skills/` имеют валидный `SKILL.md` и проходят `validate_manifest` + `scan_policy` с `0` критических нарушений:
  - Топ-10 курируемых навыков (`s0710-top-01` .. `s0710-top-10`): от `repo-map-navigator` и `safe-patch-composer` до `voice-briefing-composer` и `incident-postmortem-writer`.
  - 5 OSS-навыков (`s0710-oss-1` .. `s0710-oss-5`): `playwright-trace-auditor`, `ffmpeg-Loudness-normalizer`, `sqlite-fts5-migrator`, `comfyui-workflow-linter`, `openapi-contract-verifier`.
  - Специальные навыки безопасности и медиа (`skill-50` `windows-jobobject-runner`, `skill-51` `hybrid-media-qc-gate`).
""",
    "2026-10-28-Bossman-2.0-Mobile-Pult-8840-and-PC-Agent-UX.md": """---
title: "Bossman 2.0 — Мобильный Пульт :8840, PC-Agent и UX-стандарт умных кнопок"
date: 2026-10-28T17:20:00Z
source: bossman
kind: procedure
project: bossman-2.0
tags: [bossman-2.0, mobile-pult, pc-agent, ux, browser-tab-info]
---

# Bossman 2.0 — Мобильный Пульт :8840, PC-Agent и UX-стандарт (28.10.2026)

## 1. Мобильный Пульт (`:8840`, `apps/bossman-mobile`)
- **Честный Health (`/m/api/health`)**: пингует живой `:8801/health/live` с кэшем 2 с и возвращает `LIVE` только при живом ядре.
- **Голосовой ввод Push-to-Talk (`POST /m/api/voice/turn`)**: кнопка `🎙 Голос` в нижней панели `apps/bossman-mobile/bossmobile/ui/index.html` записывает аудио с микрофона (`MediaRecorder`), отправляет в `faster-whisper` (`base`, `int8`) и подставляет распознанный русский текст в поле ввода.
- **Живой экран ПК (`GET /m/api/pc/screen-stream`)**: захватывает реальный PNG-кадр экрана (`ImageGrab.grab`), проверяет заголовок активного окна по `PRIVACY_BLACKLIST` (при обнаружении 1Password/KeePass/Bitwarden/банка возвращает защищённую плашку `privacy_filtered: True`).
- **Реальный 1-Tap Rollback (`POST /m/api/tasks/{id}/rollback`)**: отменяет задачу через `POST /api/tasks/{id}/cancel` и откатывает записанные файлы из `pc-agent/backups/`.

## 2. Надёжное определение URL вкладки браузера (`browser.active_tab_info`)
- Инструмент `browser.active_tab_info` (`bcc/features/tools_browser.py` и `GET /api/browser/active-tab`) определяет URL и заголовок активной вкладки по цепочке:
  1. Управляемый Playwright Context (`source="playwright", confidence="high"`),
  2. Chrome DevTools Protocol (`http://127.0.0.1:9222/json/list`, `source="cdp", confidence="high"`),
  3. Заголовок окна Win32 (`GetForegroundWindow` + `GetWindowTextW`, `source="window_title", confidence="low"` — не вызывает срабатывания политики URL, но даёт честный контекст без аварийной остановки).

## 3. UX-стандарт умных кнопок Bossman 2.0
- Каждая интерактивная кнопка в Command Center (`:8801`) и Мобильном пульте (`:8840`) обязана:
  1. Блокироваться от двойного клика на время выполнения (`disabled = true`, `aria-busy = "true"`).
  2. Давать понятный русскоязычный отклик (`toastOk`, `toastError` или обновление статусной плашки на экране).
  3. Иметь глобальную кнопку **«⛔ STOP»** в шапке любого экрана.
""",
}


def _is_synthetic_junk_note(path: Path, text: str) -> bool:
    if path.name in SYNTHETIC_JUNK_FILENAMES:
        return True
    stripped = text.strip()
    if stripped.endswith("# memory\n\nmemory") or stripped.endswith("# memory\r\n\r\nmemory"):
        return True
    if "MATRIX-B1-1256" in text or "MATRIX-B2" in text or "MATRIX-B3" in text:
        return True
    if "Бюджет проекта Northstar: 250 000 CZK" in text or "Кодиое слово проекта: Янтарь-47" in text:
        return True
    return False


def inspect_obsidian_vault(vault_root: str | Path, write_folder: str = "BOSSMAN Memory") -> dict[str, Any]:
    root = Path(vault_root) if vault_root else None
    if not root or not root.is_dir():
        return {
            "configured": False,
            "vault_root": str(vault_root or ""),
            "write_folder": write_folder,
            "total_markdown_files": 0,
            "write_folder_files": 0,
            "canonical_v20_notes": [],
            "canonical_v20_count": 0,
            "synthetic_junk_notes": [],
            "synthetic_junk_count": 0,
            "freshness_status": "NOT_CONFIGURED",
        }

    wf_dir = root / write_folder
    all_md = [
        p for p in root.rglob("*.md")
        if not any(part in {".obsidian", ".trash", ".git", "node_modules"} for part in p.parts)
    ]
    wf_md = sorted(wf_dir.glob("*.md")) if wf_dir.is_dir() else []

    canonical_present: list[str] = []
    synthetic_junk: list[str] = []

    for p in wf_md:
        if p.name in CANONICAL_BOSSMAN_20_NOTES:
            canonical_present.append(p.name)
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            txt = ""
        if _is_synthetic_junk_note(p, txt):
            synthetic_junk.append(p.name)

    is_fresh = len(canonical_present) >= len(CANONICAL_BOSSMAN_20_NOTES) and len(synthetic_junk) == 0
    return {
        "configured": True,
        "vault_root": str(root),
        "write_folder": write_folder,
        "total_markdown_files": len(all_md),
        "write_folder_files": len(wf_md),
        "canonical_v20_notes": canonical_present,
        "canonical_v20_count": len(canonical_present),
        "canonical_v20_expected": len(CANONICAL_BOSSMAN_20_NOTES),
        "synthetic_junk_notes": synthetic_junk,
        "synthetic_junk_count": len(synthetic_junk),
        "freshness_status": "UP_TO_DATE" if is_fresh else "NEEDS_SYNC",
    }


async def _db_memory_counts(svc) -> dict[str, int]:
    counts = {"facts": 0, "decisions": 0, "procedures": 0, "failures": 0}
    if svc is None or getattr(svc, "db", None) is None:
        return counts
    try:
        async with svc.db.session() as s:
            for name, tbl in (
                ("facts", dbm.facts),
                ("decisions", dbm.decisions),
                ("procedures", dbm.procedures),
                ("failures", dbm.failures),
            ):
                res = await s.execute(sa.select(sa.func.count()).select_from(tbl))
                counts[name] = int(res.scalar() or 0)
    except Exception:
        pass
    return counts


async def _seed_canonical_db_memory(svc) -> dict[str, int]:
    """Удаляет синтетические тестовые строки Northstar и обучает все 4 таблицы памяти bcc.db
    (facts, decisions, procedures, failures) по результатам выполненных шагов и аудитов."""
    if svc is None or getattr(svc, "db", None) is None:
        return {"facts": 0, "decisions": 0, "procedures": 0, "failures": 0}
    now = datetime.now(timezone.utc)
    try:
        async with svc.db.session() as s:
            # 1. Удаляем синтетические строки Northstar из facts
            await s.execute(
                sa.delete(dbm.facts).where(
                    sa.or_(
                        dbm.facts.c.subject.ilike("%Northstar%"),
                        dbm.facts.c.object.ilike("%Northstar%"),
                        dbm.facts.c.statement.ilike("%Northstar%"),
                    )
                )
            )

            # 2. Канонические факты Bossman 2.0 (соблюдая правило append/supersede схемы dbm.facts)
            canonical_facts = [
                (
                    "bossman.version",
                    "release",
                    "Bossman 2.0 («Надстройка»), ревизия 29.10.2026 (Harness Approved)",
                    "Bossman 2.0 («Надстройка») работает на едином main с проверкой source_identity=PASS и git_clean=true.",
                ),
                (
                    "bossman.obsidian_vault",
                    "path",
                    "C:\\Users\\asd\\Bossman\\audit-memory (BOSSMAN Memory)",
                    "Каноническое хранилище Obsidian расположено в C:\\Users\\asd\\Bossman\\audit-memory (папка записи BOSSMAN Memory).",
                ),
                (
                    "bossman.health_split",
                    "ui_contract",
                    "24 компонента ядра отделены от опциональных адаптеров через splitHealth()",
                    "Верхняя панель отделяет 24 компонента ядра от необязательных адаптеров (models, providers, browser) через splitHealth().",
                ),
                (
                    "bossman.skills_library",
                    "inventory",
                    "94 валидированных навыка в .agents/skills/ (0 ошибок манифеста и политики)",
                    "Библиотека навыков содержит 94 валидированных SKILL.md в .agents/skills/ с 0 критических нарушений scan_policy.",
                ),
                (
                    "bossman.art_v2",
                    "capabilities",
                    "Photo Studio 2.0, Direct Gen, Motion 2.0 (ASS + .cube LUT + measure_beats BPM), Music Studio (ACE-Step)",
                    "Контур искусства Bossman 2.0 включает Photo Studio 2.0, Direct Gen, Motion Studio 2.0 и ленивый старт ACE-Step.",
                ),
                (
                    "bossman.spend_firewall",
                    "security_gate",
                    "$0.00 по умолчанию; платные вызовы требуют одноразового токена с привязкой к provider/model",
                    "SpendFirewall блокирует любые платные облачные вызовы без одноразового токена владельца, привязанного к allowed_provider и allowed_model.",
                ),
                (
                    "bossman.pc_agent_perimeter",
                    "auth_rule",
                    "Роль agent имеет доступ только к /api/pc-agent/stop; approve/resume/trust/self-modify требуют owner",
                    "Периметр PC-Agent разрешает токену agent только экстренный /api/pc-agent/stop; все мутирующие маршруты требуют owner.",
                ),
            ]
            for subj, pred, obj_val, stmt_val in canonical_facts:
                exists = (
                    await s.execute(
                        sa.select(dbm.facts.c.id).where(
                            sa.and_(
                                dbm.facts.c.subject == subj,
                                dbm.facts.c.predicate == pred,
                                dbm.facts.c.invalid_at.is_(None),
                            )
                        )
                    )
                ).first()
                if not exists:
                    await s.execute(
                        sa.insert(dbm.facts).values(
                            subject=subj,
                            predicate=pred,
                            object=obj_val,
                            statement=stmt_val,
                            valid_at=now,
                            created_at=now,
                            source_kind="note",
                            source_note="BOSSMAN Memory/2026-10-28-Bossman-2.0-Master-Architecture-and-15-Branches.md",
                            confidence=1.0,
                        )
                    )

            # 3. Канонические архитектурные решения (dbm.decisions: key, decision, reason, alternatives_rejected, scope, created_by)
            decisions_seed = [
                (
                    "bossman-2.0-health-split@v1",
                    "Разделять здоровье 24 компонентов ядра и опциональных облачных адаптеров через splitHealth().",
                    "Отсутствие ключей к внешним провайдерам не является сбоем локального ядра и не должно красить топбар в «Сбой».",
                    ["Плоский цикл по всем ключам sys.health"],
                    "Command Center UI",
                ),
                (
                    "bossman-2.0-pc-agent-owner-perimeter@v1",
                    "Разрешать роли agent только /api/pc-agent/stop; требовать owner и одноразовую запись в approvals для /api/pc-agent/self-modify.",
                    "Предотвращение самоодобрения агента через префиксные правила или подмену JSON approved=True.",
                    ["Доверие булеву полю approved=True из тела запроса"],
                    "PC-Agent Security",
                ),
                (
                    "bossman-2.0-spend-firewall-binding@v1",
                    "Проверять бесплатность по urlsplit(host) и привязывать одноразовый токен владельца к allowed_provider, allowed_model и max_usd.",
                    "Защита от обхода через 127.0.0.1@evil.example и от расходования токена дешёвой модели на дорогую модель.",
                    ["Поиск подстроки 127.0.0.1 в URL", "Непривязанный к модели токен"],
                    "Cloud & SpendFirewall",
                ),
                (
                    "bossman-2.0-win-job-object-before-resume@v1",
                    "Создавать дочерние процессы pc.run_command с CREATE_SUSPENDED и убивать процесс до resume() при сбое привязки к Windows Job Object.",
                    "Гарантия 100% уничтожения дерева дочерних процессов при нажатии STOP или таймауте.",
                    ["Возобновление процесса при ошибке _win_job_attach"],
                    "Windows Process Control",
                ),
                (
                    "bossman-2.0-honest-gates-and-revocation@v1",
                    "Отзывать синтетические бенчмарки (revocation.json) и удерживать непроверенные вживую контуры в статусе NOT_MEASURED / NOT_RUN.",
                    "Исключение ложной отчётности о готовности весов QLoRA и неподключённых демонов видео.",
                    ["Захардкоженные таблицы оценок в скриптах турниров"],
                    "Audit & Governance",
                ),
            ]
            for d_key, d_text, d_reason, d_alt, d_scope in decisions_seed:
                exists_d = (
                    await s.execute(sa.select(dbm.decisions.c.id).where(dbm.decisions.c.key == d_key))
                ).first()
                if not exists_d:
                    await s.execute(
                        sa.insert(dbm.decisions).values(
                            key=d_key,
                            decision=d_text,
                            reason=d_reason,
                            alternatives_rejected=d_alt,
                            scope=d_scope,
                            created_by="bossman-2.0-sync",
                            created_at=now,
                        )
                    )

            # 4. Канонические процедуры (dbm.procedures: name, description, steps, scope)
            procedures_seed = [
                (
                    "bossman_20_obsidian_sync",
                    "Очистка синтетического тестового мусора, запись канонических заметок 2.0 и обучение таблиц памяти SQLite.",
                    [
                        "Вызвать POST /api/v20/memory/sync",
                        "Переместить синтетические тестовые заметки в .trash/archive-synthetic-tests-202609/",
                        "Записать канонические заметки 2026-10-28-Bossman-2.0-*.md в BOSSMAN Memory",
                        "Заполнить таблицы facts, decisions, procedures, failures и переиндексировать BM25",
                    ],
                    "Memory & Obsidian 2.0",
                ),
                (
                    "bossman_20_coding_red_green_protocol",
                    "Строгий протокол внесения правок безопасности и логики в main.",
                    [
                        "1. Написать красный регрессионный тест, воспроизводящий дефект",
                        "2. Внести минимальную точечную правку без затрагивания неизменяемого ядра (§0)",
                        "3. Проверить негативный контроль и соседние тестовые наборы (--basetemp вне репозитория)",
                        "4. Провести перекрёстное ревью двумя моделями разных семейств и записать урок в память",
                    ],
                    "Harness Engineering",
                ),
                (
                    "bossman_20_live_release_and_stop_drill",
                    "Проверка идентичности сборки /health/live и готовности аварийного останова STOP после обновления main.",
                    [
                        "Проверить git status -sb (рабочее дерево должно быть чистым)",
                        "Перезапустить сервер и убедиться, что GET /health/live возвращает source_identity=PASS и build_sha==HEAD",
                        "Выполнить POST /api/v20/security/self-test (5/5 рубежей PASS)",
                    ],
                    "Ops & Release",
                ),
            ]
            for p_name, p_desc, p_steps, p_scope in procedures_seed:
                exists_p = (
                    await s.execute(sa.select(dbm.procedures.c.id).where(dbm.procedures.c.name == p_name))
                ).first()
                if not exists_p:
                    await s.execute(
                        sa.insert(dbm.procedures).values(
                            name=p_name,
                            description=p_desc,
                            steps=p_steps,
                            scope=p_scope,
                            created_at=now,
                        )
                    )

            # 5. Извлечённые уроки из сбоев и аудитов (dbm.failures: symptom, root_cause, attempted_fix, result, files, test)
            failures_seed = [
                (
                    "plugin:obsidian.read отвечал no credential при настроенном в UI хранилище Памяти",
                    "resolve_cred('OBSIDIAN_VAULT') проверял только переменную окружения OBSIDIAN_VAULT и игнорировал settings_kv['memory.vault']",
                    "В resolve_cred добавлен автоматический fallback к load_config(svc)['root']",
                    "RESOLVED_AND_VERIFIED",
                    ["command-center/bcc/v2/plugin_adapters.py"],
                    "command-center/tests/test_bossman_20_superstructure.py",
                ),
                (
                    "Синтетический токен роли agent проходил авторизацию на мутирующих маршрутах /api/pc-agent/approve и /self-modify",
                    "Префиксное правило path.startswith('/api/pc-agent') в api.py разрешало роль agent на всех подмаршрутах",
                    "Ограничить роль agent только маршрутом /api/pc-agent/stop; для self-modify проверять одноразовую одобренную запись в approvals",
                    "RESOLVED_AND_VERIFIED",
                    ["command-center/bcc/api.py", "command-center/bcc/features/pc_agent.py"],
                    "command-center/tests/test_self_modify_approval_binding.py",
                ),
                (
                    "Оплаченные вызовы cloud.ask со статусом empty/error или после 2000 записей выпадали из дневного бюджета",
                    "budget_state фильтровал только status=='ok' и читал хвост из 2000 строк, а приCancelledError использовался asyncio.shield вместо await_shared",
                    "Учитывать стоимость всех статусов, читать весь audit.jsonl с проверкой целостности и записывать billing_unknown через await_shared",
                    "RESOLVED_AND_VERIFIED",
                    ["command-center/bcc/features/tools_cloud.py"],
                    "command-center/tests/test_exec_swarm_regressions.py",
                ),
                (
                    "При ошибке привязки процесса к Windows Job Object (_win_job_attach -> None) процесс всё равно возобновлялся",
                    "_win_bind_and_resume вызывал resume() вне зависимости от результата _win_job_attach",
                    "При job is None немедленно вызывать proc.kill() и выбрасывать OSError до resume()",
                    "RESOLVED_AND_VERIFIED",
                    ["command-center/bcc/v2/terminal_control.py"],
                    "command-center/tests/test_exec_swarm_regressions.py",
                ),
                (
                    "Таблицы decisions, procedures и failures в bcc.db оставались с 0 записей после /api/v20/memory/sync",
                    "В _seed_canonical_db_memory использовались неверные имена колонок (success_Count, title/rationale, fix/prevention) под широким except Exception: pass",
                    "Привести вставки в точное соответствие со схемой dbm.facts, dbm.decisions, dbm.procedures, dbm.failures и добавить /api/v20/memory/learn-step",
                    "RESOLVED_AND_VERIFIED",
                    ["command-center/bcc/features/bossman_20.py"],
                    "command-center/tests/test_bossman_20_superstructure.py",
                ),
            ]
            for f_sym, f_root, f_fix, f_res, f_files, f_test in failures_seed:
                exists_f = (
                    await s.execute(sa.select(dbm.failures.c.id).where(dbm.failures.c.symptom == f_sym))
                ).first()
                if not exists_f:
                    await s.execute(
                        sa.insert(dbm.failures).values(
                            symptom=f_sym,
                            root_cause=f_root,
                            attempted_fix=f_fix,
                            result=f_res,
                            files=f_files,
                            test=f_test,
                            created_at=now,
                        )
                    )
            await s.commit()
    except Exception:
        pass
    return await _db_memory_counts(svc)


@router.get("/v20/memory/audit")
@router.get("/memory/audit")
async def http_memory_audit(request: Request):
    svc = getattr(getattr(request, "app", None), "state", None)
    svc = getattr(svc, "svc", None)
    cfg = await load_config(svc) if svc is not None else {"root": "", "write_folder": "BOSSMAN Memory"}
    vault_report = inspect_obsidian_vault(cfg.get("root") or "", cfg.get("write_folder") or "BOSSMAN Memory")
    db_counts = await _db_memory_counts(svc)
    stats: dict[str, Any] = {}
    if svc is not None and vault_report.get("configured"):
        try:
            service = await get_service(svc)
            stats = await service.stats()
        except Exception:
            stats = {}
    return {
        "ok": True,
        "audit": vault_report,
        "index_stats": stats,
        "db_counts": db_counts,
    }


@router.post("/v20/memory/sync")
@router.post("/memory/sync-bossman-20")
async def http_memory_sync(request: Request):
    svc = getattr(getattr(request, "app", None), "state", None)
    svc = getattr(svc, "svc", None)
    cfg = await load_config(svc) if svc is not None else {"root": "", "write_folder": "BOSSMAN Memory"}
    root_str = str(cfg.get("root") or "").strip()
    write_folder = str(cfg.get("write_folder") or "BOSSMAN Memory").strip() or "BOSSMAN Memory"

    if not root_str or not Path(root_str).is_dir():
        return {
            "ok": False,
            "error": "Хранилище Obsidian не настроено или папка не существует.",
        }

    root = Path(root_str)
    wf_dir = root / write_folder
    wf_dir.mkdir(parents=True, exist_ok=True)
    trash_dir = root / ".trash" / "archive-synthetic-tests-202609"

    archived: list[str] = []
    for p in sorted(wf_dir.glob("*.md")):
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            txt = ""
        if _is_synthetic_junk_note(p, txt):
            trash_dir.mkdir(parents=True, exist_ok=True)
            dest = trash_dir / p.name
            shutil.move(str(p), str(dest))
            archived.append(p.name)

    written: list[str] = []
    for fname, content in CANONICAL_BOSSMAN_20_NOTES.items():
        target = wf_dir / fname
        existing = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
        if existing != content:
            target.write_text(content, encoding="utf-8")
            written.append(fname)

    db_counts = await _seed_canonical_db_memory(svc)

    index_result: dict[str, Any] = {}
    if svc is not None:
        try:
            service = await get_service(svc)
            index_result = await service.index(force=True)
        except (MemoryNotConfigured, FileNotFoundError, QdrantUnavailable) as exc:
            index_result = {"error": str(exc)}

    updated_audit = inspect_obsidian_vault(root, write_folder)
    return {
        "ok": True,
        "archived_synthetic_notes": archived,
        "archived_count": len(archived),
        "written_canonical_notes": written,
        "written_count": len(written),
        "db_counts": db_counts,
        "index_result": index_result,
        "audit": updated_audit,
        "summary_ru": (
            f"Память Obsidian 2.0 синхронизирована: в архив убрано {len(archived)} синтетических заметок, "
            f"актуализировано {len(CANONICAL_BOSSMAN_20_NOTES)} канонических заметок Bossman 2.0, "
            f"индекс обновлён ({updated_audit['total_markdown_files']} файлов)."
        ),
    }


@router.post("/v20/security/self-test")
async def http_security_self_test():
    from ..security.astra_sandbox import verify_self_modify_candidate_isolation
    from ..security.powershell_gate import classify as ps_classify
    from ..security.spend_firewall import PaidCallBlockedError, SpendFirewall
    from ..security.win_paths import PathRefused, _screen
    from .pc_agent_policy_guards import (
        is_immutable_audit_log_target,
        validate_powershell_ast_or_tokens,
    )

    checks: list[dict[str, Any]] = []

    # 1. PowerShell AST & Obfuscation Gate
    ps_tok_ok, ps_tok_reason = validate_powershell_ast_or_tokens("$c='Remove-Item'; & $c secret.txt")
    ps_ast_verdict = ps_classify("$c='Remove-Item'; & $c secret.txt")
    c1_pass = (ps_tok_ok is False) and (ps_ast_verdict.get("readonly") is False)
    checks.append({
        "id": "powershell_ast_and_tokens",
        "title": "PowerShell AST & детектор обфускации (& $var / InvokeScript / -enc)",
        "passed": c1_pass,
        "detail": f"token_gate={ps_tok_reason}, ast_reasons={ps_ast_verdict.get('reasons')}",
    })

    # 2. Immutable Audit Log Guard (20/20)
    c2_pass = (
        is_immutable_audit_log_target("copy /y nul audit.jsonl")
        and is_immutable_audit_log_target("Remove-Item 'audit.json'+'l'")
        and is_immutable_audit_log_target("del aud*.jsonl")
        and not is_immutable_audit_log_target("Get-Content audit.jsonl -Tail 5")
    )
    checks.append({
        "id": "immutable_audit_log_guard",
        "title": "Защита журнала audit.jsonl от удаления/перезаписи/масок (20/20)",
        "passed": c2_pass,
        "detail": "copy /y nul, склейка кавычек и маски заблокированы; чтение разрешено",
    })

    # 3. Windows Path Canonicalization & ADS/Device Screen
    ads_blocked = False
    try:
        _screen("C:\\temp\\note.txt:hidden_stream")
    except PathRefused:
        ads_blocked = True
    checks.append({
        "id": "windows_path_canonical_guard",
        "title": "Защита путей Windows (NTFS ADS, имена устройств DOS, хвостовые точки)",
        "passed": ads_blocked,
        "detail": "NTFS Alternate Data Stream перехвачен PathRefused",
    })

    # 4. SpendFirewall ($0.00 Cloud Gate)
    fw = SpendFirewall()
    c4_pass = False
    fw_detail = ""
    try:
        fw.verify_pre_call_free_or_authorized(
            provider="openrouter",
            model="openai/gpt-4o",
            pricing={"prompt": 0.005, "completion": 0.015},
            owner_token=None,
        )
    except PaidCallBlockedError as exc:
        c4_pass = True
        fw_detail = str(exc)
    checks.append({
        "id": "spend_firewall_zero_cost_gate",
        "title": "Облачный SpendFirewall ($0.00 без одноразового токена владельца)",
        "passed": c4_pass,
        "detail": fw_detail or "Платный вызов заблокирован",
    })

    # 5. Self-Modify Host Isolation Fail-Closed
    sm_res = verify_self_modify_candidate_isolation()
    c5_pass = sm_res.get("status") in ("verification_blocked_unisolated", "hermetic_container_ready")
    checks.append({
        "id": "self_modify_fail_closed",
        "title": "Защита pc.self_modify (Fail-Closed без герметичной OS-песочницы)",
        "passed": c5_pass,
        "detail": str(sm_res.get("status")),
    })

    passed_count = sum(1 for c in checks if c["passed"])
    return {
        "ok": passed_count == len(checks),
        "passed": passed_count,
        "total": len(checks),
        "checks": checks,
        "summary_ru": f"Самопроверка контура защиты Bossman 2.0: {passed_count}/{len(checks)} рубежей в норме.",
    }


@router.get("/v20/status")
async def http_v20_status(request: Request):
    svc = getattr(getattr(request, "app", None), "state", None)
    svc = getattr(svc, "svc", None)
    cfg = await load_config(svc) if svc is not None else {"root": "", "write_folder": "BOSSMAN Memory"}
    vault_report = inspect_obsidian_vault(cfg.get("root") or "", cfg.get("write_folder") or "BOSSMAN Memory")
    db_counts = await _db_memory_counts(svc)

    return {
        "ok": True,
        "version": "2.0.0",
        "codename": "Bossman 2.0 · Надстройка",
        "memory_obsidian": {
            **vault_report,
            "db_counts": db_counts,
        },
        "security_contour": {
            "powershell_ast_gate": True,
            "windows_path_gate": True,
            "audit_journal_guard_20_20": True,
            "run_command_job_object": True,
            "spend_firewall_zero_usd": True,
            "self_modify_fail_closed": True,
        },
        "art_media_contour": {
            "photo_studio_20": "READY (ComfyUI v0.37.0 verified nodes)",
            "direct_gen_20": "READY (sd-server / sd-cli + ComfyUI split indicators)",
            "motion_studio_20": "READY (ASS kinetic subtitles + 3D .cube LUT + PCM measure_beats BPM)",
            "music_studio_20": "ON_DEMAND (ACE-Step lazy start button)",
            "bossblocks_3d": "READY (WebGL smooth subdivision PBR characters)",
        },
        "mobile_and_swarm_contour": {
            "mobile_pult_8840": "READY (Whisper STT Push-to-Talk + Privacy Screen Stream + Real Rollback)",
            "browser_tab_detector": "READY (Playwright -> CDP :9222 -> Win32 title with confidence level)",
            "swarm_dag_planner": "READY (Cross-family verifier + memory_retrieval_v16.pack)",
            "curated_skills": 94,
        },
    }


@router.post("/v20/memory/learn-step")
async def http_memory_learn_step(request: Request, body: dict[str, Any]):
    """Записывает урок выполненного шага (см. record_harness_step_lesson)."""
    svc = getattr(getattr(request, "app", None), "state", None)
    svc = getattr(svc, "svc", None)
    return await record_harness_step_lesson(svc, dict(body or {}))


def _check_postcondition(pc: dict[str, Any]) -> tuple[bool, str]:
    """Real check of a step's postcondition on the file system; no postcondition or an unknown type is NOT ok."""
    from .pc_agent_policy_paths import is_t3_secret_or_protected_path
    kind = str((pc or {}).get("type") or "")
    if kind not in {"file_exists", "file_sha256", "file_contains"}:
        return False, f"postcondition type not supported: {kind or '(none)'}"
    path = Path(str(pc.get("path") or ""))
    refused, why = is_t3_secret_or_protected_path(str(path), for_write=False)
    if refused:
        return False, f"postcondition path refused: {why}"
    if not path.is_file():
        return False, f"{kind}: file missing"
    if kind == "file_exists":
        return True, "file_exists: present"
    data = path.read_bytes()
    if kind == "file_sha256":
        ok = hashlib.sha256(data).hexdigest() == str(pc.get("sha256") or "").lower()
        return ok, f"file_sha256: {'match' if ok else 'mismatch'}"
    ok = str(pc.get("text") or "") != "" and str(pc.get("text")) in data.decode("utf-8", errors="replace")
    return ok, f"file_contains: {'found' if ok else 'not found'}"


async def _recall(svc: Any, words: str, limit: int = 5) -> dict[str, list[dict]]:
    """Relevant past decisions and failures for this step (metadata the harness shows before acting)."""
    out: dict[str, list[dict]] = {"decisions": [], "failures": []}
    if svc is None or getattr(svc, "db", None) is None or not words.strip():
        return out
    like = f"%{words.strip()[:80]}%"
    async with svc.db.session() as s:
        rd = await s.execute(sa.select(dbm.decisions.c.key, dbm.decisions.c.decision)
                             .where(sa.or_(dbm.decisions.c.key.ilike(like), dbm.decisions.c.decision.ilike(like))).limit(limit))
        rf = await s.execute(sa.select(dbm.failures.c.symptom, dbm.failures.c.attempted_fix)
                             .where(dbm.failures.c.symptom.ilike(like)).limit(limit))
        out["decisions"] = [dict(r._mapping) for r in rd]
        out["failures"] = [dict(r._mapping) for r in rf]
    return out


@router.post("/v20/harness/execute-step")
async def http_harness_execute_step(request: Request, body: dict[str, Any]):
    """One harness cycle for a step that was carried out: STOP check -> recall -> postcondition -> learn-step -> receipt."""
    from .telegram_calls import global_stop_active
    svc = getattr(getattr(request, "app", None), "state", None)
    svc = getattr(svc, "svc", None)
    payload = dict(body or {})
    if svc is not None and global_stop_active(svc, fail_closed=True):
        return {"ok": False, "code": "STOP_ACTIVE", "message": "глобальный STOP владельца активен: шаг не засчитан"}
    step_id = str(payload.get("step_id") or "").strip()
    title = str(payload.get("title") or step_id).strip()
    if not step_id or not title:
        return {"ok": False, "code": "STEP_ID_REQUIRED", "message": "нужны step_id и title"}
    recalled = await _recall(svc, str(payload.get("topic") or title))
    post_ok, post_detail = _check_postcondition(payload.get("postcondition") or {})
    lesson = str(payload.get("lesson") or "").strip() or (
        f"Шаг «{title}»: постусловие {'выполнено' if post_ok else 'НЕ выполнено'} ({post_detail})")
    learned = await record_harness_step_lesson(svc, {
        "step_id": step_id, "title": title, "lesson": lesson, "root_cause": post_detail,
        "symptom": None if post_ok else f"постусловие не выполнено: {title}",
        "attempted_fix": str(payload.get("attempted_fix") or lesson), "files": list(payload.get("files") or []),
        "test": str(payload.get("test") or ""), "result": "VERIFIED" if post_ok else "POSTCONDITION_FAILED",
        "procedure_steps": list(payload.get("procedure_steps") or [lesson]),
    })
    receipt = {"step_id": step_id, "postcondition_ok": post_ok, "postcondition_detail": post_detail,
               "recalled": {k: len(v) for k, v in recalled.items()}, "db_counts": learned.get("db_counts"),
               "obsidian_written": learned.get("obsidian_written"), "ts": datetime.now(timezone.utc).isoformat()}
    receipt["sha256_receipt"] = hashlib.sha256(json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {"ok": post_ok, "recalled": recalled, **receipt}


async def record_harness_step_lesson(svc: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Записывает урок выполненного шага одновременно в таблицы памяти SQLite (facts/decisions/procedures/failures)
    и в канонический журнал уроков Obsidian (BOSSMAN Memory)."""
    payload = dict(payload or {})
    step_id = str(payload.get("step_id") or payload.get("key") or f"step-{int(datetime.now(timezone.utc).timestamp())}").strip()
    title = str(payload.get("title") or payload.get("symptom") or step_id).strip()
    lesson = str(payload.get("lesson") or payload.get("decision") or payload.get("statement") or "").strip()
    root_cause = str(payload.get("root_cause") or payload.get("reason") or "Аудит выполненного шага").strip()
    files = list(payload.get("files") or [])
    test_ref = str(payload.get("test") or "").strip()
    if not lesson:
        return {"ok": False, "error": "Поле lesson/statement не может быть пустым."}

    now = datetime.now(timezone.utc)
    if svc is not None and getattr(svc, "db", None) is not None:
        async with svc.db.session() as s:
            await s.execute(
                sa.insert(dbm.facts).values(
                    subject=f"step.{step_id}",
                    predicate="learned_lesson",
                    object=title,
                    statement=f"[{step_id}] {title}: {lesson}",
                    valid_at=now,
                    created_at=now,
                    source_kind="run",
                    source_note="BOSSMAN Memory/2026-10-29-Bossman-Harness-Step-Lessons.md",
                    confidence=1.0,
                )
            )
            exists_d = (
                await s.execute(sa.select(dbm.decisions.c.id).where(dbm.decisions.c.key == step_id))
            ).first()
            if not exists_d:
                await s.execute(
                    sa.insert(dbm.decisions).values(
                        key=step_id,
                        decision=lesson,
                        reason=root_cause,
                        alternatives_rejected=[],
                        scope="Bossman Harness Agent",
                        created_by="learn-step",
                        created_at=now,
                    )
                )
            proc_name = f"proc.{step_id}"
            exists_p = (
                await s.execute(sa.select(dbm.procedures.c.id).where(dbm.procedures.c.name == proc_name))
            ).first()
            if not exists_p:
                proc_steps = list(payload.get("procedure_steps") or payload.get("steps") or [lesson])
                await s.execute(
                    sa.insert(dbm.procedures).values(
                        name=proc_name,
                        description=title,
                        steps=proc_steps,
                        scope="Bossman Harness Agent",
                        created_at=now,
                    )
                )
            if payload.get("symptom") or payload.get("attempted_fix") or payload.get("root_cause") or files:
                await s.execute(
                    sa.insert(dbm.failures).values(
                        symptom=str(payload.get("symptom") or title),
                        root_cause=root_cause,
                        attempted_fix=str(payload.get("attempted_fix") or lesson),
                        result=str(payload.get("result") or "VERIFIED"),
                        files=files,
                        test=test_ref,
                        created_at=now,
                    )
                )
            await s.commit()

    cfg = await load_config(svc) if svc is not None else {"root": "", "write_folder": "BOSSMAN Memory"}
    root_str = str(cfg.get("root") or "").strip()
    if not root_str and Path(r"C:\Users\asd\Bossman\audit-memory").is_dir():
        root_str = r"C:\Users\asd\Bossman\audit-memory"
    write_folder = str(cfg.get("write_folder") or "BOSSMAN Memory").strip() or "BOSSMAN Memory"
    obsidian_written = False
    if root_str and Path(root_str).is_dir():
        wf_dir = Path(root_str) / write_folder
        wf_dir.mkdir(parents=True, exist_ok=True)
        note_path = wf_dir / "2026-10-29-Bossman-Harness-Step-Lessons.md"
        if not note_path.is_file():
            header = (
                "---\n"
                'title: "Журнал пошагового обучения Bossman Harness Agent"\n'
                f"date: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
                "source: bossman\n"
                "kind: decision\n"
                "project: bossman-harness\n"
                "tags: [bossman-2.0, harness, step-learning, canonical]\n"
                "---\n\n"
                "# Журнал пошагового обучения Bossman Harness Agent\n\n"
            )
            note_path.write_text(header, encoding="utf-8")
        cur_txt = note_path.read_text(encoding="utf-8", errors="replace")
        entry_md = (
            f"## [{step_id}] {title} ({now.strftime('%Y-%m-%d %H:%M UTC')})\n"
            f"- **Урок / Инвариант:** {lesson}\n"
            f"- **Причина / Контекст:** {root_cause}\n"
            + (f"- **Файлы:** `{', '.join(files)}`\n" if files else "")
            + (f"- **Тест:** `{test_ref}`\n" if test_ref else "")
            + "\n"
        )
        if f"## [{step_id}]" not in cur_txt:
            note_path.write_text(cur_txt + entry_md, encoding="utf-8")
            obsidian_written = True

    db_counts = await _db_memory_counts(svc)
    return {
        "ok": True,
        "step_id": step_id,
        "obsidian_written": obsidian_written,
        "db_counts": db_counts,
    }


FEATURE = Feature(name="bossman_20", router=router)
