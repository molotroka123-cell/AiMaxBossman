# Visual page inventory — Command Center (vision pass 2026-09-07)

Source of truth: `window.__bxPages` after login against a real uvicorn server
(`tests/test_ux2_thinking_pane.LiveServer`) in real Chromium
(`/opt/pw-browsers/chromium-1194`). The list is the union of the MVP pages in
`command-center/ui/pages.js` (`PAGES`) and the lazy feature registry
`command-center/ui/pages/index.js` (`FEATURE_PAGES`), in navigation order.
No routes were invented; every row below was opened and screenshotted at
1920×1080, 1440×900 (light + dark), 1366×768, 1024×768 and 390×844.

Total: **36 routes**. `nav` = primary (sidebar main group / dock candidates) or more.

| Route | Title | nav | Registered in |
|---|---|---|---|
| `#/home` | Главная | primary | ui/pages.js (PAGES, MVP) |
| `#/models` | Модели | primary | ui/pages.js (PAGES, MVP) |
| `#/agents` | Агенты | primary | ui/pages.js (PAGES, MVP) |
| `#/tasks` | Задачи | primary | ui/pages.js (PAGES, MVP) |
| `#/schedules` | Расписания | primary | ui/pages.js (PAGES, MVP) |
| `#/approvals` | Ждут решения | primary | ui/pages.js (PAGES, MVP) |
| `#/system` | Система | primary | ui/pages.js (PAGES, MVP) |
| `#/settings` | Настройки | primary | ui/pages.js (PAGES, MVP) |
| `#/video-studio` | Video Studio | primary | pages/index.js → ui/pages/video_studio.js |
| `#/bossman-chat` | История видео и чат | more | pages/index.js → .ui/pages/video_chat.js |
| `#/home-v3` | Главная | primary | pages/index.js → ui/pages/home.js |
| `#/apps` | Приложения | primary | pages/index.js → ui/pages/apps.js |
| `#/overview` | Обзор | primary | pages/index.js → ui/pages/overview.js |
| `#/missions` | Миссии | primary | pages/index.js → ui/pages/missions.js |
| `#/router` | Выбор модели | more | pages/index.js → ui/pages/router.js |
| `#/governor` | Присмотр | more | pages/index.js → ui/pages/governor.js |
| `#/resources` | Ресурсы | primary | pages/index.js → ui/pages/resources.js |
| `#/skills` | Навыки | primary | pages/index.js → ui/pages/skills.js |
| `#/terminal` | Терминал | primary | pages/index.js → ui/pages/terminal.js |
| `#/benchmarks` | Замеры моделей | more | pages/index.js → ui/pages/benchmarks.js |
| `#/browser` | Браузер | primary | pages/index.js → ui/pages/browser.js |
| `#/coding` | Coding-сессии | more | pages/index.js → ui/pages/coding.js |
| `#/agentmap` | Карта агентов | more | pages/index.js → ui/pages/agentmap.js |
| `#/orchestras` | Команды агентов | more | pages/index.js → ui/pages/orchestras.js |
| `#/forks` | Развилки | more | pages/index.js → ui/pages/forks.js |
| `#/healing` | Восстановление | more | pages/index.js → ui/pages/healing.js |
| `#/openrouter` | OpenRouter | more | pages/index.js → ui/pages/openrouter.js |
| `#/command` | Пульт | primary | pages/index.js → ui/pages/mobile.js |
| `#/builder` | Конструктор миссий | primary | pages/index.js → ui/pages/builder.js |
| `#/images` | Изображения | primary | pages/index.js → ui/pages/images.js |
| `#/trading_lab` | Обучение трейдингу | more | pages/index.js → ui/pages/trading_lab.js |
| `#/mission_console` | Операторский канал | primary | pages/index.js → ui/pages/mission_console.js |
| `#/web_research` | Поиск в интернете | more | pages/index.js → ui/pages/web_research.js |
| `#/control` | Пульт | primary | pages/index.js → ui/pages/control.js |
| `#/web_designer` | Веб-дизайн | primary | pages/index.js → ui/pages/web_designer.js |
| `#/objectives` | Цели | more | pages/index.js → ui/pages/objectives.js |

## Open states covered (real clicks, same server)

| State | How it was opened | Screenshot prefix |
|---|---|---|
| Models → provider wizard (step 1, step 2) | `#/models` → «Добавить модель» | `models_wizard_step1`, `models_wizard_step2` |
| Agents → new agent modal | `#/agents` → «Новый агент» | `agents_new_modal` |
| Schedules → new schedule modal | `#/schedules` → «Новое расписание» | `schedules_new_modal` |
| Missions → new mission modal | `#/missions` → «Новая миссия» | `missions_new_modal` |
| Skills → new skill modal | `#/skills` → «Новый навык» | `skills_new_modal` |
| Command palette | Ctrl+K on `#/home` | `palette_open` |
| Thinking pane («Процесс работы») | `#think-open` on `#/tasks` | `thinking_pane` |
| Video Studio: new project dialog, editor (media library + timeline), workspaces Цвет/Звук/VFX/ИИ, export modal | `#/video-studio` → «Новый проект» → tabs → «Экспорт» | `video_*` |
| Web Designer: project created + editor (code / live preview / inspector) | `#/web_designer` → name + description → «Открыть проект» | `web_designer_editor` |
| Login screen after logout (error state) | `#/settings` → «Выйти» | `stale_banner` (captured the post-logout login screen, see audit) |

Not reproducible here (environment limits, recorded as such — not defects):
* Web Designer responsive preview buttons are a `<select>` («ПК · 1440 × 900»), and real pointer clicks do not reach the sandboxed (opaque-origin) preview iframe in this container.
* Tasks → «По расписанию…» opens no `#modal-root` dialog in this build (see FUNCTIONAL_FINDINGS in `VISUAL_AUDIT.md`).
* `ffmpeg` with libx264 is absent, so Video Studio export/preview jobs cannot be run to a finished state; only the dialogs were checked.
