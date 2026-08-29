# SESSION HANDOFF — Bossman Core (этапы 1–10)

> Самодостаточно: продолжай **без** исходного чата. Работай точечным поиском и
> git diff, не перечитывай весь repo.

## CURRENT HEAD
- ветка: `claude/bossman-control-v03-43igbk`
- HEAD: `7d424dc`. Всё запушено в origin.
- В ветке параллельно работают другие воркеры (Stage 9 e2e, `ai_lab`, openrouter,
  Stage 11/12). Перед работой делай fetch+merge: конфликты бывают в общем списке
  подсистем `bossman/api.py` и в `docs/context/WORKLOG.md` (журнал — дописываемый,
  при конфликте сохраняй ОБЕ стороны).

## LATEST TEST RESULTS
`432 passed, 2 skipped` — весь `bossman-core`, БЕЗ переменных окружения
(Chromium ищется сам через `tests/browser_support.py`).

```
cd bossman-core
python -m pytest -q                       # 432 passed, 2 skipped
python -m pytest tests/test_sandbox_*.py -q
python -m pytest tests/test_dev_factory.py -q
```

## ЧТО РАБОТАЕТ
Подсистемы в реестре жизненного цикла (все `critical=False`, ленивая регистрация
в `bossman/api.py`): `resource_brain`, `remote_client`, `search_everything`,
`video_factory`, `sandbox`, `dev_factory` (+ `ai_lab` роутер от другого воркера).

- **Этап 4 Resource Brain** — аренды памяти закрывают OOM-race, единый пул (VRAM
  как претензия, не суммируется).
- **Этап 5 Search** — поверх `context_engine`, второго RAG нет, секреты не
  индексируются, sensitivity-gate на выдаче.
- **Этап 6 Remote Client** — устройства в Postgres, scope на каждом роуте,
  токены хешированы (показ один раз), аварийный lock fail-closed.
- **Этап 7 Video Factory** — возобновляемая, под допуском, ffmpeg только argv,
  дубли не перезаписываются, браузерный провайдер стопается на captcha.
- **Этап 8 Sandbox** — см. ниже, прошёл red-team.
- **Этап 10 Dev Factory** — `bossman/dev_factory/`, петля до ПАТЧА без
  авто-мержа; подробности в `docs/context/STAGE10_STATUS.md`.

## ЭТАП 8 — что закрыто и ЧЕМ доказано
- **OFF значит OFF**: `BOSSMAN_SANDBOX_ENABLED` по умолчанию выключен.
- **Fail closed**: риск и режим задают минимальный `IsolationTier`; недостижимый
  tier → `IsolationUnavailable`, без тихого даунгрейда. OFFLINE без энфорсмента
  рантайма тоже отвергается.
- **Egress**: ALLOWLIST реально энфорсится — CONNECT-прокси (`egress.py`) плюс
  **принудительный барьер** (`netguard.py`): процесс идёт под выделенным uid, а
  nftables по `meta skuid` режет весь трафик кроме прокси. Проверено живой
  пробой: прямой сокет → BLOCKED, через прокси → CONNECTED. Правила сносятся
  вместе с песочницей.
- **Red-team пройден** (18 проб). Найдены и закрыты ТРИ дыры — регрессы в
  `tests/test_sandbox_redteam_findings.py`:
  - хардлинк проходил ArtifactGate (эксфильтрация любого файла хоста);
  - песочница шла под uid ядра = root (сброс прав был привязан к наличию прокси);
  - секрет с дефисами внутри токена и поле `key` проходили редакцию.

## ЧТО НЕ СДЕЛАНО (упирается в железо/следующий заход)
- **runsc / MicroVM не проверены «в железе»**: на хосте нет `runsc` и нет
  `/dev/kvm` (проверено). Адаптеры написаны и честно определяют возможности,
  протестирован только путь ОТКАЗА. Нужен хост с runsc/KVM.
- **Toolbox ВНУТРИ песочницы** (shell/git/files/browser как её собственные
  инструменты) — не начат. Браузер там обязан использовать отдельный профиль.
- **Dev Factory**: реальный планировщик на модели не подключён (есть контракт
  `Planner` + `FakePlanner`); `executor.edit()` — шов под модель, сам ничего не
  пишет, чтобы пустой прогон не выдавал себя за работу.
- Вне этапов: `context_engine` делает O(N)-скан векторов (P2, масштаб).

## ГРАНИЦЫ БЕЗОПАСНОСТИ (НЕ ослаблять)
1. OFF=OFF. 2. Сеть по умолчанию OFFLINE. 3. Никакого host docker.sock.
4. Никаких сырых прод-секретов в песочнице — только брокер. 5. Fail closed на
недостижимой изоляции. 6. Прод-ФС не writable-монтируется. 7. Лимиты через
Resource Brain. 8. Approvals ВЫШЕ песочницы и выше Dev Factory. 9. Прод
браузер-профиль не переиспользуется. 10. Прод-эндпоинты private-first.
11. Сырые логи → обучение запрещено (dataset gate). 12. Память из песочницы —
только кандидат. Полный список: `_staging/s8/NON_NEGOTIABLES.md`.

**Важное правило работы:** не объявляй security-фикс закрытым без повторной
атаки на новый HEAD. Зелёный тест ≠ закрытая дыра (см. FAIL-001 в FAILURES.md).

## СЛЕДУЮЩИЕ ШАГИ
`docs/context/NEXT.md` — пронумерованный исполняемый список.
Решения — `DECISIONS.md`, провалы — `FAILURES.md`, журнал — `WORKLOG.md`.
