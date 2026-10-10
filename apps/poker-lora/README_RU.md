# Poker-LoRA — выбор действия из проверенного состояния (HU NLHE, river)

Только выбор действия. Зрение (`apps/poker-vision`) и клики (executor + Computer Use) остаются отдельными компонентами.

```
Vision → проверенное состояние → Poker-LoRA (или эвристика) → проверка ответа → executor/Computer Use → проверка результата
```

## Что внутри
| часть | файл |
|---|---|
| точный решатель реки (CFR+, numpy) с заданными диапазонами и малой абстракцией ставок | `pokerlora/solver.py` |
| диапазоны (top-X % по heads-up эквити BotLab), споты, решение до сходимости | `ranges.py`, `spots.py` |
| датасет: вход (то, что видит игрок + заданные диапазоны), ответ JSON (действие, размер, вероятности, короткое пояснение) | `dataset.py` |
| валидация примеров и ответов модели (скрытая информация во входе запрещена) | `validate.py` |
| базовые политики и numpy-«имитация» (заглушка конвейера, НЕ LoRA) | `policies.py` |
| оценка на отложенных бордах: валидность, потеря EV, эксплуатируемость, игра против фиксированных, CI по спотам | `evaluate.py` |
| реестр версий: общий holdout, парный CI, откат, запись для дерева | `registry.py` |
| очередь разбора ошибок (никогда не метки) | `mistakes.py` |
| выгрузка SFT (только train/val) | `sft.py` |
| клиент локальной модели (loopback, OpenAI-совместимый) | `openai_policy.py` |
| обучение LoRA и проверка окружения (**не запускались здесь**) | `train/lora_train.py`, `train/env_check.py` |

## Разбор спота реки руками (учебный инструмент, без экрана и без живой игры)
```
python -m pokerlora.cli solve --board "Ah Kd 7c 2s 9h" --hole "As Qs" --pos IP --pot 40 --stack 100 --history "OOP:check"
```
`SolverPolicy` (`pokerlora/solver_policy.py`) решает спот тем же CFR+, что размечает датасет, и печатает равновесный микс для вашей руки.
Диапазоны — допущения (`--hero-pct/--villain-pct` = top-X %, или свои `--hero-range/--villain-range`), абстракция ставок та же
(check / 50 % / 100 % / pot-raise / all-in при малом SPR). Если история, кнопки или рука не ложатся в дерево — отказ, а не догадка.
Та же политика подключается к маршруту Bossman (`pokervision.policy_route`) вместо LoRA, но маршрут применим только к доказанному
heads-up на реке; в 6-max тренажёре зрение этого не доказывает, поэтому там она не используется.

## Команды
```
python -m pokerlora.cli build --spots 400 --seed 20261006 --out DATA      # решает споты, делит ПО БОРДАМ, пишет manifest с sha256
python -m pokerlora.cli study --data DATA --out OUT                       # базовые политики + имитация + реестр
python -m pokerlora.cli sft   --data DATA --out SFT                       # sft_train.jsonl / sft_val.jsonl (test не выгружается)
python train/env_check.py --out train_capability.json                    # на Windows/AMD ПК: можно ли вообще обучать
python train/lora_train.py --base PATH --data SFT --out ADAPTER --max-steps 20   # сначала smoke train
pytest apps/poker-lora/tests
```
Сравнение базовой модели и адаптера: поднять оба на локальном сервере (llama.cpp и т.п.) и пропустить через `OpenAICompatPolicy` + `example_metrics` / `profile_metrics` — тот же тест, те же споты.

Подробности и результаты: `evidence/REPORT.md`.
