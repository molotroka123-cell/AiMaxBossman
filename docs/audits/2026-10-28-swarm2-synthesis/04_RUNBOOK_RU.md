# Операционный Runbook Bossman OS 2.0

**Дата:** 28.10.2026  
**Рабочее дерево релиза:** `C:\Users\asd\Bossman\wt-bossman-20-argon` (ветка `main`)  
**Порты:** Command Center `:8801`, Мобильный пульт `:8840`, War Terminal `:8830`

---

## 1. Проверка здоровья и идентичности сборки (`Health & Source Identity`)

Выполнить в PowerShell:
```powershell
Invoke-RestMethod http://127.0.0.1:8801/health/live | ConvertTo-Json -Depth 5
Invoke-RestMethod http://127.0.0.1:8801/api/v20/status | ConvertTo-Json -Depth 5
Invoke-RestMethod http://127.0.0.1:8840/m/api/health | ConvertTo-Json -Depth 5
```

**Критерий нормы (`PASS`):**
- `status == "ALIVE"`
- `source_identity == "PASS"`
- `git_clean == true`
- `build_sha == head_sha` (совпадает с `git rev-parse HEAD` на ветке `main`)
- В `/api/v20/status`: `security_self_test.passed == 5`, `obsidian_memory.status == "UP_TO_DATE"`.

---

## 2. Чистый перезапуск живого сервера `:8801` на актуальном `main`

Если `/health/live` сообщает `SOURCE_IDENTITY_STALE` или `SOURCE_IDENTITY_UNKNOWN`:
1. Убедиться, что рабочее дерево чистое:
   ```powershell
   git -C C:\Users\asd\Bossman\wt-bossman-20-argon status -sb
   ```
2. Перезапустить процесс `:8801` из чистого дерева `wt-bossman-20-argon\command-center` с сохранением данных владельца `BCC_DATA_DIR=C:\Users\asd\AppData\Local\Bossman\CommandCenter`:
   ```powershell
   $conn = Get-NetTCPConnection -LocalPort 8801 -State Listen -ErrorAction SilentlyContinue
   if ($conn) { Stop-Process -Id $conn.OwningProcess -Force }
   $env:BCC_DATA_DIR = "C:\Users\asd\AppData\Local\Bossman\CommandCenter"
   $env:PYTHONPATH = "C:\Users\asd\Bossman\wt-bossman-20-argon\command-center;C:\Users\asd\Bossman\wt-bossman-20-argon\bossman-core;C:\Users\asd\Bossman\wt-bossman-20-argon"
   Start-Process -FilePath "python" -ArgumentList "-m uvicorn bcc.main:app --host 127.0.0.1 --port 8801" -WorkingDirectory "C:\Users\asd\Bossman\wt-bossman-20-argon\command-center" -WindowStyle Hidden
   ```
3. Повторно проверить `GET http://127.0.0.1:8801/health/live`.

---

## 3. Экстренный глобальный `STOP` и возобновление (`Resume`)

- **Активация `STOP` (доступна и владельцу, и агенту):**
  ```powershell
  $tok = (Get-Content C:\Users\asd\AppData\Local\Bossman\CommandCenter\token -Raw).Trim()
  Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8801/api/pc-agent/stop -Headers @{"X-BCC-Token"=$tok} -ContentType "application/json" -Body '{"reason":"owner_emergency_stop"}'
  ```
  *Постусловие:* создаётся маркер `STOP`, все активные Windows Job Objects дочерних команд уничтожаются вместе с деревом потомков, новые мутирующие шаги получают `HTTP 409 PC_AGENT_STOPPED`.
- **Снятие `STOP` (`Resume` — строго только с токеном `owner`):**
  ```powershell
  Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8801/api/pc-agent/resume -Headers @{"X-BCC-Token"=$tok} -ContentType "application/json" -Body '{"reason":"owner_resume"}'
  ```

---

## 4. Аудит и синхронизация памяти Obsidian 2.0

```powershell
Invoke-RestMethod http://127.0.0.1:8801/api/v20/memory/audit | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post http://127.0.0.1:8801/api/v20/memory/sync | ConvertTo-Json -Depth 5
```
Проверяет хранилище `C:\Users\asd\Bossman\audit-memory`, удаляет синтетические черновики при наличии и обновляет индекс канонических заметок Bossman 2.0.

---

## 5. Запуск проверочного набора тестов (`Harness Approved Suite`)

Всегда указывать `--basetemp` вне корня репозитория и удалять временный каталог после завершения:
```powershell
$env:PYTHONPATH="command-center;bossman-core;."
python -m pytest -q `
  command-center/tests/test_swarm2_coding_fixes.py `
  command-center/tests/test_exec_swarm_regressions.py `
  command-center/tests/test_defensive_security_p1_p2.py `
  command-center/tests/test_bossman_20_superstructure.py `
  command-center/tests/test_spend_firewall_free_gate.py `
  command-center/tests/test_economy_orchestrator.py `
  tests/test_qlora_tournament_integrity.py `
  command-center/tests/test_orch_smoke_guard.py `
  command-center/tests/test_orch_smoke_canon.py `
  command-center/tests/test_site_app_tool.py `
  -p no:cacheprovider --basetemp=C:\Users\asd\Bossman\.pytest-tmp-runbook
Remove-Item -Recurse -Force C:\Users\asd\Bossman\.pytest-tmp-runbook -ErrorAction SilentlyContinue
```
