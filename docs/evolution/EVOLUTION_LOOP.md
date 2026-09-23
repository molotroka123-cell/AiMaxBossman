# Bossman 1.1 — цикл улучшения и завтрашний прогон

Статус на `98667ada`: **инфраструктура есть; owner-run не пройден**. Отдельный CLI
`bossman_evolve.py` теперь входит в Windows ZIP как `app-support/bossman_evolve.py`
вместе с конфигурацией. Он вызывает тот же `bossman_v3.self_improvement.loop`,
которым должен управлять будущий `/api/evolution/*`: второго движка нет.

## Реальный маршрут

`OBSERVE → SELECT → ATTEMPT → VERIFY → ACCEPT/REJECT → LEARN → CHECKPOINT → NEXT`.
Модель работает через существующий `bossman_coding` и его Command Center coding
path. Независимый verifier повторяет тесты, запускает отрицательный контроль и
проверяет новый regression; текст «я исправил» от модели не является verdict.
Принятый результат остаётся **кандидатом** в `candidates.git` кампании и не
подменяет установленный Bossman. `MOCK_MODEL` показывает только связность,
`student_verified_passes=0` для него по определению.

Кампания хранит `loop-state.json`, `loop-report.json`, sealed evidence и
`STOP`/`PAUSE` во внешней рабочей папке. Lease не позволяет двум циклам
исполняться в одной папке. После аварии опасные фазы ATTEMPT/ACCEPT
становятся `UNKNOWN_OUTCOME`; автоматического повтора нет. Для осознанного
повтора владелец проверяет доказательства и явно задаёт `--redo CYCLE_ID`.

## Проверяемые команды

Из checkout (для собственного исходного репозитория под контролем владельца):

```text
python tools/bossman_evolve.py loop --repo C:\Bossman\AiMaxBossman --suite C:\Bossman\AiMaxBossman\config\evolution\owner-v1.1.json --work C:\Bossman\evolution-campaign --backend bossman_coding --api-url http://127.0.0.1:8800 --data-dir %LOCALAPPDATA%\Bossman\CommandCenter --max-cycles 3 --attempt-minutes 40 --total-hours 8 --max-usd 2
python tools/bossman_evolve.py status --work C:\Bossman\evolution-campaign
python tools/bossman_evolve.py pause --work C:\Bossman\evolution-campaign
python tools/bossman_evolve.py resume --work C:\Bossman\evolution-campaign --no-start
python tools/bossman_evolve.py stop --work C:\Bossman\evolution-campaign
python tools/bossman_evolve.py report --work C:\Bossman\evolution-campaign
```

Из распакованного Windows ZIP заменить `python tools/bossman_evolve.py` на
`runtime\python.exe app-support\bossman_evolve.py`. `--repo` задаётся явно:
эксперимент по исправлению исходного кода требует доступного исходного Git
репозитория, хотя обычная установленная программа работает без checkout.
Кампания хранится отдельно от `--repo` и не имеет права писать в stable.
Перед стартом убедиться, что `--suite` относится к данному checkout и что
Command Center работает с проверенным coding sidecar.

`pause` запрещает новый цикл, `stop` отменяет текущие дочерние процессы.
`resume --no-start` только очищает управляющие файлы; `resume` без этого
параметра продолжает цикл. Все действия относятся к **одному** `--work`.
Директории с фото, личными документами и токенами в эксперимент не добавлять.

## Что ещё не принято

- Черновик сквозного `gate.py` не влит: в этом Linux окружении mock gate
  остановился на запуске Command Center (`uvicorn` отсутствует), `psutil`
  тоже отсутствует. Нельзя выдавать этот запуск за PASS.
- `/api/evolution/*` и Telegram `/evolution_*` ещё не подключены. Терминал
  `bossman repair --self` пока честно сообщает, что API недоступно; прямой
  `bossman_evolve.py loop` является ограниченным инженерным входом, а не
  доказательством parity между CLI, UI и Telegram.
- Плохой patch + три самостоятельных цикла с настоящей локальной моделью,
  перезапуск, перенос навыка на невиданный кейс, 24/48 часов и реальная
  коммерческая задача ждут живого owner-run и отдельного evidence.

Текущий уровень лестницы: **SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT**.
Цель окна 4–6 дней после принятого первого прогона — измеримая передача
навыка и работа с коммерческой пользой с согласия владельца, без обещания дохода.
Terminal Run 1.2 остаётся другим пультом **того же** Bossman с общими
проектами/памятью/задачами/разрешениями и доказательствами.
