# STAGE 13 STATUS — Computer Operator / Dispatch

HEAD: 1d45d08
BRANCH: claude/bossman-control-v03-43igbk

## IMPLEMENTED
- `bossman/computer_operator/` (16 файлов, из интеграционного пакета): models (TaskState 12 состояний, ComputerAction, ExpectedState), manager (observe→plan→policy→approval→action→re-observe→verify), policy (consequential-список send/pay/delete/git_push/…→approval), verifier, store (JSON-journal, atomic replace), routes `/computer/*`, subsystem, adapters (windows UIA/input, browser, screenshot, vision, router)
- Зарегистрирован в api.py: subsystem + router

## WIRED
- Подсистемы Core: resource_brain, remote_client, search_everything, video_factory, sandbox, computer_operator — все 6 регистрируются (починен P0: `getattr(mod, factory)` с несуществующим именем глотался except'ом и пропускал ВСЕ подсистемы)
- Approvals: computer actions идут через существующий `approvals.create/wait` (Stage 5 boundary)

## UNIT_TESTED
- Пакет: policy/planner/recovery/verifier — 12 passed

## ADVERSARIAL_TESTED (новые red-team батареи, 189 тестов)
| Файл | Тестов | Что доказано |
|---|---|---|
| test_stage13_auth_redteam | 31 | route matrix: 61/70 авторизованы, 9 public обоснованы; anonymous/invalid/revoked/locked→denied; IDOR cross-device; WS auth до подписки, токен не в URL; approve не самовыдаётся |
| test_stage13_ailab_redteam | 83+2skip | traversal-батарея 26 payloads (../, UNC, %2e%2e, C:\, NUL, 500 chars) → uniform 404 без host-path в ответе; admin-only; lease |
| test_stage13_hostexec_redteam | 57 | argv-only sweep (AST по всему core); 11 injection payloads → literal argv; allowlist exact identity (21 lookalike → reject) |
| test_stage13_operator_redteam | 21 | LLM не может кликнуть BOSSMAN approval UI (architectural deny); desktop ControlLease эксклюзивен (2 задачи → desktop busy); stale generation после approval → reject; injected "ignore previous instructions" не расширяет права; secret entry → approval + REDACTED в журнале |

## LOCAL_LIVE_VERIFIED
- Полный bossman-core: **681 passed / 30 skipped / 0 failed**
- Live local model qwen2.5:7b (Ollama 11435): PASS
- Live OpenRouter: PASS (предыдущий прогон, 1 inference)

## CI_VERIFIED
- Bossman Core CI @ 1d45d08: **success**
- Command Center CI @ 1d45d08: in_progress на момент отчёта (проверить в CI_NOTES)

## BLOCKED_BY_HOST
- Реальные Windows desktop-сценарии (Notepad/Calculator UIA-клики) — требуют pywinauto + интерактивную сессию; на CI-runner невозможны, на машине владельца — по команде
- KVM/runsc, Chromium, macOS/Swift — как раньше

## OPEN P0
- нет (factory-баг, LLM-click-approve, lease-гонка, stale approval — исправлены и покрыты)

## OPEN P1
- `projects/runner.py:76` shell-exec от owner-конфига (не attacker-reachable; зафиксирован ratchet-тестом KNOWN_SHELL_EXCEPTIONS)
- Command Center CI: 12 pre-existing Windows-failures в чужом v23_openclaw (см. HARDWARE_RUN_AUDIT)

## NEXT
1. Реальные desktop-сценарии A–M из ТЗ на машине владельца (BOSSMAN_LIVE_DESKTOP=1)
2. Vision grounding через bossman-vision + DXGI capture
3. Stage12 PWA: computer-панель (observe/control/approve)
