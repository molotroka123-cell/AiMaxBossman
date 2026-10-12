# Аудит готовности Bossman к продакшн-сборке (11.10.2026, вечер)

Честный срез: что работает на каком SHA, что исправлено за этот прогон, что проверено
независимо, что осталось. Документ не сертифицирует продукт; каждая строка привязана к
команде, результату или файлу улик (относительные пути репозитория).

## 1. Идентичность

| Что | Значение |
|---|---|
| Ветка | `feat/bossman-genjutsu-jeff-unified-20261010`, PR #108 → `main` |
| HEAD при написании | HEAD — коммит этого аудита (fix(ci) для test_companion_remote_access + документы), родитель `3910be96`; точный SHA: `git log -1` |
| Коммиты этого прогона | `9520a45a` удалённый доступ из пульта; `4f4e7183` ночной раннер, аудит безопасности, улики браузера, сценарии заработка; `3910be96` WIP Poker (Codex) |
| SHA архива | `9520a45a610a05a59aacfb89904dc909ea29a0aa` (после него только tools/docs/poker-wip, продуктовый код не менялся) |
| Архив | `dist-9520a45a/BOSSMAN-Windows-x64-9520a45a610a.zip`, 2 030 445 323 байт, 23 868 файлов, `BOSSMAN_BUILD_INPUTS=LOCKED`, профиль release, `required_downloads=0` |
| SHA256 архива | `ebd82c533b0fd897df7f68555d864d784953ef9dc0bc056db2651dfda5fa7cc8` |
| Верификатор | `tools/verify_windows_bundle.py --archive … --expected-sha 9520a45a…` → `BOSSMAN_BUNDLE_ACCEPTANCE=PASS`, exit 0 |
| Установленная сборка на ПК | `6de18f8d9a08` (прежняя; НЕ заменена кандидатом) |
| Дерево | seed/registry на `250dabbf` (сайт), CI-улик для листьев пока 0 — обновляется после зелёного CC CI |

## 2. CI точного SHA

`9520a45a` (все workflow завершены): root-ci PASS · Bossman Core CI PASS · Solana PASS ·
PostgreSQL PASS · Shipped app contracts PASS · Local bundle PASS · Installed product chain PASS ·
Fable media and Fleet acceptance PASS · Editors user safety PASS · internal benchmark PASS ·
PIT foundation PASS · 1.5 Economy PASS · ASTRA acceptance PASS · Owner scenarios PASS ·
OSS integrations PASS · V2 Auto-Repair PASS · Windows 100 real checks PASS ·
**Intelligence Preservation FAIL (по замыслу: нужен комментарий-улика владельца на SHA)** ·
**Command Center CI CANCELLED** (отменён следующим push; concurrency) ·
One-download Windows application **PASS** (jobs bundle, owner-experience, freeze — все success; на CI-раннере обход интерфейса уложился в 900 с, значит локальный таймаут — особенность ПК владельца, не сборки). CI-архив: 2 030 445 312 байт против 2 030 445 323 локально — побайтовая воспроизводимость НЕ подтверждена (различие в zip-метаданных не проверено), содержимое сверять по SHA256SUMS внутри архивов.

`3910be96`: все workflow зелёные, кроме Intelligence Preservation (владелец) и **Command Center CI FAIL**: 5 тестов `test_companion_remote_access.py` падали только на Linux-раннере (тест подменял `os.name`, pathlib выбирал WindowsPath). Исправлено в `4c29b274` флагом `IS_WINDOWS`. **CI `4c29b274`: Command Center CI PASS** (run 38185553138: py3.11/3.12/3.14, windows paths, core runtime ubuntu+windows, секреты/JS — все success; параллельный run 38185555767 упал одним тайминг-тестом `test_jeff_math_assist[3**2**20]` 1.03 с > 1.0 с на нагруженном раннере, локально отказ 0.000 с — флейк, тест не менялся). Остальные workflow на `4c29b274` зелёные, кроме Intelligence Preservation (владелец).

## 3. Матрица требований

| Требование | Код | Тест | Реальный прогон | Статус | Блокер / следующий шаг |
|---|---|---|---|---|---|
| Запуск Bossman, один backend CLI/dashboard/Telegram | `bcc` :8801, пульт и Jeff на нём же | CI | пульт и браузер через :8801 11.10 | TESTED | — |
| Локальная модель без скрытого cloud fallback | `bcc.pit` local_chat_only | tests | Jeff 2.0 сейчас на облачных free (локальная занята/нестабильна) | NOT_RUN | после перезагрузки: Ollama локально, проверка ответа без сети |
| terminal.run, безопасное управление приложениями | `computer_operator`, пульт `/chatgpt` | 25 тестов remote_access | запуск ChatGPT не проверялся вживую | TESTED | владелец: `/chatgpt` в пульте |
| Claude CLI: запуск, продолжение, Remote Control | `remote_access.py`, keeper | 25 тестов, Mistral review | keeper реальный прогон 15:33; Connected (Codex) | TESTED | reboot → Connected NOT_RUN |
| STOP, сохранение задач, восстановление | `delegation_locked`, STOP-файлы | tests | не прогонялось | TESTED | ночь: `cycle-*.json` |
| Автозапуск после reboot | задачи `BossmanOne-1..3`, `BossmanJeff2`, `BossmanClaudeRemote` | — | NOT_RUN | OWNER_STEP | автологон + reboot |
| Выход в интернет (computer use + vision) | `/api/browser/*`, Chromium в сборке | CI browser | Wikipedia 11.10, DOM + скриншот + vision | OWNER_VERIFIABLE | `docs/evidence/browse-proof-20261011/` |
| Motion Studio V2 | FaceFusion, Wan VACE, `tools/v2_pipeline` | gates | IMG_0889 сходство 0.70; ролики 02/03 0–8.6 с | PARTIAL | GPU нестабилен; оценки ≥6 не подтверждены владельцем |
| Poker: импорт записей, разметка | `recording_dataset.py` (Codex) | 16 tests | GLM-разметка отклонена | NOT_RUN (обучение) | ручная разметка held-out |
| Jeff/голос, паспорта | `bcc.pit` | tests | работает 11.10 | TESTED | 9-страничный документ владельца не прислан |
| Fleet, обменник, trading paper | код есть | tests | не прогонялось сегодня | PRESENT | — |
| Безопасность ПК | `tools/security_audit.ps1` | ревью free-моделей | FAIL 0 / WARN 5 | TESTED | SSH-правило, BitLocker — владелец |
| Архив: AGPL, notices, без ключей/медиа | builder + verifier | verifier PASS | `LICENSE`, `LICENSES/`, `LICENSING.md` в архиве; сборка из чистого worktree без runtime-данных | TESTED | — |
| Распаковка → запуск → базовая задача → перезапуск | `app-support/bundle_evening_test.py --full --ui-sweep` | — | доктор OK (17 pass / 5 warn); приёмка PASS: identity = SHA, auth PASS, 119 assets, restart_persistence PASS; **installed_ui_sweep TIMEOUT 900 с**; live-model OWNER_REQUIRED (ключ OpenRouter в сборку не передаётся намеренно) | PARTIAL (OWNER_EVENING_RESULT=FAIL из-за таймаута обхода) | повторный обход без лимита времени запущен отдельно (см. раздел 6) |

## 4. Что исправлено за прогон

- Пульт: удалённый доступ с телефона (/remote и др.), STOP блокирует все запуски (найдено
  независимым review Mistral Large 4, исправлено до коммита).
- Найдена и обойдена ловушка: установленный пульт падает на незнакомых ключах `config.json`
  (конфиг откатан из бэкапа, новые ключи не добавляются).
- Аудит безопасности: ложный PASS SMB1 без прав администратора, null-процессы в списке
  портов, шумный фильтр ключей (ревью Nemotron Ultra, free).
- Ночной раннер: защита от пустого аудита, секретные переменные не попадают в дочерний
  Claude, хвосты логов санируются (ревью Nemotron Ultra, free).
- Ключ RunPod убран из checkout в хранилище ключей.
- ПК разгружен: FaceFusion/ffmpeg/автопилот остановлены, бенч-задача автозапуска отключена.

## 5. Независимые проверки vs авторские

| Проверка | Кто |
|---|---|
| diff remote_access/service/keeper | Mistral Large 4 (платный ключ владельца, ~€0.02) — независимая |
| PowerShell keeper + аудит | Nemotron 3 Ultra :free, Nemotron 3.5 Lightning :free — независимые |
| night_autonomy.py | Nemotron 3 Ultra :free — независимая |
| скриншот браузера | dots-3-note-preview :free (vision) — независимая |
| тесты 25 + 318 companion | pytest — авторские |
| верификатор архива, evening test | существующие инструменты репозитория — авторские инструменты, реальный прогон |

## 6. Что осталось неисправным / не проверенным и как воспроизвести

1. Intelligence Preservation красный: нужен комментарий владельца на точном SHA (PR #108).
2. Command Center CI: на `3910be96` красный из-за платформозависимого теста (исправлено в этом коммите);
   зелёный CC CI нужен на новом HEAD: `gh run list --branch feat/bossman-genjutsu-jeff-unified-20261010`.
3. Headless-восстановление после reboot: автологон (шаг владельца) → `/reboot` → `/remote_status`.
4. Доступ владельца с телефона из мобильной сети: OWNER_VERIFICATION_PENDING.
5. Локальная модель без fallback и живая Ollama: после перезагрузки GPU.
6. Установка на второй ПК/ноутбук и связь двух устройств: NOT_RUN.
7. Motion V2 оценки ≥6/8 и промо: не подтверждены владельцем.
8. Вечерний тест сборки: обход кнопок интерфейса (`installed_ui_sweep.py`) не уложился в 900 с на ПК владельца (улики `%LOCALAPPDATA%\Bossman\evening\9520a45a…61011T230323Z-22687c`). Воспроизведение: `runtime\python.exe -I app-support\installed_ui_sweep.py --output … --expected-sha 9520a45a…`; отдельный прогон без лимита: **2693 с, 630 кликов, 53 страницы, статус REVIEW_REQUIRED**, 11 замечаний (poker-vision sandbox-контролы 403/400 в пустом приложении, direct-gen 3 невидимых/без ответа элемента, telegram_calls «Проверить аудиоконтур» таймаут) — `docs/evidence/ui-sweep-9520a45a/`. Вывод: сборка не виснет, обход на этом ПК втрое дольше лимита CI; замечания — вход для ночного диалога Bossman→Claude.

## 7. North Star

Уровень не повышается: SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT. Ночной раннер даёт первые
улики автономного цикла (verify → UX → security → диалог с Claude), но transfer/soak
измерений нет; `docs/evidence/night-20261011/` станет первым материалом для
SELF_REPAIR_SINGLE_CYCLE_PASS только если Claude реально исправит дефект и повторный UX-тест
пройдёт.

## 8. Статус и вердикт

- Кандидат `9520a45a610a`: **READY_FOR_OWNER_RUN** — обязательные workflow зелёные на `4c29b274` (кроме Intelligence Preservation, шаг владельца), архив verifier PASS, CI One-download PASS, локальная приёмка PASS; локальный обход интерфейса REVIEW_REQUIRED (11 замечаний) и таймаут 900 с на ПК владельца — открытые пункты, не блокирующие owner run.
- **NOT PRODUCTION_READY / NOT FINAL_FREEZE**: открыты п. 1–4 раздела 6.

## 9. Вклад исполнителей (по проверенным изменениям)

| Исполнитель | Проверенные изменения | Исправленные сбои | Откаты | Расходы |
|---|---|---|---|---|
| Claude (этот прогон) | 3 коммита: пульт remote access + keeper + тесты; ночной раннер; аудит безопасности; улики браузера; документы | STOP-обход, config-ловушка, SMB1 false-PASS, null-процессы, env-утечки | конфиг пульта (бэкап) | Mistral ~€0.02, OpenRouter ≈ $0.001, free-модели $0 |
| Codex | `start_claude_remote.ps1`, quick-access doc, Poker recording_dataset + 16 тестов, `origin/HEAD` → main | — | — | — |
| Бесплатные/облачные модели | 4 ревью (найдено 6 реальных дефектов), 1 vision-проверка | — | — | $0 / ~$0.001 |
| Владелец | решения: AGPL, bypassPermissions, разгрузка ПК, запрет RunPod | — | — | — |
