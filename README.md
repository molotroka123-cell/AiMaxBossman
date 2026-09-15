# AiMaxBossman

Локальное приложение для задач с ИИ, работы с файлами, браузером, сайтами и видео.

**Рабочая ветка: `claude/bossman-final-convergence-hu2702`.**
[Установка и запуск](INSTALL.md) · [Аудит и изменения 15 сентября](docs/final/PRODUCT_CLEANUP_20260915.md) · [Предыдущие результаты](docs/final/CURRENT_STATE.md)

## Запуск

Нужен Python 3.11+; для Video Studio — FFmpeg. Первый запуск устанавливает
пакеты приложения, готовые интеграции и Chromium.

Windows, из PowerShell в папке репозитория:

```powershell
.\start-bossman.ps1
```

Linux/macOS:

```bash
bash start-bossman.sh
```

Панель: <http://127.0.0.1:8800>. Для повторного запуска без переустановки:
`start-bossman.ps1 -SkipInstall` или `bash start-bossman.sh --skip-install`.
Подключите свою локальную модель или провайдера в настройках панели.

## Что используется из open source

| Возможность | Готовая основа | Как используется |
|---|---|---|
| Браузер | Playwright + Chromium | Существующий управляемый браузер Bossman |
| Коннекторы | Официальный MCP Python SDK | Устанавливается профилем runtime; серверы подключаются в панели |
| Визуальное создание сайтов | GrapesJS 0.23.6 | «Веб-дизайн → Конструктор блоков»: блоки, слои, стили, отмена; файлы движка поставляются локально |
| Монтаж и экспорт | FFmpeg / ffprobe | Существующий Video Studio |
| Перенос монтажа | OpenTimelineIO | Импорт/экспорт OTIO через уже существующие команды Video Studio |
| PDF | pypdf | Существующий разбор документов |
| Управление Windows | pywinauto / pywin32 / PyAutoGUI | Устанавливаются на Windows; требуют реального сеанса рабочего стола |
| Агент для кода | OpenHands | Существующий адаптер; нужен настроенный sidecar и провайдер |

Сохранение проектов, права на действия, бюджеты и проверка результата остаются
общими для приложения. Подключение библиотеки не даёт ей права обходить эти правила.

OpenContext и Windows-MCP пока не подключены к основному пути выполнения;
они не перечисляются здесь как работающие функции. Текущее состояние адаптеров:
[INTEGRATION_STATE](docs/final/INTEGRATION_STATE.md).

## Проверка

```bash
python tools/ci_secret_scan.py
python tools/skips_registry.py --check
python scripts/update_readme_scorecard.py --check
```

Тесты выполняются из каталогов `command-center` и `bossman-core` после установки
соответствующего `[dev]`. Проверка собранного приложения без доступа к исходникам:

```bash
python tools/build_local_bundle.py --out /path/to/new-empty-directory
```

Результаты на Linux и GitHub Actions не заменяют проверку на компьютере владельца
с Windows, Ryzen AI Max+ 395 и настоящей моделью. Приложение не объявляет задачу
выполненной только на основании ответа модели.

## Структура

- `command-center/` — приложение, интерфейс, задачи и интеграции.
- `bossman-core/` — выполнение, провайдеры, права и восстановление.
- `apps/` — запускаемые приложения.
- `bossman_shared/`, `learning/`, `schemas/` — общие контракты.
- `tools/`, `scripts/` — сборка и проверка.
- `docs/final/` — актуальные инженерные отчёты; [архив](docs/archive/root-history/README.md) хранит старые снимки.

<details>
<summary>Историческая оценка от 7 сентября — не оценка текущего коммита</summary>

Автоматически поддерживаемый снимок из `docs/benchmark/current-scorecard.json`.
Новые оценки без новых измерений не выставляются.

<!-- BOSSMAN_LIVE_SCORECARD_START -->
| # | Ось системы | Оценка | Статус | Уверенность | Улики |
|---|---|---:|---|---|---|
| 1 | Execution Truth | 8.8/10 | VERIFIED | HIGH | AT-01 effect obligations and fresh post-state verification are closed with regression coverage; Fencing, anti-replay and recovery invariants remain in force; V6 performance work did not weaken effect-boundary semantics |
| 2 | Security | 8.5/10 | VERIFIED | HIGH | Secret scan and blocking SAST/SCA completed successfully on the V6 code candidate; Approvals, authorization, privacy routing, freshness and fail-closed behavior were explicitly preserved through V6 |
| 3 | Tooling / OS Integration | 7.5/10 | INTEGRATED | MEDIUM | Windows-path CI is green on the current V6 code candidate; Owner-session reconnect, app restart recovery, provider UX and Trading Lab crash paths have repository fixes |
| 4 | Organization Layer | 7.3/10 | INTEGRATED | MEDIUM | Mission/task orchestration remains durable and blocked-only missions now terminate honestly instead of appearing to run forever; Organization/Fleet execution contracts and verified-child completion rules remain covered |
| 5 | Fleet & Resources | 7.0/10 | INTEGRATED | MEDIUM | Lease/fence/queue safety contracts remain covered and V6 does not bypass the canonical execution path; Interactive work is protected from background FFmpeg contention by lowered child-process priority |
| 6 | Memory / Context | 6.5/10 | IMPLEMENTED | MEDIUM | Durable task/recovery memory and scoped context contracts remain intact; Long-session testing now explicitly watches stale context, zombie runs, duplicate subscribers and memory growth |
| 7 | Testing / CI | 8.0/10 | VERIFIED | MEDIUM | Bossman Core full coverage/rest/security/gateway/stage8-14 jobs are green on 413a97a1; ASTRA acceptance, Solana safety, Windows paths and secret/SAST gates are green; Python 3.14 is a hard Command Center lane |
| 8 | Observability / CEO Control | 7.0/10 | PARTIAL | MEDIUM | V6 adds Services.start phase tracing, UI_READY/first-page timing and Computer Use phase_timing; Testing-period evidence from owner session 6cbb17ce84db is retained with corrected dead-click classification |
| 9 | Treasury / Cost | 6.8/10 | IMPLEMENTED | MEDIUM | Budget/cost gates remain unchanged and fail-closed during V6 performance work; Unknown pricing is not treated as free and Fable hard-cap accounting remains isolated from tests |
| 10 | Mission UX / Command Center | 6.8/10 | IMPLEMENTED | MEDIUM | V6 lazy pages reduced first-render modules from 42/788 KiB to 14/290 KiB in the measured harness; Owner-session reconnect, blocked mission, app restart and provider/trading error paths received targeted fixes |

- **Current bottleneck:** V6 phase 0/1 is repository-complete pending external validation: Core/ASTRA/Solana/Windows/security evidence is green on the current code candidate, but an uninterrupted full Command Center exact-SHA matrix plus owner Windows/local-model/Video/Web Designer/long-session acceptance is still missing.
- **Next highest-value fix:** Finish one uninterrupted exact-SHA CI certification, then run the second owner Dashboard acceptance on Windows with the configured model/provider and close only reproduced Video Studio, Web Designer and long-session findings.
- **Last evidence SHA:** `413a97a1ce2936f9543fea5d8f0b529256fc1de2` · **Current HEAD SHA:** `see git rev-parse HEAD` · **Evidence freshness:** PARTIALLY_STALE_AFTER_DOC_COMMITS
- **Last scorecard update:** 2026-09-07
- **Benchmark hard failures:** none observed
- **Live hardware attestation:** PENDING
- **Exact-SHA CI:** UNPROVEN

_Среднее (вторично, не авторитетно): 7.4/10. 10.0 = ATTESTED; ни одна ось не ATTESTED без живой аттестации железа._
<!-- BOSSMAN_LIVE_SCORECARD_END -->

</details>
