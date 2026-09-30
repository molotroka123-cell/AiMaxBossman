# TZ-02 — Безопасность и периметр (8 → 10)

Находки: SEC-01..SEC-06. Инварианты: INV-6 (fail-closed), INV-1 (через подпись улик, см. TZ-01 §2.1).

## 1. Текущее состояние (сильное — сохранить)

- Cookie-сессия HttpOnly + CSRF-заголовок на небезопасных методах (`command-center/bcc/api.js:12-14`, `auth.py`).
- Браузер: `HARD_DENY_ACTIONS={purchase,payment,wallet,bank_transfer}`, запрет схем вне http(s) и userinfo, F-010 запрет loopback/private/link-local/metadata без явного `BCC_BROWSER_ALLOW_PRIVATE` (`browser_control.py:35,63-140`).
- Терминал: `hard_deny_reason`, корни `allowed_roots`, scratch на агента, argv без shell (`tools_terminal.py:91-181`, `apps_control.py:379`).
- Секреты в БД — Fernet; редакция URL/логов (`redact_secrets`, `assembler.redact`, `testing_period._LONG_SECRET`).
- Организация: не расширяет права, ревью — только вето (`docs/v3/organization/SECURITY.md`).

## 2. Требования

### 2.1 Секрет-скан 2.0 (SEC-01) — MUST
`tools/ci_secret_scan.py` расширить:
1. Паттерны провайдеров: `sk-ant-[A-Za-z0-9_-]{20,}`, `sk-or-v1-[a-f0-9]{64}` (OpenRouter), `AIza[0-9A-Za-z_-]{35}` (Google), `xox[baprs]-[0-9A-Za-z-]{10,}` (Slack), `\b\d{8,10}:[A-Za-z0-9_-]{35}\b` (Telegram bot), `eyJ[A-Za-z0-9_-]{10,}\.eyJ` (JWT), `ghp_|github_pat_`, `AKIA` + 40-символьный secret, `-----BEGIN (PGP|OPENSSH|EC|RSA) PRIVATE`.
2. Энтропийный детектор: для токенов `[A-Za-z0-9+/=_-]{24,}` вычислять Шеннона `H = −Σ p_i log₂ p_i`; порог `H ≥ 4.0` бит/символ **и** отсутствие словарных подстрок → finding. Порог 4.0 выбран потому, что base64-случайные строки дают `H≈5.6–6.0`, hex — `≈4.0`, а естественные слова — `<3.5`.
3. Запрет файлов: `.env`, `*.pem`, `*.key`, `*.p12`, `id_rsa*` в индексе git (уже частично — «Файлы, которых не должно быть в git»); ZIP-архивы в корне MUST сканироваться по содержимому (`zipfile` в памяти), т.к. сейчас `.zip` в `SKIP_SUFFIX`.
4. Allow-маркер `ci-secret-scan: allow` остаётся, но требует комментарий-причину в той же строке (регэксп `allow: .+`).

### 2.2 Сессии и вход (SEC-02, SEC-03) — MUST
1. `SessionStore(ttl_hours=720)` → абсолютный TTL 7 дней **и** idle-таймаут 12 часов (`touch()` продлевает idle, не абсолютный). Ротация `sid` при каждом логине и при повышении привилегий.
2. Rate-limit на `POST /login`: token bucket на (IP, user-agent-hash): 5 попыток / 60 с, затем экспоненциальная задержка `min(2^n, 300)` с; после 20 неудач за час — lockout 15 мин с событием `auth.lockout`. Реализация — in-memory словарь + запись в `events` (без Redis).
3. Констант-тайм сравнение токена (`hmac.compare_digest`) — проверить в `auth.check`.

### 2.3 Сканы как гейт (SEC-04) — MUST
1. `pip-audit --strict` и `bandit -ll` без `continue-on-error`; baseline-файл `security/bandit-baseline.json` для известных findings с датой пересмотра.
2. Pin с хешами для dev-зависимостей (`pip-compile --generate-hashes`) хотя бы в CI.
3. SBOM (CycloneDX) как артефакт CI.

### 2.4 Неизменяемый журнал решений (SEC-06) — SHOULD
Hash-chain для таблиц `approvals` и `events`: колонка `prev_hash`, `hash = sha256(prev_hash || canonical(row))`. Проверка цепочки — команда `bcc verify-chain` и тест. Это делает ретроактивную правку аппрува в SQLite обнаруживаемой.

### 2.5 Capability-токены на инструменты — SHOULD
Вместо `allowed_tools` как списка строк — краткоживущий токен на run: `{run_id, tools:[…], effect_caps, exp}` подписанный тем же ключом, что улики (TZ-01). Исполнитель проверяет токен, а не доверяет `task.meta`. Это закрывает класс «модель изменила meta через инструмент».

### 2.6 Threat model — MUST (документ)
`docs/security/THREAT_MODEL.md`: активы, акторы (модель как ненадёжный участник, плагин, MCP-сервер, локальная сеть, владелец), границы доверия, для каждого контроля — какой инвариант он защищает.

## 3. Математика
- Энтропия: `H(s) = −Σ_{c} (n_c/|s|) log₂(n_c/|s|)`; для равномерного base64 `H → log₂64 = 6`. Порог 4.0 при `|s|≥24` даёт FP на hex-хешах git (H≈4.0) — исключить контексты `sha256=`, `commit`, 40/64-hex.
- Rate-limit: token bucket ёмкости `B=5`, скорость `r=5/60` с⁻¹; вероятность перебора 12-байтного `token_urlsafe` при 5 попытках/мин за год `≈ 2.6·10⁶ / 2⁹⁶` — пренебрежимо; без лимита — 10⁹ попыток/сутки всё равно ничтожны, но защищаем от credential-stuffing по утекшим токенам и от DoS БД.

## 4. Приёмка
1. Тест-корпус из 30 синтетических секретов (по одному на паттерн + 10 энтропийных) внутри `tests/fixtures/secrets_canary/` с маркером allow — все ловятся при снятии маркера.
2. ZIP с секретом внутри → FAIL скана.
3. `test_login_rate_limit`: 6-я попытка за минуту → 429; после 60 с — снова 200.
4. `test_session_idle_timeout`: `touch` через 13 ч → 401.
5. `test_hash_chain_detects_tamper`.
6. CI: job «секреты, JS, запрещённые файлы» падает на findings pip-audit (проверить намеренно уязвимой dev-зависимостью в отдельной ветке).

## 5. Чек-лист 10/10
- [ ] ≥ 15 паттернов + энтропия, ZIP сканируются
- [ ] TTL 7д/idle 12ч, ротация sid, rate-limit/lockout
- [ ] pip-audit/bandit — блокирующие, baseline с датой
- [ ] hash-chain approvals/events
- [ ] THREAT_MODEL.md
