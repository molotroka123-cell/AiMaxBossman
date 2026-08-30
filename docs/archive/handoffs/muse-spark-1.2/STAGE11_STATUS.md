# STAGE 11 STATUS

HEAD: f2ce086 + stage11-коммиты (см. git log)
BRANCH: claude/bossman-control-v03-43igbk

## DONE
- `bossman/ai_lab/`: sanitizer (SANITIZER_VERSION="ailab-sanitize-1", PII-подобные: email/IP/hex/b64/phone поверх obs.redact_obj), CandidateStore (raw→sanitize→validate→candidate, DatasetGate Stage 8 переиспользован), EvalRunner (bounded, Resource Brain admission, cap 50), Exporter (SFT JSONL + DPO pairs, provenance в каждом образце), LocalTrainingAdapter (OFF by default, owner-approval обязателен)
- API `/api/lab/{trajectories,candidates,evals,exports}` — read-only raw; без мутаций cloud_policy/агентов/провайдеров; зарегистрирован в `_include_stage_routers`

## VERIFIED (18 тестов, `tests/test_stage11_ai_lab.py`)
- raw никогда не становится training-данными напрямую (NOT_FOUND без candidate+gate)
- секреты/PII вычищены (Bearer-ключ, api_key, password, email, IP, hex — не в сэмплах)
- provenance на каждом экспортируемом образце (источник+sha256+sanitizer+validator+approval)
- дубликат кандидата (source_sha256, sanitizer_version) → CONFLICT
- отзыв approval → REJECTED → export denied
- malicious/empty/failed/oversized/injection → отклонены по умолчанию; raw иммутабелен
- export schema SFT (messages+provenance) и DPO (chosen≠rejected+provenance)
- Resource denial: без аренды — отказ ДО первого вызова модели (0 звонков)
- bounded eval: 200 кейсов → POLICY_DENIED; max_cases=3 → ровно 3 вызова
- TrainingDisabled по умолчанию; без owner-approval — APPROVAL_REQUIRED

## NOT VERIFIED / BLOCKED BY HOST
- Реальный GPU fine-tuning — адаптер намеренно OFF (решение владельца)
- Live eval через настоящую модель (нет ключа/модели в этой сессии)

## OPEN P0
- нет

## OPEN P1
- candidates.json — JSON-файл, не Postgres (объёмы малые; перенос при необходимости)
