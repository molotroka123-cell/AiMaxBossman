# Слияние ветки веб-дизайнера: готовые разрешения конфликтов

Проверено пробным слиянием `claude/bossman-control-v03-43igbk` (`e626bee`) с
`feat/web-designer-live-panel` (`54947dc`). Конфликтов четыре, все механические —
это не спорные места, а параллельные правки одних строк.

| Файл | Что брать | Почему |
|---|---|---|
| `command-center/ui/pages/index.js` | **обе стороны** | Каждая ветка добавляет свою страницу. Нужны и импорт, и строка в `FEATURE_PAGES`: `ControlPage` (пульт владельца) и `WebDesignerPage`. Взять одну — молча потерять страницу. |
| `.github/workflows/bossman-core-ci.yml` | любую | Одна и та же строка `python -m pip install -e .`, разница только в поясняющем комментарии. |
| `.github/workflows/command-center-ci.yml` | любую | То же. |
| `solana_volume_suite/setup_mainnet.py` | **сторону дизайнера** | Её версия убирает пароль хранилища по умолчанию (`SuperSecretMasterPass123!`), который раньше подставлялся при нажатии Enter. Это исправление слабого места, а не косметика. |

После разрешения проверить, что не потеряно:

    grep -n "ControlPage\|WebDesignerPage" command-center/ui/pages/index.js   # обе строки
    grep -c "SuperSecretMasterPass" solana_volume_suite/setup_mainnet.py      # 0
    python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]"
    python3 tools/ci_secret_scan.py

Смоук на объединённом дереве, уже выполненный: 86 passed по набору дизайнера и
всем воротам TRUTH-003, 62 passed по всем браузерным наборам.
**Полный регресс объединённого дерева НЕ завершён** — остановился на 69% с
двумя неразобранными падениями (см. `OPEN_WORK_AUDIT.md`). Считать дерево
зелёным до их разбора нельзя.
