# Мастер-промпт: как подключиться к RunPod (для другой модели / агента)

Ты — агент на ПК владельца Bossman (Windows 11, PowerShell + Git Bash). Задача этого документа — только подключение к RunPod: где взять доступ, как проверить ключ, как создать/найти под, как зайти по SSH, как всё выключить. Что именно обучать — придёт отдельным заданием.

## 1. Где брать ключ API

- Ключ уже лежит у владельца локально: `%LOCALAPPDATA%\Bossman\keys\provider-keys.env`, переменная `RUNPOD_API_KEY`.
- Читай его в коде (парсинг строки `RUNPOD_API_KEY=...`). Никогда не печатай ключ, не пиши его в логи, конфиги, коммиты, чекпоинты, промпты облачных моделей, на сам под.
- Если ключа нет или он не работает — попроси владельца прислать новый через пульт командой `/key RUNPOD_API_KEY=...` (он попадёт в тот же файл). Создаётся ключ в кабинете: https://www.runpod.io/console/user/settings → API Keys.

## 2. Официальные источники

- Документация: https://docs.runpod.io/
- GraphQL API (основной, им пользуемся): https://docs.runpod.io/sdks/graphql/manage-pods
- REST API: https://rest.runpod.io/v1/docs
- Python SDK: `pip install runpod` — https://github.com/runpod/runpod-python
- CLI `runpodctl`: https://github.com/runpod/runpodctl
- Цены GPU: https://www.runpod.io/pricing
- Готовые образы PyTorch: https://hub.docker.com/r/runpod/pytorch/tags

## 3. Проверка ключа и баланса (без запуска пода)

`POST https://api.runpod.io/graphql`, заголовки `Authorization: Bearer <ключ>`, `Content-Type: application/json`, любой `User-Agent`.

```graphql
query { myself { clientBalance currentSpendPerHr pods { id name desiredStatus costPerHr } } }
```

Ожидаемо: число в `clientBalance`, список `pods` (должен быть пуст, если ничего не запущено).

## 4. SSH-ключ (делать ДО создания пода)

- Сгенерируй локально: `ssh-keygen -t ed25519 -f %USERPROFILE%\.ssh\runpod_bossman -N ""`.
- Добавь публичную часть в настройки аккаунта (не в env пода — так не работает):
  сначала прочитай текущие ключи `query { myself { pubKey } }`, сохрани копию, затем
  `mutation { updateUserSettings(input: { pubKey: "<старые ключи>\n<новый .pub>" }) { id } }` — дописывай, не затирай.

## 5. Создание пода (общий шаблон)

```graphql
mutation { podFindAndDeployOnDemand(input: {
  name: "bossman-<задача>", gpuTypeId: "NVIDIA GeForce RTX 4090", cloudType: SECURE,
  gpuCount: 1, volumeInGb: 50, containerDiskInGb: 40,
  imageName: "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
  ports: "22/tcp", supportPublicIp: true, volumeMountPath: "/workspace"
}) { id costPerHr machine { gpuDisplayName } } }
```

- Не задавай `dockerArgs` — перезапись команды старта убивает `sshd`.
- Ошибка `SUPPLY_CONSTRAINT` = нет свободных карт: перебирай другие `gpuTypeId` (список: `query { gpuTypes { id displayName memoryInGb securePrice communityPrice } }`).
- Community-хосты дешевле, но бывает очень медленная сеть — проверь скорость до установки пакетов.
- Два пода одновременно — только если это укладывается в бюджет владельца.

## 6. Как зайти

- Опрашивай `query { pod(input:{podId:"<id>"}) { runtime { ports { ip isIpPublic privatePort publicPort } } } }`, пока не появится порт 22 с публичным IP.
- `ssh -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -i %USERPROFILE%\.ssh\runpod_bossman -p <publicPort> root@<ip>`
- Файлы: `scp -P <port> -i <ключ> ...`. `tmux` не предустановлен: `apt-get update && apt-get install -y tmux`. Модели с HF: `hf download` (старого `huggingface-cli` нет); токен `HF_TOKEN` из того же `provider-keys.env` — передавать как переменную окружения сессии, не записывать в файлы на поде.

## 7. Сторож и выключение (обязательно)

- Сразу после создания запусти локальный сторож, который через `N` часов (по бюджету) вызывает
  `mutation { podTerminate(input:{podId:"<id>"}) }`, и следит за суммой расходов.
- По окончании: скачай результат → `podTerminate` → снова проверь `myself { pods }` — список должен быть пустым. Ни один под не остаётся работать.
- Сообщи владельцу потраченную сумму и остаток баланса.

## 8. Жёсткие правила владельца

- RunPod — только для обучения (QLoRA/LoRA), не для турниров, eval и генерации.
- Бюджет задаёт владелец (сейчас лимит \$30); превышать нельзя.
- Личное (фото, видео людей, чаты, память) на под не отправлять никогда.
- Контрольные 150 сценариев `eval500` (holdout) не попадают в обучающие данные.
- Никаких админ-установок и драйверов на ПК владельца; ключи не пушить в git, перед любым push — скан на секреты.
