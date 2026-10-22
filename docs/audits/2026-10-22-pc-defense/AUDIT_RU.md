# Независимый аудит киберзащиты ПК владельца и доработка 2 линий обороны Bossman (22.10.2026)

- **Ветка**: `integrate/north-star-final-20261023` (включая `origin/integrate/claude-north-star-20261022` @ `7c876d39`)
- **Мастер-промт**: [`docs/owner/PC_DEFENSE_AUDIT_MASTER_PROMPT_20261022_RU.md`](file:///C:/Users/asd/Bossman/wt-rc-20261019/docs/owner/PC_DEFENSE_AUDIT_MASTER_PROMPT_20261022_RU.md)
- **Соблюдение правил владельца (§0)**:
  1. Никакие локальные переписки, чаты, фото или значения секретов не читались и не выводились (правило владельца от 21.10).
  2. На живом ПК задачи планировщика и файлы ключей не удалялись и не отключались без явного «да» владельца (подготовлен обратимый скрипт с XML-бэкапом).
  3. Worktree `wt-checkpoint-20261015` и живые процессы не затрагивались.

---

## 1. Проверка 9 утверждений Claude (§1 мастер-промта)

| # | Утверждение | Статус | Доказательство / Факты проверки |
|---|---|---|---|
| **1** | LiteLLM на ПК не установлен | **`VERIFIED`** | Проверено через `python -m pip show litellm` (код возврата `1`, `NOT_INSTALLED` в `C:\Users\asd\AppData\Local\Programs\Python\Python312\python.exe`) и поиском по всем `*requirements*.txt` и `pyproject.toml` репозитория (`0` вхождений). |
| **2** | Ключи провайдеров хранятся под DPAPI, в открытом виде ключей нет | **`WRONG`** | Файлы `C:\Users\asd\AppData\Local\Bossman\keys\provider-keys.dpapi` (`1062` Б) и `C:\Users\asd\AppData\Local\Bossman\CommandCenter\keys\provider-keys.dpapi` (`870` Б) действительно зашифрованы (`DPAPI_BLOB`, сигнатура `01 00 00 00 d0 8c 9d df`). Однако в `%LOCALAPPDATA%\Bossman` обнаружены **7 открытых (`PLAINTEXT`) файлов** (проверены только имена, размеры и первые байты сигнатуры без чтения значений):<br>1. `keys\provider-keys.env` (`782` Б, `PLAINTEXT`)<br>2. `secrets\free-provider-keys.txt` (`717` Б, `PLAINTEXT`)<br>3. `secrets\openrouter-rc19-test.env` (`169` Б, `PLAINTEXT`)<br>4. `secrets\openrouter-test.env` (`214` Б, `PLAINTEXT`)<br>5. `secrets\provider-pool.env` (`626` Б, `PLAINTEXT`)<br>6. `telegram-companion\companion.env` (`138` Б, `PLAINTEXT`)<br>7. `keys\moved-20261011\runpod.env` (`66` Б, `PLAINTEXT`) |
| **3** | `tools/ci_secret_scan.py` ловит ключи перед push | **`VERIFIED`** *(доработано для CLI-файлов)* | На чистом дереве git возвращает `Secret-pattern scan: PASS` (`rc=0`). До доработки `main()` игнорировал позиционные аргументы `sys.argv[1:]`; теперь `python tools/ci_secret_scan.py <file>` проверяет переданные файлы напрямую и ловит `sk-...`, `ghp_...`, `RAILWAY_API_TOKEN`, `ZEABUR_API_KEY`, `LITELLM_MASTER_KEY` и URI баз данных с паролями (`rc=2`, доказано в `test_line2_ci_secret_scan_catches_synthetic_keys_and_db_uris`). |
| **4** | Терминал Bossman запрещает `rm -rf /`, форматирование, доступ к `.ssh`/`.env`, ClickFix | **`VERIFIED`** | В `command-center/bcc/features/tools_terminal.py` (`HARD_DENY`) и `command-center/bcc/v2/terminal_control.py` (`DANGEROUS`, `UNRECOVERABLE_TARGETS`) блокируются `rm -rf /`, `mkfs`/`fdisk`/`diskpart`/`format`, `/etc/shadow`, `id_rsa`, `.ssh/id_`, `.aws/credentials`, `.env`, `wallet`/`seed_phrase` и отключение защиты ОС. Дополнительно закрыт класс наступательных сетевых утилит (`60 passed` в `test_pc_defense_two_lines.py`). |
| **5** | В AUTO-режиме не автоодобряются опасные классы (`_NEVER_AUTO_APPROVE_KINDS`) | **`VERIFIED`** *(расширено)* | В `command-center/bcc/features/agent_opencode.py` множество `_NEVER_AUTO_APPROVE_KINDS` расширено до `{"self_improve", "skill_permissions", "admin", "destructive", "outbound_send", "network_send", "cloud_send", "cloud.ask"}`. В `test_line1_bypass_permissions_never_auto_approves_outbound_or_cloud` доказано, что при `bypass_permissions=True` одобрение остаётся в статусе `approval.required` (`pending`). |
| **6** | `web.fetch` не ходит на 127.0.0.1, metadata-хосты, `.internal` | **`VERIFIED`** | В `command-center/bcc/plugin_security.py` функции `validate_url` и `resolve_pinned_ip` блокируют `127.0.0.1`, `localhost`, `169.254.169.254`, `metadata.google.internal`, `*.internal`, IPv6-литералы `[::1]`, `[fe80::1]`, `[fc00::1]`, `[::ffff:127.0.0.1]` и DNS-rebinding со смешанными A/AAAA-записями. Доказано тестами в `test_pc_defense_two_lines.py`. |
| **7** | Bossman Mobile виден только внутри Tailscale, funnel выключен | **`VERIFIED`** | `tailscale funnel status` и `tailscale serve status` показывают `http://win-iqprhtsajl9.tail0f845b.ts.net (tailnet only) -> http://127.0.0.1:8840`. Порты `8801`, `8840`, `8830`, `11434` слушают строго на `127.0.0.1`. |
| **8** | Аудит `tools/pc_defense_audit.py` даёт YELLOW: 27 задач планировщика с PowerShell из `C:\temp` | **`VERIFIED`** *(уточнено: 39 задач)* | Базовый отчёт `docs/north-star/evidence/pc_defense_audit_20261022.json` имел статус `YELLOW` и 27 задач с обратным слэшем `\temp\`. При проверке всех активных не-Microsoft задач обнаружено ещё **12 задач** (`\LunaArg`, `\LunaMenu*`, `\LunaOpenPost`, `\LunaTgLaunch`, `\LunaTgWin*`, `\LunaUia*`, `\LunaWins`), использующих прямой слэш `C:/Temp/...`. Все **39 задач** созданы пользователем `WIN-IQPRHTSAJL9\asd` (старые скрипты автоматизации Luna), все 39 файлов `.ps1` физически лежат в `C:\Temp` / `C:\Windows\Temp`. В `tools/pc_defense_audit.py` добавлена проверка `/temp/` и `c:/temp`, теперь аудит видит все 39 задач. |
| **9** | `cloud.ask` всегда ASK и не утекает контекст чата | **`VERIFIED`** | Проверено прогоном `command-center/tests/test_agent_extras_cloud_ask.py` (`37 passed`) и тестом `_bus_bridge` в `test_pc_defense_two_lines.py`: `cloud.ask` имеет `default_effect="ask"`, `hook_is_floor=True`, режет любые маркеры истории чата/вложений/путей медиа (`CLOUD_ASK_CONTEXT_LEAK`, `CLOUD_ASK_MEDIA_PATH`), редактирует секреты и никогда не автоодобряется в `AUTO`. |

---

## 2. Закрытые пробелы (§2 мастер-промта — «А после аудита сразу Допиливай»)

1. **Запрет наступательных сетевых команд и эксплойтов в терминале (`ЗАКРЫТО`)**:
   - В [`command-center/bcc/features/tools_terminal.py`](file:///C:/Users/asd/Bossman/wt-rc-20261019/command-center/bcc/features/tools_terminal.py#L58-L63) (`HARD_DENY`) и [`command-center/bcc/v2/terminal_control.py`](file:///C:/Users/asd/Bossman/wt-rc-20261019/command-center/bcc/v2/terminal_control.py#L22-L27) (`DANGEROUS`) добавлен безусловный запрет (`deny` во всех режимах, включая `system_admin`):
     `nmap`, `masscan`, `zmap`, `sqlmap`, `hydra`, `medusa`, `ncrack`, `john`, `hashcat`, `metasploit`, `msfconsole`, `msfvenom`, `nuclei`, `nikto`, `gobuster`, `ffuf`, `dirbuster`, `mimikatz`, `responder`, `impacket*`, `crackmapexec`, `netexec`, `bloodhound`, `chisel`, `ligolo(-ng)`.
   - Обычный `curl` к локальному API и команды разработки (`git status`, `pytest`) проходят штатно.
2. **Запрет автоодобрения сетевой и облачной отправки в AUTO (`ЗАКРЫТО`)**:
   - В [`command-center/bcc/features/agent_opencode.py`](file:///C:/Users/asd/Bossman/wt-rc-20261019/command-center/bcc/features/agent_opencode.py#L2556-L2565) в `_NEVER_AUTO_APPROVE_KINDS` добавлены `"network_send"` и `"cloud_send"` (в дополнение к `"outbound_send"` и `"cloud.ask"`).
3. **Скрипт бэкапа и отключения 39 устаревших задач планировщика из `C:\Temp` (`ПОДГОТОВЛЕНО, ЖДЁТ «ДА» ВЛАДЕЛЬЦА`)**:
   - Создан [`tools/disable_temp_scheduled_tasks.ps1`](file:///C:/Users/asd/Bossman/wt-rc-20261019/tools/disable_temp_scheduled_tasks.ps1) с тремя режимами: `DryRun`, `Disable` (экспортирует XML каждой из 39 задач в `%LOCALAPPDATA%\Bossman\backups\scheduled-tasks-20261022\` и отключает их) и `Restore` (откат одной командой).
4. **Проверка `OutboundGuard` для Jeff и Пульта (`ЗАКРЫТО`)**:
   - В [`command-center/tests/test_pc_defense_two_lines.py`](file:///C:/Users/asd/Bossman/wt-rc-20261019/command-center/tests/test_pc_defense_two_lines.py#L234-L280) проверены все **6 из 6** защитных сценариев `OutboundGuard`: выключенный глобальный тумблер (`global_switch_off`), отсутствие согласия получателя (`not_opted_in`), суточный лимит 1 сообщение / 24 ч (`rate_24h`), предохранитель веерной рассылки на 3 чата за 10 мин (`fanout_breaker`), блокировка всех последующих чатов после срабатывания (`broadcast_tripped`) и пустой `chat_id` (`chat_unknown`), плюс положительный тест прямого ответа в окне 30 минут (`reply_window`).

---

## 3. Навыки в памяти Bossman (`.agents/skills/`)

Созданы, подписаны (`provenance.json` со схемой `bossman.skill_provenance.v1` и статусом `VERIFIED`) и покрыты тестами 3 навыка:
1. [`pc-defense-audit`](file:///C:/Users/asd/Bossman/wt-rc-20261019/.agents/skills/pc-defense-audit/SKILL.md) — регулярный read-only аудит 2 линий обороны ПК (`tools/pc_defense_audit.py --json`), сверка с базовым отчётом и уведомление в Пульт при новых `HIGH`.
2. [`secret-hygiene`](file:///C:/Users/asd/Bossman/wt-rc-20261019/.agents/skills/secret-hygiene/SKILL.md) — обязательный `ci_secret_scan.py` перед `push` и ежедневная проверка сигнатур файлов ключей (`DPAPI_BLOB` vs `PLAINTEXT`) без чтения значений.
3. [`incident-response-local`](file:///C:/Users/asd/Bossman/wt-rc-20261019/.agents/skills/incident-response-local/SKILL.md) — локальный сбор фактов при `HIGH`/`MEDIUM`-срабатываниях и подготовка обратимого плана с бэкапом, исполняемого только после явного «да» владельца.

---

## 4. Результаты тестов

- `pytest -q command-center/tests/test_pc_defense_two_lines.py`: **`60 passed in 1.44s`**
- `pytest -q command-center/tests/test_agent_extras_cloud_ask.py`: **`37 passed`**

---

## 5. Действия на живом ПК, требующие явного «да» владельца

1. **Отключить 39 устаревших задач планировщика Luna из `C:\Temp` с предварительным XML-бэкапом**:
   - Команда запуска (после «да»):
     `powershell -NoProfile -ExecutionPolicy Bypass -File tools\disable_temp_scheduled_tasks.ps1 -Action Disable`
   - Команда отката (если потребуется вернуть):
     `powershell -NoProfile -ExecutionPolicy Bypass -File tools\disable_temp_scheduled_tasks.ps1 -Action Restore`
2. **Убрать 7 открытых (`PLAINTEXT`) `.env`/`.txt` файлов в `%LOCALAPPDATA%\Bossman\`**:
   - Ключи из `keys\provider-keys.env` уже импортированы в `keys\provider-keys.dpapi` (`DPAPI_BLOB`). После вашего «да» перенесём тестовые/дублирующие `.env` в зашифрованный DPAPI-контейнер и удалим открытые текстовые копии с диска.
