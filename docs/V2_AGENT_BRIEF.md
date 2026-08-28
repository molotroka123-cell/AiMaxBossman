# V2 — общий бриф Fable-агента (читать первым)

Ты — один из 15 feature-агентов BOSSMAN V2. Работаешь ТОЛЬКО в своём worktree
(путь дан в твоём задании), в ветке `agent/NN-…`. Итог — коммиты в этой ветке.

## Обязательное чтение (в своём worktree)

1. `docs/V2_SHARED_CONTRACTS.md` — сущности/статусы/события/права/границы/хуки. Закон.
2. `docs/V2_CURRENT_STATE_AUDIT.md` — что уже есть; не дублируй существующее.
3. Код ядра: `command-center/bcc/{engine,features/__init__,db,permissions}.py` —
   хуки и таблицы уже готовы, твоя фича их ИСПОЛЬЗУЕТ, не переизобретает.
4. Существующий стиль: `bcc/scheduler.py` (backend), `ui/pages.js` строки 1–200
   и `ui/components.js` (UI-хелперы), `tests/conftest.py` (фикстуры, FakeAdapter).

## Что можно менять (контракты §7)

Только: `command-center/bcc/features/<твоя>.py`, `command-center/ui/pages/<твоя>.js`,
`command-center/tests/test_<твоя>.py`, две строки в `ui/pages/index.js`
(импорт + имя в FEATURE_PAGES), свои файлы в `docs/V2_PROOFS/` и
`docs/V2_AGENT_REPORTS/`. ВСЁ остальное — read-only. Нужен новый хук/колонка —
запиши в Integration notes отчёта, лид добавит. Не редактируй `docs/V2_AGENT_BRIEF.md`
и не включай его в коммиты.

## Среда

- Python 3.11, все зависимости уже установлены глобально (fastapi, sqlalchemy,
  aiosqlite, httpx, pytest, pytest-asyncio, psutil, cryptography, playwright).
  Пакет `bcc` резолвится из cwd: **тесты гонять строго так**:
  `cd <worktree>/command-center && python -m pytest -q` (сначала весь набор —
  34 базовых должны остаться зелёными, потом твои).
- Приложение: `cd <worktree>/command-center && BCC_DATA_DIR=/tmp/bcc-NN BCC_PORT=91NN python -m bcc.app`
  (NN — твой номер; токен в консоли и в `/tmp/bcc-NN/token`).
- Твои порты: сервер 91NN, mock-endpoint'ы 92NN и 93NN. Чужие порты не занимать.
- Mock OpenAI-endpoint (для живых прогонов):

```python
# /tmp/mock-NN.py — python /tmp/mock-NN.py (порт из MOCK_PORT)
import json, os, time
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _s(self, o, c=200):
        b = json.dumps(o).encode(); self.send_response(c)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): self._s({"object": "list", "data": [{"id": "mock-model"}]})
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); req = json.loads(self.rfile.read(n) or b"{}")
        time.sleep(float(os.environ.get("MOCK_DELAY", "0")))
        self._s({"choices": [{"index": 0, "finish_reason": "stop",
                              "message": {"role": "assistant", "content": "ok: 42"}}],
                 "usage": {"prompt_tokens": 37, "completion_tokens": 21}})
HTTPServer(("127.0.0.1", int(os.environ.get("MOCK_PORT", "9200"))), H).serve_forever()
```

- Chromium для UI/browser-проверок: playwright python,
  `p.chromium.launch(executable_path="/opt/pw-browsers/chromium")`.
- В юнит/интеграционных тестах сети нет: `FakeAdapter` из conftest
  (подмена `env.svc.registry.adapter_factory`) или httpx.MockTransport.

## Как фича подключается

`bcc/features/<имя>.py` экспортирует
`FEATURE = Feature(name="…", router=APIRouter(), setup=…, tick=…, tick_seconds=…)`.
Router монтируется под `/api` с auth автоматически; в endpoint'ах бери
`svc = request.app.state.svc`. `setup(svc)` — регистрация хуков:
`svc.engine.add_hook("pick_model", fn)` и т.д. События — только kind'ы из
контрактов §3 через `svc.bus.emit`. Статусы — только словарь §2. Ошибки —
`fastapi.HTTPException(status, {"message": …, "hint": …})` не использовать:
подними `bcc.api.ApiError` (импорт `from ..api import ApiError` создаёт цикл —
поэтому в фичах поднимай `HTTPException(status_code=…, detail={"message": …,
"hint": …})`, формат конвертируется общим обработчиком).

UI-страница `ui/pages/<имя>.js`: экспорт `{id, title, icon, nav, render(ctx), onEvent(ev)}`;
хелперы `import {h, …} from '../components.js'`, данные `import {api} from '../api.js'`
→ `api.raw('/api/…', {method, body})`. Иконки — существующие имена из components.js.

## Определение «готово» (жёстко)

1. Backend + UI-страница + тесты; ВЕСЬ pytest зелёный (базовые 34 + твои).
2. **Реальная проверка из твоего задания выполнена тобой на живом сервере**
   (не только юнит-тесты) — с логами/скриншотами.
3. `docs/V2_PROOFS/NN_<FEATURE>.md` — формат §37 мастер-промпта: дата, среда,
   шаги, ожидаемое/фактическое, логи, PASS/FAIL. Скриншоты — в
   `docs/V2_PROOFS/shots/NN-*.png` (git add).
4. `docs/V2_AGENT_REPORTS/NN_<FEATURE>.md` — формат §36; `[x]` только за
   реально выполненное. Ложный `[x]` = отклонение всей работы.
5. Никаких fake-кнопок: нереализованное — disabled + «Недоступно» (§28).
6. Коммиты в своей ветке (русские, содержательные), **без push**, без секретов,
   без временных файлов (mock-скрипты — в /tmp, не в репо). Финальный
   `git status` чистый; данные (`/tmp/bcc-NN`) в репо не попадают.
7. Перед финалом: `pkill -f "bcc-NN"`-подобной очистки не надо, но останови
   свои фоновые процессы (сервер, mock'и).

Лид проверит твою работу сам («Не верь отчётам — проверяй»); нерабочая
функция вернётся тебе на доработку с конкретными FAIL.
