# Computer Use в облачном Windows VM — 10.10.2026

**Итог: BLOCKED** — у Bossman нет настроенного канала, через который он видит экран VM и отправляет туда ввод.

Подробности в формате JSON: `computer-use-vm-20261010.json`.

## Что проверено (только чтение)

- **computer_operator** (`bossman-core/bossman/computer_operator/subsystem.py`) подключает только локальные backends: `LocalScreenshotProvider` (снимок экрана этого ПК через pyautogui), `WindowsDesktop` (локальный ввод через pywinauto/pyautogui), `AppLaunchAdapter`, `ExistingBrowserAdapter`. Удалённого backend (RDP/VNC/агент в VM) нет.
- **Этот ПК** WIN-IQPRHTSAJL9 физический (AMD Radeon 8060S / Strix Halo, AMI BIOS), сессия console, не RDP. Входящий RDP выключен. Слушают sshd :22, Bossman 127.0.0.1:8801 и Ollama 127.0.0.1:11434.
- **Tailscale**: два узла — этот ПК (.36) и `muse` (.6, ОС не указана). Ни в репозитории, ни в данных Bossman `muse` не упоминается, поэтому считать его VM нельзя.
- **Клиенты удалённого доступа на этом ПК**: история mstsc пуста, .rdp-файлов нет, Windows App / Windows 365 не установлены. Процессов RDP/VNC/Parsec/RustDesk/AnyDesk/TeamViewer/Sunshine нет, активных соединений на 3389/5900/22/5985 тоже нет. Каталог `~/.ssh` содержит только `authorized_keys`, клиентских хостов нет.
- **bcc.db**: ключи settings — `apps.control_enabled`, `higgsfield.api_key`, `memory.vault`, `openrouter.provider_id`, `terminal.roots`. Таблиц, колонок и значений, связанных с VM, нет.
- В репозитории совпадения rdp/vnc/tailscale/remote относятся только к Stage-6 API `/remote/*` для телефона владельца и к политике `tailscale serve`. Это канал от владельца к Bossman, а не от Bossman к VM.

## Границы доступа (по коду и документации)

- allowlist содержит только Блокнот и Калькулятор, на каждое действие нужен approval владельца.
- Если после approval сменился foreground, ввод отклоняется. Обходить это нельзя.
- Endpoint локальной модели принимается только loopback-адрес.

## Прогон

- Выбрана модель `bossman-fast-qwen36-35b-a3b-q5:latest`, digest `0c57084a…2a65`, endpoint Ollama 127.0.0.1:11434, профиль 38. **Модель не вызывалась.**
- Действий: 0. Снимков VM «до/после»: нет. Локальный рабочий стол намеренно не снимался и не подменял VM. Блокнот не открывался, ничего не сохранено. ПО удалённого доступа не устанавливалось, порты не открывались.

## Что нужно от владельца

1. Указать, какой именно компьютер — «облачный VM»: провайдер, hostname или имя узла Tailscale. Если имеется в виду этот ПК WIN-IQPRHTSAJL9, скажите это явно. Тогда для него действует уже записанный результат `computer-use-local-qwen-20261010.json` (BLOCKED: окно Блокнота не удерживало фокус).
2. Выбрать и одобрить канал:
   - (a) рекомендуется: агент observe/act Bossman работает внутри VM, VM входит в tailnet, Bossman обращается к нему с токеном устройства и scope;
   - (b) RDP/VNC-клиент на этом ПК. Этот вариант слабее: ввод идёт через окно клиента, и привязать фокус сложнее.
3. Передать учётные данные только через vault и одобрить включение RDP/агента и изменения firewall и Tailscale ACL на VM.
4. Одобрить изменение кода: в computer_operator нужен удалённый backend (снимок экрана, ввод и список окон, привязанные к сессии VM).
