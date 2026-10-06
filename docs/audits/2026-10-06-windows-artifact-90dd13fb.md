# Windows-пакет из CI: факты по run 37453456807 (06.10.2026)

Same-product Terminal Run: пульт, CLI, дашборд и Telegram — одна поверхность одного Bossman. Лестница North Star: достигнут только `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`; готовый
пакет и PASS приёмки архива её **не поднимают**.

## Подтверждено логом и API GitHub Actions

| Что | Значение |
|---|---|
| Run | https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/37453456807 (`One-download Windows application`, вывод `success`; задачи `bundle`, `owner-experience`, `freeze` — все `success`) |
| Ветка и SHA сборки | `night/bossman-windows-bundle-20261006`, `90dd13fb92c4e838b24a0d580a1e7b9847d63d89` |
| Архив внутри run | `BOSSMAN-Windows-x64-90dd13fb92c4.zip`, 792 054 565 байт |
| SHA256 архива (`bundle-acceptance.json`, поле `archive_sha256`) | `c1fc77b0178d99bef7ac8a0ee60c73989caf8db8b138ec387493dd2b65daa6c3` |
| Приёмка архива | `bundle-acceptance.json` → `"status": "PASS"`, `source_sha` = `90dd13fb…`; в логе `BOSSMAN_BUNDLE_ACCEPTANCE=PASS`, `OWNER_ACCEPTANCE_PREFLIGHT=PASS` |
| Артефакт GitHub для скачивания | `bossman-windows-90dd13fb92c4e838b24a0d580a1e7b9847d63d89`, id `11409362149`, 783 831 813 байт, действует до 2026-11-05; контрольная сумма контейнера артефакта `sha256:697664ff30d665a3e716a3402ba54acc9e52737198e298cbbc8cb0058b1d0634` |
| Дополнительные артефакты | `windows-owner-proof-90dd13fb…` (id 11412965225), `astra6-freeze-90dd13fb…` (id 11412784954) |

Две суммы — разные объекты: `c1fc77b0…` — SHA256 самого `BOSSMAN-Windows-x64-90dd13fb92c4.zip`, `697664ff…` — сумма, под которой GitHub хранит контейнер артефакта (zip с zip).
Скачать ZIP из облака я не мог (хост артефактов недоступен из этой среды), поэтому **сумму файла после скачивания я не сверял**: сверка — шаг владельца (`certutil -hashfile … SHA256` → должно совпасть с `c1fc77b0…`).

## К какому коду относится архив (важно)

Собран коммит `90dd13fb` ветки `night/…`: это `6d9c1f56` (исправление `EchoGuard`) плюс один docs-маркер в `START_TOMORROW_RU.md`, нужный лишь для запуска workflow.
**Это не текущий HEAD ветки цели**: ветка цели с тех пор ушла на ~30 коммитов (английский Jeff, исправление Switch, влив poker-vision/poker-lora, тесты, исправления CI).
Поэтому пакет доказывает, что **сборка Windows-архива проходит приёмку на `6d9c1f56`**, а не что на текущей ветке цели есть готовый пакет. Пакет на актуальный код запускается отдельным прогоном (см. журнал).

## Чего это не доказывает

* Приёмка архива (`PASS`) — проверка состава и запуска на CI-раннере, а не работа Bossman на ПК владельца и не самоулучшение.
* Живая работа, голос, звонки, деньги — не проверялись.
