# K1m6a: фактический воркфлоу прогона 08.10.2026 (ПК владельца)

Так реально прошла первая десятка после #25. Повторять следующие десятки ровно так. Один Bossman (backend :8801), Ollama :11434.

## 0. Точка и материал
- Остановка была #25 `8wItpyVUi2k`. Очередь `%LOCALAPPDATA%\Bossman\CommandCenter\learning\k1m6a-youtube\queue.json` (канал @K1m6a, от новых к старым).
- Если в очереди меньше 10 после точки: `pin` даёт `BATCH_INCOMPLETE`; копия очереди дополняется следующими роликами канала (`yt-dlp --flat-playlist`), оригинал не меняется.
- Конец обучения: ролик 22 июня `BNSfWoqsBmw` (№61 в списке канала).

## 1. Зафиксировать ДО работы
`k1m6a_exam pin` → `split` (6/2/2 целыми роликами, test самые поздние) → `criteria` (хэш).

## 2. Материал локально
- `yt-dlp` 720p + субтитры в `raw\<id>\video.mp4` (403 — повторить один ролик).
- ASR: faster-whisper small, CPU int8, 2 воркера (~9 мин на 10 роликов, 264 мин аудио).
- Кадры: по опорным фразам речи (уровни, лонг/шорт, VWAP, value area…), шаг ≥45 с, ≤20 на ролик → `smart_frames.json`.
- `situations` (кадр + речь строго до момента).

## 3. Эталон ДО ответов
Эталон по locked test (детерминированно каждая 3-я ситуация, ≥10), поля `symbol/exchange/timeframe/last_close` из строки OHLC, сценарии и `author_next` по речи после момента → `seal`.

## 4. Ответы ученика
Код запуска: worktree `Bossman\k1m6a-run-20261008` (main + PR #101 #103 #105).
```
python -m bossman.trading_learning.k1m6a_exam ask ... --model bossman-fast-qwen36-vision:latest --reasoning-effort none --resume
```
- Без `--reasoning-effort none` vision-модель отдаёт пустой content (всё в reasoning) → BAD_JSON.
- Ответы пишутся построчно; после сбоя тот же запуск с `--resume`.
- Один тяжёлый GPU-процесс. Во время прогона Jeff отвечает медленнее (до 43 с), ответы не теряются.

## 5. Учёба → LESSONS → перезапуск → вердикт
- BASELINE 08.10: 192/192 OK.
- LESSONS требует `/api/memory` (память Bossman). В установленной сборке 6de18f8d её нет (404) → BLOCKED до установки линии PR #99.
- Затем RESTART_TRANSFER и `aggregate`. Вердикт только из `aggregate`.

## 6. Отчёты
Каждый чекпоинт в Пульт: `python -m bcc.telegram_companion.owner_report --file <md> --send --html --pin` (BCC_DATA_DIR=%LOCALAPPDATA%\Bossman\CommandCenter), с блоком экономии (облачная цена локальной работы против реальных трат).
