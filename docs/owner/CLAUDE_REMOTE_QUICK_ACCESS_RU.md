# Быстрый удалённый доступ к Claude на ПК владельца

Запуск из существующего терминала Bossman через `terminal.run`, host-mode,
разрешённый project root и обычные approval gates:

```powershell
powershell.exe -NoProfile -File tools/start_claude_remote.ps1
```

Явно запрошенный владельцем режим без подтверждений Claude:

```powershell
powershell.exe -NoProfile -File tools/start_claude_remote.ps1 -PermissionMode bypassPermissions
```

Это workflow существующего terminal backend, а не fine-tune модели и не
доказательство управления терминалом мышью/клавиатурой. UI-автоматизация
терминала здесь не используется. Политики самого Bossman не меняются.
Запускать только по запросу владельца. Не пересылать ссылки и вывод в чужие чаты.

Скрипт проверяет ветку и origin/HEAD, не переключает ветку и не делает commit/push.
Команда проверена по help установленного Claude Code 2.1.284.
Открыть выданную ссылку на claude.ai/code или сессию Bossman-Owner-PC
в Claude mobile под аккаунтом владельца. ПК должен оставаться включённым,
подключённым к интернету, процесс Remote Control должен работать.
Доступ из внешней сети отдельно ещё не проверен.
Остановка: Ctrl+C в запущенном процессе; автоматический запуск после reboot
и постоянная служба не установлены. Не запускать второй экземпляр поверх первого.
