# Runbook на завтра (12.10.2026)

Ветка `feat/bossman-genjutsu-jeff-unified-20261010`, HEAD на момент написания — коммит этого runbook (родитель `3910be96`).
Кандидат-архив собран из `9520a45a` (после него в ветку добавлены только tools/docs/poker-wip,
продуктовый код не менялся): `C:\Users\asd\Bossman\dist-9520a45a\BOSSMAN-Windows-x64-9520a45a610a.zip`,
SHA256 `ebd82c533b0fd897df7f68555d864d784953ef9dc0bc056db2651dfda5fa7cc8`, 2.03 ГБ,
`BOSSMAN_BUNDLE_ACCEPTANCE=PASS` по точному SHA. Это DIAGNOSTIC/READY_FOR_OWNER_RUN-кандидат,
не production release (см. PRODUCTION_READINESS_AUDIT_RU.md).

## Первый шаг утром

1. Открыть пульт → `/remote_status`. Ожидается «Remote Control «Bossman-Owner-PC»: работает» и
   ссылка `https://claude.ai/code/session_…`. Если «не запущен» → `/remote` → через минуту снова
   `/remote_status`.
2. Прочитать ночной отчёт в пульте (закреплённое сообщение «🌙 Bossman: ночь завершена») и
   `docs/evidence/night-20261011/report.md`.

## Что можно делать завтра (проверено 11.10)

| Сценарий | Как | Проверено |
|---|---|---|
| Управлять Claude с телефона | claude.ai/code или Claude mobile → сессия Bossman-Owner-PC; из пульта `/claude <задание>` (продолжает сессию) | Connected до перезагрузки (Codex); автозапуск после входа — задача `BossmanClaudeRemote`, реальный reboot NOT_RUN |
| Jeff 2.0 в Telegram (голос, паспорта, фото) | написать Jeff; «поговори со мной» включает голос владельцу | работает с 11.10 из unified-исходников, задача `BossmanJeff2` |
| Bossman в интернете | backend `/api/browser/*`: сессия → navigate → DOM → скриншот | 11.10 Wikipedia, улики `docs/evidence/browse-proof-20261011/` |
| Аудит безопасности ПК | `powershell -File tools\security_audit.ps1` | 11.10 FAIL 0 / WARN 5 |
| Face swap локально (CPU) | `.agents/skills/video-face-swap-facefusion/`, `tools/v2_pipeline/` | IMG_0889 сходство 0.70; GPU нестабилен до перезагрузки |
| Пульт: ChatGPT, перезагрузка | `/chatgpt`, `/reboot` → кнопка подтверждения | тесты; реальная перезагрузка NOT_RUN |

## Шаги владельца (по порядку)

1. `C:\Users\asd\Bossman\owner-autologon-20261011.ps1` — ПКМ → Run with PowerShell → UAC →
   ввести пароль один раз (нигде не сохраняется). После этого вход на рабочий стол без пароля.
2. `C:\Users\asd\Bossman\owner-hardening-20261011.ps1` — ограничить SSH домашней сетью и Tailscale.
3. Перезагрузка: в пульте `/reboot` → «🔁 Перезагрузить» (откажет, если идёт рендер). Через
   3–5 минут: `/remote_status` → Connected; открыть сессию с телефона в мобильной сети и
   написать «ответь словом OK». Это закрывает OWNER_VERIFICATION_PENDING.
4. Комментарий-улика на финальном SHA в PR #108 для workflow Intelligence Preservation.
5. Установка на ноутбук: распаковать архив, `Start-Bossman.cmd`, `Evening-Test.cmd`.

## Запуск Bossman вручную (если автозапуск не поднял)

```powershell
# backend + Jeff + пульт установленной сборки
Start-ScheduledTask BossmanOne-1-Backend; Start-ScheduledTask BossmanOne-2-Jeff; Start-ScheduledTask BossmanOne-3-Companion
# Jeff 2.0 (unified-исходники)
Start-ScheduledTask BossmanJeff2
# Remote Control keeper
Start-ScheduledTask BossmanClaudeRemote
```

Пульт из unified-исходников (нужен для `/remote`, пока новая сборка не установлена):
см. docs/owner/REMOTE_ACCESS_AFTER_REBOOT_RU.md (остановить установленный пульт штатно,
запустить `python -m bcc.telegram_companion --config …` из `command-center`).

## Остановить всё

- Пульт: `/stop` (STOP блокирует запуски и перезагрузку).
- Ночной раннер: файл `docs\evidence\night-20261011\STOP`.
- Remote Control keeper: файл `%LOCALAPPDATA%\Bossman\remote\STOP` (или `DISABLED` — не стартовать после входа).

## Что НЕ делать

- Не ставить новые ключи в `config.json` пульта: установленный пульт старой сборки падает на
  незнакомых ключах.
- Не запускать RunPod/облачные GPU; личные фото/видео не выгружать.
- Не мержить в `main` без зелёного обязательного CI на точном SHA.
