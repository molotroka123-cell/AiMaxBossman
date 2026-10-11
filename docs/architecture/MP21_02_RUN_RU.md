# 02. Запуск, проверки и сборка (сверено 11.10.2026)

## 1. Из исходников (Windows, PowerShell)

```powershell
cd C:\Users\asd\Bossman\wt-unified-20261010
.\start-bossman.ps1                # Python 3.11+, .venv, установка трёх пакетов, bossman doctor, окно
.\start-bossman.ps1 -DoctorOnly    # только диагностика
.\start-bossman.ps1 -SkipInstall -Port 8801
```

`start-bossman.ps1` останавливается на BLOCKED из doctor (шапка файла). Без скрипта backend стартует так:

```powershell
$env:BCC_PORT = "8801"             # по умолчанию 8800 (bcc/config.py)
python -m bcc                      # bcc.app.main; хост 127.0.0.1
curl http://127.0.0.1:8801/health/live   # маршрут в bcc/api.py
```

## 2. Данные и каталоги

| Переменная | Значение по умолчанию | Источник |
|---|---|---|
| `BCC_DATA_DIR` | `%LOCALAPPDATA%\Bossman\CommandCenter` (для исходной установки `command-center/data`) | `bcc/config.py::_data_dir` |
| `BCC_HOST` / `BCC_PORT` | `127.0.0.1` / `8800` | `bcc/config.py` |
| `DATABASE_URL` | `sqlite+aiosqlite:///<data_dir>/bcc.db` | `bcc/config.py` |
| `BOSSMAN_AUTONOMY_ROOT` | `<data_dir>/autonomy` | `bcc/autonomy/cycle.py::default_root` |
| `BOSSMAN_LOCAL_ONLY` | выключено | `bossman_shared/privacy.py` |

Ловушка этой машины (замерена 11.10, `docs/owner/runs/MP21_A_CHECKS_20261011.md`): голый `python` подхватывает
`bossman_shared` и `bossman` из СТАРОГО чекаута `C:\Users\asd\Documents\Default Project\AiMaxBossman-integrated-old-20260927`.
Перед тестами всегда задавать `PYTHONPATH=<wt>/command-center;<wt>;<wt>/bossman-core` (в bash на Windows разделитель `;`)
и проверять `python -c "import bossman_shared;print(bossman_shared.__file__)"`.

## 3. Тесты (один процесс, без `-n` больше 2)

```powershell
cd command-center
python -m pytest -q -p no:cacheprovider --timeout=240 tests/test_local_only_switch.py tests/test_offline_mode.py
cd ..\bossman-core ; python -m pytest -q -p no:cacheprovider tests/test_trading_mp21_d_fixes.py
cd ..\apps\poker-botlab ; python -m pytest -q -p no:cacheprovider tests
cd ..\.. ; python -m pytest -q -p no:cacheprovider tests/exchange_volume
```

Известное ограничение Windows: тест с очень длинным параметром (`test_open_news_skill.py::test_http_failure_never_becomes_success...`)
падает в teardown, потому что `PYTEST_CURRENT_TEST` больше 32767 символов; он отмечен как не пройденный здесь.
`solana_volume_suite/tests` требуют пакет `solders`, которого в этом интерпретаторе нет (сбор тестов падает).

## 4. Проверка секретов и точного SHA

* `python tools/ci_secret_scan.py` (stdlib, три слоя: паттерны, энтропия, запрещённые файлы и ZIP).
* `tools/exact_sha_certify.py` сертифицирует кандидата на ТОЧНОМ SHA; объявление кандидата = правка
  `tools/release_candidate.json`. Это маркер запроса прогонов, а не одобрение релиза.

## 5. Установленная сборка и откат

Архив `BOSSMAN-Windows-x64-<SHA>` самодостаточен, данные лежат отдельно (`docs/owner/ROLLBACK_RU.md`). Установленная у
владельца сборка `6de18f8d9a08` отстаёт примерно на 140 коммитов (`docs/owner/OPEN_PROBLEMS_20261010.md`, B4): новая сборка
после слияния этой ветки НЕ делалась и не публиковалась.

## 6. Что запуск не доказывает

Запуск и зелёные тесты не равны «принято владельцем»: приёмка на ПК владельца, мониторинг 24 часа и сборка нового архива
остаются отдельными шагами (см. `docs/owner/MP21_FINAL_REPORT_RU.md`).
