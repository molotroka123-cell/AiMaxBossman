# STAGE 12 STATUS

HEAD: см. git log (stage12-коммиты поверх cb97ad4)
BRANCH: claude/bossman-control-v03-43igbk

## INTEGRATED
- `bossman/remote_client/mobile_api.py` — scoped mobile surface: tasks create/list/detail (device-scoped), approvals list (redacted preview), agents read-only, session/logout, PWA-статика. Смонтирован через `router.include_router(_mobile_router)` — Stage 6 остаётся source of truth, второй auth-системы нет.
- `bossman-core/remote-app/` — приватное PWA (index/app.js/remote-core.mjs/styles.css/manifest/sw.js), отдаётся на `/remote/app` c `Cache-Control: no-store`.
- `bossman-core/scripts/bootstrap_remote_device.py` — локальный bootstrap первого owner-устройства (raw token печатается один раз, не сохраняется).
- `bossman-core/ios/` — BossmanRemoteKit (URLSession/Codable/SSE-парсер) + BossmanRemoteApp (Keychain, SwiftUI) как ОТДЕЛЬНЫЙ Swift-пакет, не смешан с Python core.
- Security-гвардейский тест Stage 6 (`test_router_exposes_no_policy_mutation_route`) расширен на mobile-роутер: whitelist путей + методы (agents/app только GET), запреты сохранены.

## MODIFIED FROM HANDOFF (ZIP)
- `_approval_view`: добавлен второй слой PII-редакции (Stage 11 `ai_lab.sanitizer.sanitize_text`) поверх `obs.redact` — ZIP-версия пропускала email/IP (найдено adversarial-тестом).
- Anti-escalation тест адаптирован к `_IncludedRouter`-обёртке нового FastAPI (интенция усилена: проверка методов на чувствительных путях).
- Остальное — как в ZIP: сравнение с HEAD показало, что Stage 6 API (Principal/scopes/db/events/runner) совпадает с ожиданиями пакета.

## SECURITY VERIFIED
- IDOR: чужая задача для chat-device → 404 (без утечки существования), admin-видимость явная
- Scope: chat не может approve/admin; mobile-роутер не содержит decide-эндпоинта (он в Stage 6 за approve-scope)
- Session: logout ревокует только session, device-token не трогает; revoked → immediate denial (Stage 6 tests)
- Token hygiene: raw-токен не в логах/URL/localStorage; hashes-only в durable store (Stage 6 `test_registry_stores_hash_not_raw`)
- Redaction: Bearer/api_key/password/email/IP — не в preview (тест)
- PWA/SW: sw.js не кэширует `/remote/*`; нет CDN/analytics/external JS; токен не в query-string; статика без credentials; manifest валидный JSON
- 0 raw-token в репо (сканер секретов Stage 8.1 проходит)

## TESTS
- `tests/test_stage12_mobile_api.py` (ZIP) — 5 passed
- `tests/test_stage12_security.py` (новый, adversarial) — 9 passed
- `tests/test_remote_client.py` — 17 passed (гвардейский расширен)
- Полный regression bossman-core: **345 passed, 27 skipped, 0 failed** (skips = Chromium/runsc/POSIX-only)

## PWA
- Установка: Safari → /remote/app → Add to Home Screen. Требуется приватный TLS-туннель (Tailscale) до Core.

## NATIVE IOS
- `swift build/test` — BLOCKED BY HOST (нет Swift на Windows). Код и SSEParserTests в репо; сборка на macOS.

## BLOCKED BY HOST
- Swift toolchain (macOS), Postgres для живой PWA-приёмки

## OPEN P0
- нет

## OPEN P1
- admin видит все задачи — соответствует текущей security model (Session Handoff: admin = operational view)
- /remote/app статика без auth по дизайну ZIP (shell не секрет, все API за скоупами) — зафиксировано тестом

## NEXT
1. Живая приёмка PWA на Core с Postgres + Tailscale
2. macOS: swift build && swift test
3. Добавить device-привязку задач в UI-фильтры admin-вью
