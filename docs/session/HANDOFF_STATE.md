# HANDOFF_STATE — Coinwise Dashboard observation (READ_ONLY)

CURRENT_SHA: eb5a5ed18673eb43d5dc8316459b1b820a511565 (origin/claude/bossman-control-v03-43igbk)

## Замысел

Локальная модель обязана уметь ПРОЧИТАТЬ дашборд Coinwise, который владелец
открыл сам, и превратить увиденное в типизированное свидетельство. Не торговать.
Не входить в аккаунт. Не обходить ограничения. Только наблюдать и честно
сказать, чего не видно.

## Что переиспользуется, а не пишется заново

| Нужное по заданию | Что уже есть | Файл |
|---|---|---|
| TRADING_EXECUTION=OFF, PAPER_TRADING_ONLY, OWNER_APPROVAL_REQUIRED | константы + `assert_no_live_execution` | `trading_learning/safety.py` |
| одобрение владельца, не самим агентом | `OwnerApproval`, `require_owner_approval` | `trading_learning/safety.py` |
| недоверенный текст (чат, реклама, субтитры, оверлеи, OCR) | `sanitize`, `as_untrusted_block` | `trading_learning/sanitize.py` |
| честный BLOCKED вместо подделки возможности | `Capability`, `AdapterResult`, `probe_ocr` | `trading_learning/adapters.py` |
| Working State / Episodic / Procedural + гейт повышения | `TradingMemory`, `promote()` | `trading_learning/memory.py` |
| класс доказательности исполнения | `EvidenceClass` | `trading_learning/safety.py` |
| хеш кадра, дедупликация | `FrameRef`, `_dhash` | `trading_learning/frames.py` |

Новое здесь — только то, чего нет: схема наблюдения дашборда, извлечение
DOM/OCR, классификатор режима рынка в SHADOW.

## Границы патча

- READ_ONLY. Пути записи наружу в модуле нет физически.
- Никаких ордеров, ключей на запись, переводов, сообщений.
- Облачное зрение выключено по умолчанию; скриншоты наружу не уходят.
- Скриншот и мок НИКОГДА не дают LIVE.
- Наблюдение попадает в Working State и Episodic Memory. В Procedural — только
  через существующий гейт повышения, автоматически — никогда.

## Файлы

(заполняется по мере коммитов)

## Откат

Каждый блок — отдельный коммит поверх eb5a5ed. Откат: `git revert <sha>`
или `git reset --hard eb5a5ed` до пуша следующего блока.
