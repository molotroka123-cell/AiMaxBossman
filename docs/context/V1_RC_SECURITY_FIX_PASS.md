# V1 RC — Security Fix Pass (pre-hardware gate)

BASE_HEAD: 9a0db65075e5d5f24a347c20b16947d71d3ef854 (remote HEAD на старте)
FINAL_HEAD: см. git log ветки claude/bossman-control-v03-43igbk после push

## Интеграции

LANE2_LSP_CONFINEMENT: cherry-pick fe874c0 (fix(code_intel): RC-HARDENING-1 LSP
workspace confinement via canonical allowed_roots). Взят код + тесты; audit-doc
конфликта не включён (файла нет в HEAD — дедуп). Regression: 31 passed / 1 SKIP_HOST.

## Lane-1 фиксы (plugins-security)

LANE1_P1_1_SQL: гейт `sql_read_only_ok` переписан (fail-closed): строковые
литералы вырезаются перед сканом; write-токены запрещены по всему оператору,
включая data-modifying CTE `WITH … DELETE/INSERT/UPDATE`; разрешены только
`pragma (table_info|index_list|index_xinfo|index_info)(`; multi-statement — deny.
Драйверный бэкстоп `mode=ro` сохранён и покрыт тестом.

LANE1_P1_2_DNS_REBIND: `safe_get` теперь коннектится на проверенный IP
(`_PinnedBackend` поверх httpcore network backend через `PinnedTransport`,
унаследованный от `httpx.AsyncHTTPTransport` — полный glue + дефолтная
TLS-проверка). Второго DNS-резолва на connect-пути нет; hostname сохраняется
для Host/SNI/сертификата. Regression: fake-DNS, второй resolve запрещён →
DNS_RESOLUTION_COUNT == 1, запрос проходит на pinned IP.

LANE1_P2_1_MAX_BYTES: тело читается потоково с капом `max_bytes`; превышение —
`PluginSecurityError` без полной аллокации; клиент/транспорт закрываются
(покрыто тестом HTTP_RESPONSE_CLOSED_ON_LIMIT).

LANE1_P2_2_REDACTION: `_h_http_get`/`monitor.feed` скрабят известные значения
настроенных кредов из внешнего контента (`redact` + `_known_secret_values`);
error-путь generic-коннекторов не содержит значений кредов (тест REDACT_ERROR_PATH).

## Тесты (CURRENT HEAD, не legacy counts)

TARGETED (plugins + security): 75 passed / 1 skipped (SKIP_HOST symlink) / 0 failed.
LSP: 31 passed / 1 skipped / 0 failed.
FactStore: 6 passed / 0 failed.
Scheduler (title↔name): 3 passed; UI шлёт `name` (ui/pages.js:1380) → API 2xx.

FULL_TESTS command-center: 510 passed / 13 failed / 20 skipped.
Baseline clean 9a0db65: 485 passed / 14 failed / 18 skipped.
Все 13 фейлов предсуществующие и host-specific (терминал/симлинки/дискавери);
на baseline их 14 (включая symlink-тест, который теперь честный SKIP).
NEW_FAILS=0.

FULL_TESTS bossman-core: 899 passed / 1 failed / 31 skipped.
Единственный фейл `test_browser_policy.py::test_profile_lock_exclusion_and_stale_recovery`
воспроизводится на чистом 9a0db65 (host-specific Windows file-lock). NEW_FAILS=0.
931 collected; suite включает test_world_intelligence_pythia.py (эквивалентный
Pythia-фикс b0a5a0c уже в HEAD — дедуп с Lane-RC-C, повторно не применялся).

SECRET_SCAN: PASS. git diff --check: чисто.

## LIVE PRECHECK (lightweight, этот хост)

SAFE_GET_LIVE_TLS: PASS (реальный HTTPS https://httpbin.org/get через pinned
transport, TLS-проверка не отключена, 200)
SQL_LIVE_READ: PASS; SQL_LIVE_WRITE_BLOCKED: PASS (mode=ro)
OBSIDIAN_INSIDE_WRITE: PASS; OBSIDIAN_TRAVERSAL: PASS

## Honest skips

- SKIP_HOST: symlink-тесты (Windows privilege, os.symlink WinError 1314)
- SKIP_HOST: часть host-specific фейлов baseline (terminal/browser/lock) —
  воспроизводятся только на этом Windows-хосте, к фиксам Lane-1/Lane-2 отношения
  не имеют (доказано baseline-прогоном)
- SKIP_EXTERNAL_CREDENTIAL / NOT_TESTED_LIVE: github/gmail/calendar/drive/
  telegram/n8n/openrouter коннекторы (нет кредов в среде); credential-gate
  покрыт unit-тестами
- LIVE_OWNERS: реальный публичный RSS/redirect-матрица на боевом наборе хостов —
  на owner hardware прогоне

## Deferred post-RC (не трогано в этом pass)

- exotic IP literal normalization (hex/decimal/short) — сейчас fail-closed
  через resolve-слой
- dead helper `_u()` / unused `allowed_hosts` cleanup
- host-specific красные тесты Windows-машины (отдельный hardening)
