# UI UX Pro Max — заметка о происхождении

**Источник:** https://github.com/nextlevelbuilder/ui-ux-pro-max-skill (папка
`.claude/skills/ui-ux-pro-max/`), версия 2.15.0, коммит
`56ea9cf174ed86786187daa9de0e6247cb0747e4` (15.10.2026). Забрано 19.10.2026 через `git clone --depth 1`.

**Лицензия:** MIT, © 2024 Next Level Builder. Полный текст — `LICENSE` рядом
(побайтово как в апстриме, sha256 `1b8bd455510cc1f6f9bd510ffe2ec372d1c51fe0e082ddfee8b41825fb7aa055`). MIT совместима с
распространением внутри репозитория AGPL-3.0-only: уведомление об авторских
правах и текст лицензии сохраняются, сам навык остаётся под MIT.

**Что внутри:** `SKILL.md` (адаптирован под Bossman: фронтматтер, раздел
«Bossman scope», пути `.agents/skills/...`, разделы границ и атрибуции; остальной
текст апстрима не менялся), `references/`, `scripts/` (`search.py`, `core.py`,
`design_system.py`, `reasoning_contract.py`) и `data/` — побайтовые копии
апстрима.

**Не взято:** тесты апстрима, `scripts/validate_data.py`,
`data/phosphor-icons-upstream.json` (нужны только мейнтейнерам для обновления
каталога), npm-установщик `ui-ux-pro-max-cli` (качает релизы с GitHub,
`update` запускает `npm install -g`), остальные навыки репозитория.

**Проверка на риски (19.10.2026):** скрипты — только стандартная библиотека
Python, читают локальные CSV; сетевых вызовов, телеметрии, subprocess/eval/exec
нет; запись файлов только по явному `--persist --output-dir` (имя
нормализуется, выход за папку невозможен). Хуков установки, postinstall и
инструкций, отменяющих правила безопасности, нет; сам SKILL.md прямо говорит,
что результаты поиска — рекомендации, а не команды.

**Как пользоваться:** из корня репозитория
`python .agents/skills/ui-ux-pro-max/scripts/search.py "touch target size" --domain ux`.
Обновление — только вручную: новый коммит апстрима, повторная проверка рисков,
обновить SHA здесь и в `SKILL.md`.
