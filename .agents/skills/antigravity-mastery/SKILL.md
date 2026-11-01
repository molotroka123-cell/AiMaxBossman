---
name: antigravity-mastery
description: Пакет мастерства Antigravity для Bossman — параллельный облачный ресёрч, предшаговый барьер CUA-Recovery (StepStateGuard), независимая крипто-приёмка AgentProofBench (verify_criterion_with_proof), темпоральная память стандарта И-1 (FactStore), спокойная адаптивная типографика (clamp/balance/pretty) и предполётный фильтр датасетов за $0.00.
---

# Скилл `antigravity-mastery` — Как работать по стандарту Antigravity

Используй этот скилл на каждой сложной инженерной, исследовательской или обучающей задаче:

1. **Параллельный ресёрч и верификация роем (`parallel_research_swarm`)**:
   - Независимые проверки, запросы и файлы запускай параллельно (`asyncio.gather`), распределяя нагрузку по бесплатным облачным API (`Mistral`, `Experiential Labs`, `7API`, `Outrun`) при `0%` локальной нагрузки GPU.
2. **Предшаговый барьер состояния CUA-Recovery (`StepStateGuard`)**:
   - Перед любым необратимым шагом (запись в БД, удаление временных файлов, запуск обучения) сверяй ожидаемое и наблюдаемое состояние через `StepStateGuard.verify_before_next_action`. При расхождении — автоматический откат к чекпойнту или `halt_before_irreversible`.
3. **Независимая приёмка результата AgentProofBench (`verify_criterion_with_proof`)**:
   - Никогда не верь текстовому отчёту без проверки артефакта на диске/в БД. Снимай `capture_initial_snapshot` до шага и проверяй `ExpectedState` после шага с записью `proof_sha256`.
4. **Темпоральная память стандарта И-1 (`FactStore`)**:
   - Каждый подтверждённый факт записывай самодостаточным утверждением (`15..80` слов, `0` местоимений из списка `PRONOUNS`, явная дата `YYYY-MM-DD`, `replace_current=True`).
5. **Предполётная защита бюджета на обучении (`vyber07_cybersec_preflight.py` + `training_secure_gate.py`)**:
   - Никогда не арендуй и не запускай GPU вслепую на сыром HF-репозитории: сначала за `$0.00` проверь `gated`-доступ (`HTTP 200`), отсей бинарные `.tar.gz` архивы и LFS SHA256 дубликаты (`oid`), нормализуй схему и пройди `training_secure_gate.py`.
6. **Спокойный UX и адаптивная типографика (`Modern Web Guidance`)**:
   - Используй системные токены темы (`var(--surface)`, `var(--text)`, `var(--accent)`), плавный `clamp()` + `text-wrap: balance` для заголовков, `text-wrap: pretty` + `max-width: 72ch` для текста и единую высоту интерактивных кнопок (`>= 36px..44px`).
