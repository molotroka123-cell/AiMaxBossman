# Чекпоинт Bossman OpenCode в Веб-Пульте (2026-10-20)

## 1. Сводка чекпоинта
Bossman расширен полноценным контуром **OpenCode Workbench** в Веб-Пульте (`#/coding`) с поддержкой:
1. **Бесплатных облачных моделей (`tier: "free"`, 10 моделей)** и **Платных топовых облачных моделей (`tier: "paid"`, 5 моделей)** через OpenRouter, Groq Cloud, Google AI Studio и NVIDIA NIM, а также локального MoE-кодера (`qwen3-coder:30b`).
2. **Подключения API-ключей прямо из Веб-Пульта** (`POST /api/coding-tasks/connect-key`) с сохранением в зашифрованное хранилище `Vault` и/или `provider-keys.env` без раскрытия секретов в ответах.
3. **Режима самокодинга (`Self-Coding Bossman`)** (`POST /api/coding-tasks/self-target` и `self_target=True` / `source_repo="self"`) с работой в изолированном git-worktree (`IsolatedWorktree`), независимой проверкой `pytest` в песочнице (`_verify_in_sandbox`), выпуском криптографической квитанции `Mission Receipt v1` и применением проверенного патча (`POST /api/coding-tasks/{id}/apply`) после подтверждения владельцем.
4. **Автоматического облачного резервирования (`fallback_workers`)**: при `429 Rate Limit` или `model_error` у основного облачного провайдера задача автоматически переключается на резервную облачную модель и сохраняет полную трассу попыток в `worker_attempts`.

---

## 2. Каталог подключённых облачных моделей (`WORKERS`)

### Бесплатные облачные кодеры (`tier: "free"`)
| ID воркера | Модель | Провайдер | Проверенная задержка tool-call |
|---|---|---|---|
| `poolside-laguna-free` | `poolside/laguna-s-2.1:free` | OpenRouter | **0.92 с** |
| `minimax-m31-free` | `minimax/minimax-m3.1-flash:free` | OpenRouter | **0.91 с** |
| `openrouter-code-free` | `cohere/north-mini-code:free` | OpenRouter | **1.18 с** |
| `nemotron-35-free` | `nvidia/nemotron-3.5-lightning:free` | OpenRouter | **1.93 с** |
| `groq-qwen38-free` | `qwen/qwen3.8-27b` | Groq Cloud | **0.40 с** |
| `groq-gpt-oss-120b-free` | `openai/gpt-oss-120b` | Groq Cloud | **0.40 с** |
| `gemini-3-flash-free` | `gemini-3-flash-preview` | Google AI Studio | **1.02 с** |
| `openrouter-free` | `qwen/qwen3-coder:free` | OpenRouter | резерв через `fallback_workers` |
| `gpt-oss-120b-free` | `openai/gpt-oss-120b:free` | OpenRouter | резерв через `fallback_workers` |
| `nvidia-nim` | `qwen/qwen2.5-coder-32b-instruct` | NVIDIA NIM | активен при наличии кредитов NIM |

### Платные топовые облачные кодеры (`tier: "paid"`)
| ID воркера | Модель | Провайдер | Проверенная задержка tool-call |
|---|---|---|---|
| `deepseek-v4.1-flash` | `deepseek/deepseek-v4.1-flash` | OpenRouter | **0.61 с** |
| `haiku-5.5` | `anthropic/claude-haiku-5.5` | OpenRouter | **1.76 с** |
| `glm-flash` | `z-ai/glm-5.3-flash` | OpenRouter | **7.67 с** |
| `sonnet-4.6` | `anthropic/claude-sonnet-4.6` | OpenRouter | активен при пополнении баланса |
| `gpt-5.4-codex` | `openai/gpt-5.4-codex` | OpenRouter | активен при пополнении баланса |

---

## 3. Ключевые изменения в кодовой базе
- **`bossman-core/bossman/apprentice/local_sidecar.py`**:
  - В `_http()` добавлены заголовки `User-Agent: Bossman-OpenCode/2.6`, `HTTP-Referer: https://bossman.local`, `X-Title: Bossman OpenCode` (устранён блок Cloudflare `HTTP 403 error 1010` при обращениях к `api.groq.com`).
  - В `Model.__init__()` сохранён суффикс `/openai` для совместимости с Google AI Studio (`https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`).
  - В `_BOOT` и `_scratch_env()` добавлено автоматическое подключение `command-center`, `bossman-core` и `bossman-shared` в `sys.path` внутри изолированного `IsolatedWorktree` при самокодинге Bossman.
- **`command-center/bcc/features/coding_tasks.py`**:
  - Добавлены маршруты:
    - `GET /api/coding-tasks/workers` — каталог бесплатных/платных облачных и локальных моделей + статус ключей (`OPENROUTER_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `NVIDIA_API_KEY`).
    - `POST /api/coding-tasks/connect-key` — безопасное подключение API-ключа из Веб-Пульта.
    - `POST /api/coding-tasks/self-target` — нацеливание задачи на собственный репозиторий Bossman и пресеты самокодинга (`SELF_CODING_PRESETS`).
    - `GET /api/coding-tasks/readiness?worker=<id>` — проверка готовности конкретного облачного или локального воркера.
  - Добавлена функция `summarize_opencode_roster()` и автоматический перебор `fallback_workers` при `model_error`.
- **`command-center/ui/pages/coding.js`**:
  - Встроена панель **OpenCode Workbench** (`#opencode-workbench`), кнопка **«Писать код самому себе (Bossman)»** (`#opencode-self-code-btn`), модальное окно подключения ключей (`#opencode-connect-key-btn`), селектор моделей по группам (`Бесплатные облачные` / `Платные топовые` / `Локальный сайдкар`), пошаговый просмотр вызовов инструментов (`sidecar.tool_calls`), просмотр `Mission Receipt v1` и кнопка `Apply`.
- **Тесты и скрипты проверки**:
  - `command-center/tests/test_opencode_web_pult_self_coding.py` (`5 passed`)
  - `command-center/tests/test_coding_workers.py` + `test_coding_tasks_cloud_readiness.py` (`10 passed`)
  - `command-center/tests/test_ux_soak_coding_page_readiness.py` (`1 passed`)
  - `tools/prove_opencode_web_pult_self_coding.py` — E2E-скрипт живого самокодинга бесплатной и платной облачными моделями.

---

## 4. Как продолжить при следующем включении ПК
1. Рабочее дерево `C:\Users\asd\Bossman\wt-rc-20261019` синхронизировано с `origin/main`.
2. Для запуска живого E2E-прогона самокодинга достаточно выполнить:
   ```powershell
   C:\Users\asd\Bossman\venv-tests-20261012\Scripts\python.exe tools/prove_opencode_web_pult_self_coding.py
   ```
3. В Веб-Пульте (`http://127.0.0.1:8801/#/coding`) доступна панель **OpenCode Workbench** с выбором бесплатных и платных облачных моделей и кнопкой **«Писать код самому себе (Bossman)»**.
