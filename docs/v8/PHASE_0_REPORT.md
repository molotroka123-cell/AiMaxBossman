# Фаза 0 — результат, 18.09.2026

База `d1b96687f02b6c17f829c671cfe4a4987d00c489`, ветка
`claude/bossman-final-convergence-hu2702`. Коммит с этим отчётом — checkpoint
фазы 0, не готовая Studio и не Windows freeze.

- Каталог: 6 записей (5 моделей/семейств + 1 placeholder Higgsfield), verified 0,
  unverified 6. Все выключены, цена null, VERIFIED не задаётся из файла.
  ComfyUI `*` — настроенный владельцем checkpoint, не список установленных весов.
  Облачные настройки ограничены наблюдавшимися значениями из скриптов;
  отсутствующие настройки не домысливаются как полный API-контракт.
- Схемы отвергают неизвестные поля, неверные типы, NaN, выход за диапазон,
  дубликаты id, недопустимые defaults, включение и ложную верификацию.
- Протокол submit/status/cancel/fetch; 401, 429, 402, 404, nsfw, malformed
  различимы. `insufficient_credit` проецируется на старый health `throttled`,
  но оригинальная причина сохраняется и требует владельца. `completed`
  провайдера всегда остаётся наблюдением, не уликой и не PASS.
- ComfyUI wrapper использует существующие workflow/client/ImageStorage и
  повторную проверку PNG, размеров, sha256. Нет фонового worker или сети при
  импорте. Отмена локальная, `cancel_ref=None`: вычисление ComfyUI может
  продолжаться; глобальный interrupt не отправляется. Получение результата
  отменённой заявки отвергается. Состояние адаптера в памяти — durable lifecycle
  принадлежит очереди следующей фазы, здесь оно не обещается.
- Тесты написаны до реализации: ошибки отсутствующих модулей сохранены в
  `evidence/*-red.txt`, дополнительные негативные сценарии тоже сначала падали.
- Контракт поправлен точечно: старое объединение 402 и 429 противоречило
  ASTER_START_HERE_RU.md; REST-пути из стороннего клиента убраны из устава.
  Новых зависимостей нет. Продуктовые UI, API, БД и очереди не изменены.

## Команды и фактические результаты

В этой среде `python` для проверок — `/tmp/bossman-v8-venv/bin/python`.

| Команда | Результат |
|---|---|
| `python -m pytest tests/test_studio_catalog.py -q --timeout=120` | 18 passed |
| `python -m pytest -c command-center/pyproject.toml command-center/tests/test_studio_provider_states.py -q --timeout=180` | 34 passed |
| Те же provider tests + `test_oss_comfyui.py` + `test_feat_images.py` | 74 passed |
| `python -m pytest tests -q --timeout=120` | 1783 passed, 11 skipped, 1 failed |
| `python tools/skips_registry.py --check` | PASS, 235 entries, 0 without_reason |
| `python command-center/bcc/studio/catalog.py --check` | BOSSMAN_STUDIO_CATALOG=6 verified=0 unverified=6 |

Корневой отказ: `test_operator_step_profile.py::test_the_framework_adds_a_bounded_amount_on_top_of_the_declared_costs`.
Он повторён отдельно и на чистом экспорте исходного Git SHA без файлов V8:
оба раза 1 failed / 7 passed. У исходного SHA floors=139.999 при пределе 60;
новый код не импортируется этой проверкой. Порог и тест не изменены.
Полный набор **не объявляется зелёным**. Новых skip нет.

Замеры и метод: [METRICS_BASELINE.md](METRICS_BASELINE.md).
Обзор аналогов: [COMPETITOR_CHECK_20260918.md](COMPETITOR_CHECK_20260918.md).

## Что осталось

Higgsfield MCP: NOT_RUN (официальная полоса не исполнялась здесь).
REST: OWNER_REQUIRED — официальный контракт не предоставлен, адаптер не написан.
Credits=0/free — историческое наблюдение 17.09 из точки входа, не свежая проверка.
Живая генерация Higgsfield/OpenRouter/ComfyUI: OWNER_REQUIRED — в этой работе
проверялись заглушки; ключи, кредиты, локальный checkpoint не использовались.
Windows installed acceptance и тест на ПК владельца: NOT_RUN / OWNER_REQUIRED.

Фаза 1 — очередь, галерея, UI и durable provenance — ещё не сделана.
Фазы 2–5, облако, MCP-приёмка, интеграции и post-processing — ещё не сделаны.
Общий реестр installed acceptance в фазе 0 не расширен: это checkpoint без
продуктовой интеграции и без Windows-поставки каталога. В фазе 1 реестр и фильтр
workflow должны обновляться вместе с владельцем файлов Windows packaging;
его текущие файлы не затронуты. Один только этот отчёт не даёт STUDIO_VERIFIED.
