# CONTINUE — следующий запуск (owner run 23.09 → дальше)

Тестовая установка и данные: `C:\Users\asd\Bossman Test 0923\` (рабочая установка владельца не тронута).
Окружение всех каналов: `C:\Users\asd\Bossman Test 0923\test-env.cmd` (BCC_DATA_DIR, BCC_PORT=8810, sidecar, spend meter).

## 1. Поднять runtime (Smart App Control блокирует llama.cpp — используем подписанный Ollama)
```powershell
$env:OLLAMA_HOST="127.0.0.1:11435"; $env:OLLAMA_KEEP_ALIVE="-1"; $env:OLLAMA_NO_CLOUD="1"; $env:OLLAMA_FLASH_ATTENTION="1"
Start-Process "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve -WindowStyle Hidden
Start-Process python "`"C:\Users\asd\Bossman Test 0923\runtime-shim\nothink_proxy.py`" 11500" -WindowStyle Hidden
```

## 2. Запустить тестовый Bossman и терминал
- Окно: `C:\Users\asd\Bossman Test 0923\start-test.cmd` (из Проводника = обычный пользователь).
- Терминал владельца: ярлык **«Bossman CMD»** на рабочем столе (или `C:\Users\asd\Bossman\Bossman-CMD.cmd`).
- Claude/скрипты: `"C:\Users\asd\Bossman Test 0923\bm-teacher.cmd" status --json` (одобрения из этого окна запрещены).

## 3. Следующий шаг обучения (одна команда)
D1 на новой модели-кандидате или повтор лестницы на SHA3/SHA4 (скрипт лестницы и скрытые verifier'ы — вне git, `C:\Users\asd\Bossman\exam-sealed-0923`):
```bat
"C:\Users\asd\Bossman Test 0923\bm-teacher.cmd" code "<текст D1 из learning/BENCHMARK_MANIFEST.json>" --repo "C:\Users\asd\Bossman Test 0923\projects\bossman-tools-lab" --allow tools --allow tests --timeout 2400 --output-format stream-json
```
Для честного transfer нужен новый unseen D3 **труднее D2** (большой файл, как D1) — D2 решается без урока.

## 4. Решения владельца (OWNER_ACTION_REQUIRED)
1. Влить `fix/owner-run-20260923-p1` (SHA3 cdb4b09d) и `feat/cli-claude-parity-20260923` через PR с CI exact-SHA (Windows bundle workflow не запускается на integrate/**; нужен push в release/** или workflow_dispatch).
2. Telegram HW-06 на тестовом экземпляре: остановить inbox-поллер Claude (файл `runtime-shim\STOP`), запустить компаньон против :8810 с его токеном.
3. Jev фаза 2 — только после ≥30 шагов на своих задачах.
4. Открытые P1: DOWNLOAD-FALSE-SUCCESS, APP-CONTRACT-OVERRIDES-AGENT-TOOLS, CODING-SNAPSHOT-32MB (см. BUGS.md).

## 5. Остановить всё тестовое
```powershell
New-Item "C:\Users\asd\Bossman Test 0923\runtime-shim\STOP" -ItemType File -Force   # Telegram inbox
# закрыть окно Bossman (backend остановится); затем:
Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'nothink_proxy|cloud_stub' } | % { Stop-Process -Id $_.ProcessId }
$env:OLLAMA_HOST="127.0.0.1:11435"; & "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" stop bossman-main-qwen38-27b-q5:latest
```
