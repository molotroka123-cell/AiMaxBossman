# CONTINUATION — собственный агент Bossman, 13.10.2026

> **Обновление 14.10.2026:** актуальная карта веток и SHA, границы владельца, что доказано и что нет по 2.2 и порядок
> проверки через пульт — в `docs/owner/ARGON_SYNC_20261014_RU.md`, единый экран презентации — в `docs/owner/BOSSMAN_ONE_PRESENTATION_20261014_RU.md`, живое доказательство пульта — в `docs/owner/READY_FOR_PULT_20261014_RU.md`.
> Статус «2.2 закрыт» не ставить, пока живьём не нажата кнопка владельца (`release_main`).

Сначала читать [AGENT_ARCHITECTURE_RU.md](../v2.2-20261013/AGENT_ARCHITECTURE_RU.md),
[выбор моделей](../v2.2-20261013/GENERATION_MODEL_SELECTION_RU.md) и
[мастер-промпт](../v2.2-20261013/MASTER_PROMPT_RU.md). Это активный контракт; датированные
старые handoff сохраняются как история. Перед действием сверить status/branch/HEAD/remote/runtime.

Последнее поручение: изменения дальше выполняет только Argon. Его
[единый план с монетизацией и критериями будущего аудита](../v2.2-20261013/ARGON_IMPLEMENTATION_PLAN_RU.md)
имеет приоритет. Параллельных кодирующих исполнителей не назначать.

## Проверенный source checkpoint

- Каноническая линия: `main`, последний remote `cd71cddc9423d9781dff7eb24ba5b09fb24a990d`;
  продуктовый код соответствует b046b3b3, дельта — три owner checkpoint/prompt документа.
- Занятый checkout: `wt-unified-20261010`, branch `feat/bossman-2-2-20261013`, HEAD до
  этой правки `af92a0fdbe9e231cbb68fcb10dfa095be2ffc6d4`. Не переключать и не сбрасывать.
- Main содержит последующие исправления оператора: read-only ответы, allowlist
  Bossman/Motion API, фактический backend port, таймаут локальных вызовов. Дельту
  брать по Git; установленный ZIP и source runtime проверять отдельно.
- `af92a0fd` фиксирует принятие конституции v2 владельцем. Проверить реальный runtime
  pin/hash; не объявлять прежний «pin отсутствует» текущим фактом без проверки.
- Полное управление Windows из Telegram, MC22-16 и общий final freeze здесь не доказаны.
  Архив `94e40cd1`, старые CI, PID и поколения не являются текущими PASS.

## Что уже переиспользовать

`/work`, `/work_status`, `/work_stop`, `/work_yes N`, `/work_no N` и
`/api/qwen-operator/*` — существующий вход оператора. Проверять фактическую таблицу
команд текущего runtime. Оператор имеет Lab и разрешённые host/API действия; не
описывать его только лабораторным кодером. Полноценный GUI-путь нужно связать с
существующими computer.observe/act, console, remote_access и approvals.
Один backend/storage/модельный каталог, один poller на токен, общие task IDs и STOP.

## Один следующий шаг

Настоящая owner Telegram задача → наблюдение Windows → действия в Блокноте → файл
→ verifier → доставка. Провести STOP/restart/negative controls и сохранить receipts
exact SHA/runtime/model/tool/output hash. Затем MC22-16: отдельный выбор prompt/image/
edit/video, предпочтительный uncensored, snapshot модели в очереди, честная недоступность.

Уже действующие короткие генерационные команды описаны в
[PULT_GEN_WORKFLOW_20261013_RU.md](PULT_GEN_WORKFLOW_20261013_RU.md), референсы — в
[PULT_REFERENCE_WORKFLOW_20261013_RU.md](PULT_REFERENCE_WORKFLOW_20261013_RU.md).
Новая предлагаемая `/genmodel` не объявлена активной. Не запускать личные снимки для smoke.

## Остаток продукта

Все 39 ID сохранены в реестре. Jeff Stars требует своего платёжного acceptance и
корпуса, Motion — реального export/reopen, Operations — dedup/recovery, Reports —
контрольной книги и полноты экспорта. Poker/Trading/Pump.fun/Music/Social Farm/Apps
принимаются отдельно; paper-only защита не снимается. Новое обучение требует baseline,
holdout и transfer, а headless — реального reboot с резервным доступом.

## Рабочие правила

Сохранять чужой dirty/untracked diff; один интегратор, stage только свои paths,
fast-forward push в согласованную ветку, без force/reset/global add. Сначала source/
receipts, потом узкий fix и проверка; не начинать весь аудит или сборку заново.
Не выводить секреты. Не повторять действие с неизвестным внешним исходом.
Уже выданные разрешения сохраняют силу, новые эффекты согласуются по общей политике.
Отменённые часовые аудиты и завершённый монитор готовности не возобновлять.
