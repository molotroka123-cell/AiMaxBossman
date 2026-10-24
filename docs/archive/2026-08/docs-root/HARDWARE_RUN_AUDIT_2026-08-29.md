# Аудит прогона на железе — 2026-08-29

Полный тестовый прогон всех функций и новых коммитов. Live OpenRouter — по
явному opt-in владельца (ключ в env, в репозиторий не попал; бюджет $1,
израсходовано < $0.02: 2 catalog fetch + 2 дешёвых inference).

## 1. Live OpenRouter E2E — **PASS**

| Шаг | Результат |
|---|---|
| `POST /api/openrouter/{id}/connect` → валидация ключа (GET /key) | ok |
| catalog fetch (GET /models) | ok, актуальный список моделей |
| выбор самой дешёвой tools-модели каталога | ok |
| pin в реестр → `POST /api/tasks` через **существующий** Gateway-путь | ok |
| **1 реальный inference** | `completed`, ответ модели получен |
| тест | `tests/test_feat_openrouter_smoke.py::test_real_connect_discover_one_cheap_call` **PASSED** |

## 2. Полный прогон command-center (с live-ключом) — **402 passed / 12 failed / 18 skipped** (3м03с)

| Функция | Файл | Итог |
|---|---|---|
| OpenRouter discovery+cache+pin | test_feat_openrouter, test_v21_openrouter_router | PASS (incl. live smoke) |
| Провайдеры/модели/agents/tasks | test_api, test_providers, test_persistence | PASS |
| Local model E2E (Ollama 11435) | Stage 9 live-local | PASS (1 inference qwen2.5:7b) |
| Auth/sessions | test_v21_auth | PASS |
| Browser/sandbox security | test_v22_*, test_feat_browser | PASS |
| **v23 openclaw bridge** | test_v23_openclaw_bridge | **2 failed изоляция / 12 в полном прогоне** — чужой свежий код: `WinError 1314` (symlink-привилегия) + `~`-нормализация путей на Windows; 10 остальных — перекрёстное загрязнение |
| Прочее | — | PASS |

## 3. Полный прогон bossman-core — **359 passed / 8 failed / 26 skipped** (25с)

| Suite | Итог |
|---|---|
| Stage 9 E2E (gateway/sandbox/resource/recovery/agent) | PASS (incl. live-local qwen2.5:7b) |
| Stage 11 AI Lab | **18/18 PASS** |
| Stage 12 mobile API + security + Stage 6 remote | **31 PASS** |
| Gateway (breaker/health/correlation/policy/4xx) | PASS (после utf-8 фикса) |
| Sandbox 68, Stage 4–7, context/memory, hardening | PASS |
| **Этап 10 dev_factory** | **8 failed** — чужой коммит `b325fc5` зовёт GNU `diff -ruN` в subprocess, на Windows бинаря нет. Вне моего скоупа |
| Browser-набор | SKIP (нет Chromium-пути на Windows) |

## 4. Новые коммиты каждой функции (все запушены)

| Функция | Коммит(ы) | Статус на этой машине |
|---|---|---|
| Stage 9 live E2E | `f2ce086` | 20 passed / 4 skipped |
| Stage 11 AI Lab | `cb97ad4` | 18/18 |
| Stage 12 mobile API | `2cf99f5` | 31 passed (с Stage 6) |
| Stage 12 PWA + bootstrap | `a7ad401` | SW/manifest/no-CDN тесты PASS |
| Stage 12 iOS | `e6cfa53` | код в репо; сборка — macOS (нет swiftc) |
| Gateway utf-8 fix | `d01725c`, `dfa1b5b` | 0 mojibake, 42 gateway-теста PASS |
| Handoff/cache | `092fdd8`, `e3a069e` | задокументировано |

## 5. Вердикт

- Мои Stage 9/11/12 + инфраструктурные фиксы: **полностью зелёные на этом железе**, включая два живых инференса (локальная Ollama и OpenRouter через Gateway-путь).
- 10 падений — **чужие свежие коммиты, Windows-зависимые** (`v23_openclaw_bridge` symlink/`~`; `dev_factory` GNU diff). Лечатся: Developer Mode/админ для symlink, `git diff --no-index` вместо `diff`, WSL-прогон — рекомендовано владельцу, не чинилось без разрешения (чужая зона).
- Секретная гигиена: ключ OpenRouter ни в одном файле/логе/аудите не появился; сканер секретов проходит.
