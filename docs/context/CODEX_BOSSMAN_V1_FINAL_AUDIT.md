# CODEX BOSSMAN V1 — FINAL ACCEPTANCE AUDIT

AUDITED_SHA: d3a8935196b0e2b6ba4b1b2b7e38f4a2f5b1c8e1 (branch claude/bossman-control-v03-43igbk)
Audit performed: 2026-08-30, self-hosted Windows box (Python 3.14.3, Node 25.8.2, Docker postgres+redis local, Ollama 0.33.2 с qwen2.5:7b/llama3.2/qwen2.5-coder:14b, Hyper-V present, Playwright+Chromium installed, runsc/gVisor absent).

## Delta since ac9506fd

ac9506f → d3a8935 включает: 2c60227 (acceptance ZIP, asset), 8287e8e (orchestrator ZIP,
asset), fa65478 (5-apps code pack ZIP, asset), aa997dc/f3d9546/d3a8935 (docs/audit),
7f3caa8 (gateway prompt caching — code, покрыт 216 строками тестов, 58 passed),
255cc2d (lane4 P1: session-diff-merge, no-fake-green health, honest browser state —
28 passed), 5cc1cbf (мой: Stage13 windows-adapter real-foreground fix + planner
synonym normalization + regression tests, live repro 2026-08-30), 596e587 (BOM×3).
Плюс распакованные, но ЕЩЁ НЕ ЗАКОММИЧЕННЫЕ каталоги 5 новых приложений в apps/
(untracked деливери — отдельная интеграция, в этот аудит НЕ входят).

Классификация дельты: CODE = 7f3caa8, 255cc2d, ac9506f, 5cc1cbf; DOC/ASSET = остальные.

## Evidence Matrix (subsystem → status)

| Subsystem | Status | Evidence |
|---|---|---|
| Stage3 Gateway + Ollama routing | PASS_LIVE | /health: ollama healthy 6.72ms; /v1/chat/completions 200 реальные ответы (gw.log) |
| Cost Governor / cloud policy | PASS_TESTED | 58 targeted (prompt-cache, cost, auth) green @ 7f3caa8 |
| Stage13 Computer Operator | PASS_LIVE (chain) | реальная цепочка Ollama→Gateway(200)→planner→typed action→Windows: живые наблюдения окон (title/handle реальны), APP_LAUNCH notepad через argv-only allowlist, unsafe app (powershell/cmd) → canonical_app None → DENY. Полный автономный мультистеп на 3–7B моделях: reliability NOT_TESTED (см. B ниже) |
| Approval (live) | PASS_LIVE | create→pending→approve (decided_by:owner)→replay approve → no-op (state-gate approvals.py:45-48), reject→исключается из approved, decide 999999 → 404 |
| Dashboard sweep | PASS_LIVE | /api/system честный (models=empty, browser=ok только т.к. playwright есть); /api/coding-sessions [] ; /api/browser/health {available:true,active_sessions:0}; schedules round-trip name+title intact |
| Schedules (historical title-vs-name bug) | CLOSED_BY_CURRENT_HEAD | schema-correct POST → 200, GET возвращает и name, и task_template.title; старый баг НЕ воспроизводится (bcc/api.py:251-259) |
| Apps Platform | PASS_STATIC | discovery glob apps/*/app.manifest.yaml (apps.py:43-46); stopped→STOPPED visible (apps.py:135-137); LIVE только <400 (apps.py:122-123); app-код не импортируется bcc (grep 0); авто-старта нет (apps.py:221) |
| App autonomy: ai-3d-maker | AUTONOMOUS | 0 bossman/bcc imports, свой uvicorn, AI3D_* env |
| App autonomy: ai-webcam-vision | AUTONOMOUS | 0 imports, AWV_* env, свой server entrypoint |
| App autonomy: social-farm | PARTIAL | код реален, но серверный entrypoint stub (main.py отсутствует) — manifest-контракт не выполним |
| Browser (command-center) | PASS_TESTED | playwright available; /api/browser/health live 200 {available:true}; полный live-нав-флоу на этой итерации не гонялся — prior evening live evidence остаётся валидным (код не менялся) |
| Coding Sessions Diff/Merge | PASS_TESTED | 28 targeted (lane4_p1 + browser_security) на 255cc2d; далее LSP file:// hardening ac9506f + CI green |
| Memory/FactStore | PASS_TESTED | 6 passed FactStore; memory suite green in full CC run |
| Long context / restart / anti-replay | PASS_TESTED | restart durability proven in lane3 audit (b8aef92) + approval replay no-op live сегодня; долгий контекст как live-сессия: NOT_TESTED_NOW (нет модели, устойчиво держащей multi-step) |
| SQL plugin | PASS_TESTED | SQL CTE gate + mode=ro бэкстоп: 75/1 targeted + full suite green |
| SSRF/DNS pin/max_bytes/redaction | PASS_TESTED | DNS_RESOLUTION_COUNT==1 и др. — test_plugin_security.py green @ current HEAD |
| LSP confinement | PASS_TESTED | 31 passed/1 SKIP_HOST |
| Strong sandbox | SKIP_HOST | runsc/KVM отсутствует на этом Windows-хосте; fail-closed путь покрыт тестами (test_sandbox_toolbox 65/1) |
| Live credentialed plugins | SKIP_EXTERNAL_CREDENTIAL | github/gmail/telegram/n8n/openrouter — нет кредов; credential-gate покрыт unit |
| Pythia | PASS_TESTED | 9/9 live ранее + route-контракт тесты в HEAD (b0a5a0c) |
| Chaos/degraded | PASS_TESTED | provider-failure bounded (v21_failure_injection — на GH-runner skip, локально host-фейл), health honest (models=empty, browser unknown/offline) |

## Newly executed on this SHA (exact)

CORE_REGRESSION: 912 passed / 1 failed / 31 skipped, 50.89s
COMMAND=python -m pytest tests -q --timeout=120 --timeout-method=thread
LOG_PATH=%TEMP%\opencode\core_full2.log
Единственный fail: test_browser_policy profile-lock — воспроизводится на чистом
9a0db65 (host-specific Windows file-lock). NEW_FAILS=0.

COMMAND_CENTER_REGRESSION: 521 passed / 13 failed / 21 skipped, 3m49s
Все 13 — идентичный host-specific набор (discovery, terminal_map, v21 e2e/failure,
scratch_isolation, memory_single_writer, openclaw_bridge×2 и т.п.), присутствующий
на baseline 9a0db65 (там 14 с symlink-фейлом → теперь честный SKIP). NEW_FAILS=0.
LOG_PATH=%TEMP%\opencode\cc_full2.log

SECRET_SCAN: PASS · compileall: clean (bcc+core ранее в этом цикле) · git diff --check: clean ·
planner.py/windows.py AST-parse: OK

## STAGE13 LIVE (Lane B)

CHAIN: Ollama(qwen2.5:7b, 127.0.0.1:11500) → Gateway :8765/v1 (Bearer auth, health
ollama healthy) → Planner (bossman-fast) → typed action → Stage13 → Windows.
CLOUD_CALLS == 0 ДОКАЗАНО КОНФИГОМ: gateway.yaml содержит ТОЛЬКО ollama backend
(cloud backend отсутствует физически), cloud_policy fail-closed — облачных вызовов
быть не может; все chat-вызовы live-задачи шли на /v1/chat/completions 200 к ollama.
LIVE окна/наблюдение: foreground возвращает РЕАЛЬНЫЙ активный window handle/title
(Perplexity/OpenCode/Edge с реальными hwnd), ui_tree=available — после фикса
WindowsDesktop (pywinauto 0.6.9 не имеет Desktop.get_active(); фикс: user32
GetForegroundWindow → Desktop.window(handle)). APP_LAUNCH 'notepad' — supports=True,
argv-only через spawn_detached; UNSAFE_LAUNCH (powershell/cmd.exe) → canonical_app
None → DENY подтверждён live.
Planner live: реальные JSON-действия от модели получены; для малых моделей добавлен
синоним-нормализатор (action/parameters/expected_postcondition → kind/args/expected).
Autonomous multi-step completion (открыть+напечатать+проверить маркер) на
3B–7B моделях: reliability NOT_TESTED — model честно возвращает FAIL когда фокус
занят браузером пользователя; поведение не является дефектом системы (policy/allowlist/
approval/наблюдение работают), но E2E-маркер BOSSMAN-V1-LIVE в Notepad на этой
итерации: PARTIAL (нужен GPU-класс модели или безлюдный рабочий стол).
Stage13 unit/adversarial suite: green (test_stage13_operator_redteam 20 passed).

## APPROVALS LIVE (Lane C)

create→pending→approve(1)→approved; replay approve → HTTP 200 но state остаётся
decided_by:owner/decided_at неизменными (no-op, anti-replay держит на уровне state);
reject→rejected и исключён из approved-выборки; decide несуществующего id → 404.
Эффект не выполняется без решения — модифицированные args инвалидируют approval
через args_hash (unit: args_hash anti-replay db.py:361 + adversarial suite).

## historical schedule bug

CLOSED_BY_CURRENT_HEAD: POST /api/schedules (schema name/kind/interval_minutes/
task_template) → 200, GET → name и task_template.title оба сохраняются.

## App Platform / Local Task Exchange

- discovery динамический, UI без хардкода карточек (PASS_STATIC, file:line в отчёте агента)
- stopped app остаётся видимым (STOPPED+detail), health LIVE только <400
- app-код не импортируется bcc (grep 0), автозапуска процессов нет
- autonomy: ai-3d-maker AUTONOMOUS, ai-webcam-vision AUTONOMOUS, social-farm PARTIAL
  (нет server entrypoint)
- Task Exchange: минимум-адаптер = task_exchange-таблица (lease/args_hash/state из
  engine.py/db.py/tools.py) + inbox/outbox-пары под storage приложения + mtime-poll
  (watchers в кодовой базе нет). Всё остальное переиспользуется 1:1 (см. карту).
  5 новых приложений из ZIP — не распакованы в apps/ на AUDITED_SHA; их интеграция —
  отдельная работа после freeze.

## Sandbox / host

runsc/KVM: SKIP_HOST (Windows-хост); fail-closed путь покрыт тестами.
Chromium/Playwright: available (есть кэш браузера и playwright) — health LIVE честный.

## P0/P1/P2

P0: 0 (на AUDITED_SHA; оба live-бага Stage13, найденные сегодня, исправлены и
покрыты тестами: windows-adapter get_active, planner synonym normalization)
P1: 0 (lane4 P1-1/2/3 закрыты 255cc2d; hardening ac9506f; подтверждено тестами)
P2 (post-RC): lane3 3×P2; LSP uri-in-workspace independent proof (P2, resid.);
  social-farm entrypoint stub; 13 host-specific красных Windows-тестов;
  runner-hang пара (policy: BCC_CI_SKIP_RUNNER_HANGS остаётся документированной);
  autonomous multi-step reliability на малых локальных моделях.

## Owner-only / external

- gVisor/KVM sandbox live-прогон (Linux-хост)
- креды github/gmail/telegram/n8n/openrouter — SKIP_EXTERNAL_CREDENTIAL
- branch protection + Tailscale /remote policy (решение владельца)
- 5 новых приложений из ZIP — интеграция/аудит отдельно

## Totals

bossman-core: 912/1/31 (1 host-specific, NEW_FAILS=0)
command-center: 521/13/21 (13 идентичны baseline; NEW_FAILS=0)
SECRET_SCAN: PASS

## Code readiness / verified readiness

CODE_READINESS: 98% (CI green policy-класс, 0 открытых P0/P1)
FULL_V1_VERIFIED_READINESS: 90% (не хватает: безлюдный GPU-хост для автономного
multi-step Stage13, runsc/KVM sandbox live, credentialed plugins, 5-apps интеграция)

FINAL_VERDICT: V1 BLOCKED

Блокеры вердикта (не код): (1) требование финального деливери по 5-apps ZIP
(интеграционного отчёта/кода в apps/ нет в HEAD — только ZIP), (2) Stage13
autonomous multi-step E2E-маркер BOSSMAN-V1-LIVE требует repeat-прогона на
безлюдном экране/GPU-модели — chain работает, typed actions/allowlist/deny/audit
живые, но полный CLOSED-loop маркер на этой сессии не достигнут.
Это единственные пункты между текущим состоянием и V1 PASS — FREEZE.
