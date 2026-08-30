# SKILLS PACK V1 — INTEGRATION REPORT

Дата: 2026-08-30 · HEAD: `4e72785` · Пакет: `bossman_skills_pack` (в bundle V1)

## Статус: NOT_INTEGRATED (обоснованное решение) + AUDIT DONE

Собственные тесты пакета: **8 passed**. Security-разбор: см. SKILLS_PLUGINS_FINAL_BUG_CHECK.md.

## Что в пакете
Скиллы: web (SSRF-safe fetch), files, knowledge/RAG, github, gmail, calendar, drive, n8n,
monitor, sqlite (read-only), media/ffmpeg. Инфраструктура пакета: `registry`, `policy`
(ALLOW/ASK/DENY), `security` (path confinement + SSRF), `secrets`, `audit` (redaction),
`contracts`, `factory`, `models`.

## Почему НЕ вендорим как есть
Пакет несёт СВОИ registry/policy/security/secrets/audit. Внести их в bossman-core целиком —
значит поднять вторую policy-подсистему, второй secret store и второй audit-слой рядом с уже
существующими (`perimeter` scopes, secret-broker, `obs.redact`, event bus). Это прямой запрет
absolute rules. Плюс правильную интеграцию нельзя LIVE-проверить в этой среде (нет внешних
кредов/сервисов), а mock как evidence для live-гейта запрещён.

## Как интегрировать правильно (адаптерный план)
Каждый скилл — тонкий адаптер, монтируемый в СУЩЕСТВУЮЩИЙ реестр инструментов ядра
(`bossman.tools.REGISTRY` / `command-center` tool-registry), а НЕ через собственный registry:
1. **Идентичность/скоупы** — маппить `capabilities`/`scopes` скилла на Stage 6 scopes ядра
   (`SCOPE_CHAT`/`SCOPE_ADMIN`/…); unknown capability → DENY.
2. **Политика** — ALLOW/ASK/DENY решает существующий policy/approvals-путь ядра; ASK →
   `approvals.create/wait`. Не поднимать второй policy-движок.
3. **Секреты** — только reference через существующий secret-broker; сырые креды не в
   манифест/лог/audit/exception (skills `secrets.py` уже работает по reference-модели —
   переиспользовать через broker ядра).
4. **Сеть** — web-скилл уже fail-closed (DNS-resolve + no-redirect); monitor/n8n подключать с
   тем же классом защиты (F1/F2 из bug-check) или через egress-allowlist ядра.
5. **Файлы** — `confined_path` пакета корректен; при интеграции корень = одобренный workspace
   ядра; деструктивное удаление → ASK.
6. **SQL** — sqlite-скилл открывает `mode=ro` — переиспользовать как есть; запись отсутствует.
7. **Media/ffmpeg** — argv-only, без shell; переиспользовать существующий admission/ресурс-гейт.
8. **Audit** — эмитить в существующий `events`/audit ядра с redaction, без второго sink.

## Верификация, которую потребует интеграция (сейчас недоступна)
- Реальные OAuth-креды (github/gmail/calendar/drive) → SKIP_EXTERNAL_CREDENTIAL.
- Живой n8n → SKIP_EXTERNAL_SERVICE.
- Полный прогон ядра под Postgres → SKIP_HOST (Docker down).

## Вывод
Пакет качественный и fail-closed; готов к адаптерной интеграции, но НЕ к вендорингу.
Интеграция — отдельная задача на способном хосте с живыми сервисами; здесь она была бы
недоказуема и рискнула бы дублированием инфраструктуры.
