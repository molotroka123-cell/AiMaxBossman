# Единый реестр требований

39 устойчивых ID. Прежние 38 ID сохранены; добавлен MC22-16. Для каждого в соответствующей спецификации есть исходное состояние,
вход, выход, приёмка, отказ/STOP/перезапуск и зависимости. Приоритеты указаны там же.
NOT_RUN в таблице означает отсутствие нового acceptance 2.2, а не отсутствие кода.

Обязательства 2.1 сохраняют блокирующий статус в согласованном объёме. Новые функции
2.2 принимаются поэтапно. P0 — критичность дефекта внутри выбранного режима/пилота;
это не требование включить все опциональные интеграции ради любого частного релиза.
Любое сокращение обязательного объёма требует явного решения владельца и списка
отложенного; перенумерация версии такого решения не заменяет.

| ID | Требование | Полная спецификация | Новая приёмка |
|---|---|---|---|
| A22-01 | . Идентичность и карта доказательств | [AUTONOMY_SPEC_RU.md](AUTONOMY_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| A22-02 | . Один Bossman в CLI, UI и Telegram | [AUTONOMY_SPEC_RU.md](AUTONOMY_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| A22-03 | . Реальный Computer Use на Windows | [AUTONOMY_SPEC_RU.md](AUTONOMY_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| A22-04 | . Разрешения, конституция и выход к провайдерам | [AUTONOMY_SPEC_RU.md](AUTONOMY_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| A22-05 | . Сохранность миссий, восстановление и лимиты | [AUTONOMY_SPEC_RU.md](AUTONOMY_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| A22-06 | . Строгое самоисправление | [AUTONOMY_SPEC_RU.md](AUTONOMY_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| A22-07 | . Перенос навыка и сохранение способностей | [AUTONOMY_SPEC_RU.md](AUTONOMY_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| A22-08 | . Жизненный цикл памяти и уроков | [AUTONOMY_SPEC_RU.md](AUTONOMY_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| A22-09 | . Обучение моделей и продвижение кандидатов | [AUTONOMY_SPEC_RU.md](AUTONOMY_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| A22-10 | . Загрузка сервера и прогоны 24/48 часов | [AUTONOMY_SPEC_RU.md](AUTONOMY_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| A22-11 | . Подключение второго устройства | [AUTONOMY_SPEC_RU.md](AUTONOMY_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| A22-12 | . Оркестрация без Claude/Codex | [AUTONOMY_SPEC_RU.md](AUTONOMY_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-01 | Активировать единый проверенный runtime пульта | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-02 | Постоянные референсы до отмены | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-03 | Подготовка и исправление промпта локальной Qwen | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-04 | Доказать короткую генерацию изображения и измерить задержку | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-05 | Подключить и проверить выбранную видеомодель | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-06 | Сохранить и доказать стадии Genjutsu | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-07 | Применять эффект ко всему клипу из редактора | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-08 | Закончить существующий монтажный workflow Motion | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-09 | Соединить Motion с генеративными преобразованиями | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-10 | Завершить аппаратную приемку Music Creator | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-11 | Голос, биты, дорожки и место для fine-tune | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-12 | Пройти платежный сценарий Jeff Stars | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-13 | Проверить качество ответов Jeff и поддержку | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-14 | Завершить один вертикальный сценарий Social Farm | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-15 | Свести Apps, дерево и доказательства выпуска | [MEDIA_COMMERCIAL_SPEC_RU.md](MEDIA_COMMERCIAL_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| MC22-16 | Выбор моделей генерации и предпочтительный uncensored-профиль | [GENERATION_MODEL_SELECTION_RU.md](GENERATION_MODEL_SELECTION_RU.md) | NOT_RUN для acceptance 2.2 |
| P22-POKER-01 | локальный импорт собственных записей и происхождение данных | [POKER_TRADING_REPORTS_SPEC_RU.md](POKER_TRADING_REPORTS_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| P22-POKER-02 | калибровка Vision, мест и безопасный отказ | [POKER_TRADING_REPORTS_SPEC_RU.md](POKER_TRADING_REPORTS_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| P22-POKER-03 | запись, подготовка выборки, перезапуск и хранение | [POKER_TRADING_REPORTS_SPEC_RU.md](POKER_TRADING_REPORTS_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| P22-POKER-04 | режимы coaching и разбор с подтверждённым solver/GTO-происхождением | [POKER_TRADING_REPORTS_SPEC_RU.md](POKER_TRADING_REPORTS_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| P22-TRADING-05 | paper trading и защита от утечки будущих данных | [POKER_TRADING_REPORTS_SPEC_RU.md](POKER_TRADING_REPORTS_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| P22-PUMP-06 | исследование Pump.fun без реального создания токенов | [POKER_TRADING_REPORTS_SPEC_RU.md](POKER_TRADING_REPORTS_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| P22-TIMESFM-07 | локальный прогноз и независимый backtest | [POKER_TRADING_REPORTS_SPEC_RU.md](POKER_TRADING_REPORTS_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| P22-EXCHANGE-08 | ручной локальный импорт и расчёт месяца с 21-го по 20-е | [POKER_TRADING_REPORTS_SPEC_RU.md](POKER_TRADING_REPORTS_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| P22-REPORTS-09 | точность отчётов, безопасный вывод и восстановление после сбоя | [POKER_TRADING_REPORTS_SPEC_RU.md](POKER_TRADING_REPORTS_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| P22-OPS-10 | очередь Operations, проверка человеком и черновики без отправки | [POKER_TRADING_REPORTS_SPEC_RU.md](POKER_TRADING_REPORTS_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |
| P22-PROVENANCE-11 | лицензии, независимые данные и реестр артефактов | [POKER_TRADING_REPORTS_SPEC_RU.md](POKER_TRADING_REPORTS_SPEC_RU.md) | NOT_RUN для acceptance 2.2 |

## Как не потерять новые запросы

Входящий запрос → ID требования → capability ID → entrypoint → зависимые файлы →
baseline receipt → candidate receipt → независимая проверка → версия принятия.
Для отсутствующей capability сначала добавить запись NOT_RUN с источником запроса;
для старой отсутствующей ссылки — решить restore/archive/remap и сохранить историю.
Ни один узел не удалять из списка молча. Число пунктов не используется как процент
готовности: один STOP/данные/платёжный дефект может блокировать весь соответствующий путь.
