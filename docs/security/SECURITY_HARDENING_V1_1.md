# SECURITY HARDENING V1.1 — defense-in-depth pass

Компактная волна поверх PRE-HARDWARE FREEZE PASS (не отменяет его). Ловим второй
уровень: fail-open, defense-in-depth, supply-chain, security-wiring. Основана на
разборе владельца cybersec-аудита (принято ~85–90%) с тремя его поправками к плану.

Принятые поправки владельца (реализованы именно так):
1. Secret Guardian на egress — **fail-CLOSED**, не fail-open: не смогли проверить
   sensitive-канал → HOLD (задержать/approval), а не «на авось».
2. Shell approval — **не** на каждый pytest/npm в disposable sandbox: изолированный
   docker (сеть none, bounded) = AUTO; host/local exec = ALWAYS ASK.
3. Hash-chained audit — tamper-**evident**, не tamper-proof: нужен внешний signed
   anchor. Для V1.1 hash-chain отложен как V1.2 design-item (см. ниже), чтобы его
   не переоценивать.

## Сделано (по приоритету владельца)

| Приоритет | Пункт | Коммит | Статус |
|---|---|---|---|
| 1 | **H2/H7 fail-closed computer access** | `c843ad7` | DONE + 3 теста |
| 2 | **H3 host shell = mandatory approval** (sandbox=AUTO) | `c843ad7` | DONE + 3 теста |
| 3 | **H4 CyberSec production wiring** — канонические `ingest_guard`/`egress_guard`, IDS→RiskSignal→Policy | `70b548c` | DONE + 12 тестов |
| 4 | **H5 SAST/SCA + Dependabot** (advisory) | `85aa28b` | DONE (+ fix bandit High) |
| 5 | **H6 Vault key source/rotation** | (этот) | DONE (key-source) + 4 теста; audit-integrity отложен |

### Детали

**H2/H7 — fail-closed computer access.** `profiles.decide_device/computer_access_check`
принимают `source` (default local). Не-локальный источник (remote/telegram) без
профиля → **DENY** (раньше трактовался как «локальный хозяин» → allow). Сервис
профилей недоступен + не-локальный источник → fail-closed. Подсистема `profiles`
теперь `critical=True`: не поднялась — загрузка прерывается громко, не деградирует
в permissive. Локальный хозяин без профиля — как раньше (не режем).

**H3 — host shell approval.** `ToolDef.mandatory_confirm()` — предикат в момент
вызова, который ORится поверх confirm и **не переотменяется грантом агента**.
`run`/`tests`: `sandbox_mode != docker` → ALWAYS ASK; docker (сеть none) → AUTO;
неизвестный режим → fail-closed к ASK.

**H4 — две канонические точки.** `bossman/cybersec/guards.py`:
`ingest_guard()` (единственная граница недоверенного входа) и `egress_guard()`
(единственная граница исходящего, **fail-closed** для sensitive-каналов: секрет/
эксфильтрация → DENY; ошибка проверки sensitive-канала → HOLD). IDS не меняет
права: `ids_risk_signal()` → `RiskSignal` → `policy_recommendation()` (совет
Policy: continue/require_approval/deny). Подключено: runner ingest (внешние данные)
и runner egress (Telegram-нотификация, reference). OFF by default.

**H5 — SAST/SCA.** pip-audit + bandit (high-severity) как **advisory** шаги в обоих
CI (не роняют сборку), Dependabot (pip + github-actions, weekly). Единственный
bandit High (SHA1 в loop_guard как non-crypto fingerprint) помечен
`usedforsecurity=False`.

**H6 — Vault key source.** `BOSSMAN_VAULT_KEY` (env/внешний секрет-стор) имеет
приоритет над файлом-ключом и **не персистится на диск** — путь для ротации.
`decrypt` при несовпадении ключа больше не молчит: логирует security-warning
(маскировка возможной потери/подмены секрета устранена).

**Ротация ключа Vault (процедура):**
1. `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — новый ключ.
2. Для каждого хранимого секрета: `decrypt` старым ключом → `encrypt` новым.
3. Выставить `BOSSMAN_VAULT_KEY=<новый>`, перезапустить, убрать старый ключ.

## Осталось (следующий заход V1.1 → V1.2)

| Пункт | Что | Почему не сейчас |
|---|---|---|
| H4-egress-rest | Провести plugin HTTP POST / email / webhook через `egress_guard` | Точки в разных пакетах; теперь это **один вызов на точку** (в этом и смысл двух chokepoint'ов) — механика готова, осталось подключить |
| H4-IDS-policy | Подключить `ids_risk_signal`→Policy в computer_operator/perimeter (advisory) | Требует выбора точки в Policy; тип RiskSignal готов |
| H6-audit-integrity | Hash-chained + внешний signed anchor для security-событий | Владелец верно заметил: hash-chain без внешнего anchor не tamper-proof — это V1.2 design, не переоценивать |
| H8 | Пометить/удалить dead protective code (cybersec defender/recovery как pure-функции, V3 dead flags) | Частично: `core/db.py` удалён (opt-аудит); V3Flags и cybersec-декоративность задокументированы в FINAL_CONNECTIVITY_MATRIX |

## Тест/регресс на момент H6
```
bossman-core (живой PG 16.13): 1105 passed, 5 skipped, 0 failed
command-center:                619 passed, 2 skipped, 0 failed
secret scan: PASS · compileall: PASS
```
Известная деталь (не в этом проходе): `test_plugin_security.py::test_redact_*` —
падают ПРИ ИЗОЛИРОВАННОМ запуске (нужна регистрация generic-хендлера другим
тестом), но проходят в полном наборе. Это pre-existing хрупкость изоляции, не
регрессия; кандидат на отдельный fix (fixture-регистрация вместо порядковой зависимости).

## Оценка зрелости (по словам владельца, после H2–H5)
Базовая архитектура ~97% pre-hardware-ready; defense-in-depth поднят с ~80–85% к
~93–96% до live-hardware security acceptance. RED-vs-BLUE стресс-тест — отдельный
последующий gate, только disposable sandbox.
