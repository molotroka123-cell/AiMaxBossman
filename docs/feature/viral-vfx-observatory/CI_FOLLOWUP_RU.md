# CI follow-up — 21 сентября 2026

PR #70 остаётся Draft; в release/bossman-owner ничего не сливать.

После a5be0acd исправлена синтаксическая граница workflow: runner.temp нельзя
использовать в job-level env. Коммит 597a25c0 задаёт пути отдельным шагом через
GITHUB_ENV. Первый прежний run 35565432745 имел ноль jobs и не считается тестом.

Настоящий feature run **35565642734** на 597a25c0 запустился: **47 passed / 1 failed**,
без skips, 48 проверок, 11.403 секунды. Все 30 FFmpeg-пиксельных проверок прошли
на FFmpeg 6.1.1 в GitHub. Отказ — наш /observatory/routes не обходил лениво
включённые роутеры FastAPI 0.141.1. Это программный дефект X-Ray, не проблема
доступа и не повод ослабить тест. Последующие шаги того run не выполнялись.

Исправление использует уже существующий `command_bar._walk_routes`, а не новый
параллельный каталог/обходчик. Две различающие регрессии lazy wrapper до починки
падали (2 FAIL / 1 PASS), после — 3 PASS. Контроль обычного prefixed APIRouter
исполняет настоящий FastAPI endpoint. В локальном FastAPI 0.128.2 современная
форма lazy wrapper представлена явным fixture, не установкой новой версии.

После исправления отдельно выполнена вся локальная группа:
**51 passed / 0 failed / 0 errors / 0 skipped, 30.46 секунды**. Native render,
DOM и Node улики предыдущего checkpoint остаются на прежних неизменённых
соответствующих файлах; они не переименованы в полный тест нового SHA.
Feature workflow теперь требует эти 51 проверки плюс прежние обязательные
native/DOM/real-browser/Node шаги. Нового полного CI PASS ещё нет на момент записи.

Exact tested runtime blob: b1be425ead70c8b85e11e200687886dcd02b2bd3.
Новый тест blob: db841961f510c6118798d5c526b157cf89bdafc2.
Original CI proof artifact10624256988 скачан: 1703 bytes,
SHA25672acf3bc0d9fbefb1bb821b5768fea6a9f3d15cfdd72f3ef9bf64da70a27f6e4.

Локальный running-app Chromium остаётся BLOCKED_BY_ENVIRONMENT (политика
localhost), а в GitHub этот сценарий обязателен. До зелёного результата,
сверки конфликтов с параллельным release и чистой сборки merge запрещён.
