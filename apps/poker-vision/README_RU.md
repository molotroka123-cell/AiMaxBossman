# Poker Vision (Poker Train × Vision)

Конвейер: **экран → область стола → поля (карты/текст/элементы) → сверка по кадрам → проверенное состояние → история → тренировка/разбор**.
Для каждого поля хранятся значение, `confidence`, время кадра и источник; **UNKNOWN — нормальный ответ**. Скрытые карты не выдумываются.

* Это отдельное приложение (`apps/poker-vision`), не второй покерный движок: правила и equity берутся из существующего `apps/poker-botlab`
  (только чтение), PokerKit используется как независимый «оракул правил» и для PHH.
* Граница BotLab (нет захвата экрана и ввода ОС) не менялась. Граница Vision: `docs/OPERATING_BOUNDARY.md`.

## Что внутри

| модуль | роль |
|---|---|
| `pokervision/adapters/poker_train.py` | адаптер №1 — свой Poker Train: якорь по картам героя (масштаб DPI), зоны в CSS-единицах, калибровка из размеченных кадров |
| `pokervision/adapters/roi.py`, `ton_poker.py` | адаптер №2 — TON Poker: **только наблюдение/replay**, по умолчанию всё UNKNOWN до калибровки и проверки на отложенных кадрах |
| `pokervision/vision/*` | поиск карт (контуры OpenCV), чтение ранга/масти и цифр (exemplar k-NN с порогом отказа), поиск строк текста |
| `pokervision/validate.py` | дубли карт, формат чисел/валюта, улицы, переходы; при ошибке поле становится UNKNOWN, а не «исправляется» |
| `pokervision/reconcile.py` | временная сверка (2 согласных кадра), границы раздач (не склеивает), устаревший/замёрзший кадр, пропуски |
| `pokervision/history.py` | наблюдённые события, `complete=false` + список ненаблюдаемого, PHH-подобный вывод |
| `pokervision/service.py`, `api.py` | **один backend** для UX и CMD (HTTP, loopback) |
| `pokervision/actuator.py` | клики только в своём тренажёре (loopback), со всеми гейтами и STOP |
| `pokervision/strategy.py` | стратегия видит только `VisibleInfo` (нет скрытого состояния) |
| `pokervision/model_registry.py` | версии профилей, отдельный holdout, сравнение с baseline, откат |
| `pokervision/eval/*` | оценка: сплиты по сессиям, baseline→after на одних кадрах, p50/p95, PokerKit, стратегии с CI, генерик-OCR |

## Запуск

```bash
pip install -e apps/poker-vision[live,test]          # numpy, opencv-headless, fastapi; playwright для живого тренажёра
python -m pokervision serve                           # backend на 127.0.0.1:8931 (в Bossman: «Приложения» → Poker Vision, или кнопка на странице)
python -m pokervision replay apps/poker-vision/evidence/sample_frames
python -m pokervision state ; python -m pokervision history ; python -m pokervision stop
```

В Bossman: страница **Poker Vision** (`#/poker-vision`): выбор источника/окна, replay/live, наложение полей, история,
«почему не знаю», калибровка новой раскладки, **STOP**. Страница и CLI ходят в один и тот же сервис.

## Воспроизведение оценки

См. `evidence/REPORT.md` (команды в конце).
