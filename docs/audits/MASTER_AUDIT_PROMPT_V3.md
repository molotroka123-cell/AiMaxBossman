# Master Audit Prompt v3 — AiMaxBossman

**Версия:** 3.0  
**Дата:** 2026-09-07  
**Назначение:** Универсальный промт для генерации аудитов в репозитории AiMaxBossman  
**Контекст:** Не повторять предыдущие аудиты, фокусироваться на новых аспектах

---

## 📋 Шаблон Промта

```markdown
# ЗАДАЧА: Генерация аудита для AiMaxBossman

## Контекст проекта
- **Репозиторий:** https://github.com/molotroka123-cell/AiMaxBossman
- **Основная ветка:** `claude/bossman-control-v03-43igbk` (или актуальная)
- **Проект:** AiMaxBossman — система оркестрации AI-агентов с локальными LLM

## Существующие аудиты (НЕ ПОВТОРЯТЬ)

### Прочитанные аудиты:
1. `docs/audits/TOTAL_PERFORMANCE_AUDIT_PERPLEXITY_2026_09_07.md` — общий аудит производительности
2. `docs/audits/OS_LOCAL_LLM_PERFORMANCE_AUDIT_2026_09_07.md` — ОС поверх локальных LLM
3. `docs/audits/2026-08-29__ai-gateway__implementation-audit__v1.md` — AI Gateway интеграция
4. `docs/audits/2026-08-29__computer-use__implementation-audit__v1.md` — Computer Use слой
5. `docs/audits/2026-08-29__gateway-context-browser__red-team-findings__v1.md` — red-team находки
6. `docs/audits/2026-09-03__bcc-desktop-launch__audit__v1.md` — BCC desktop launch
7. `docs/audits/2026-09-03__bcc-desktop-user-perspective-full-audit__v1.md` — BCC user perspective
8. `docs/audits/AUDIT_PROMPT_v2.md` — предыдущая версия мастер-промта

### Темы, которые УЖЕ покрыты:
- ✅ Общая производительность системы
- ✅ ОС управление памятью для локальных LLM
- ✅ I/O latency и task scheduling
- ✅ AI Gateway безопасность и интеграция
- ✅ Computer Use/browser инструменты
- ✅ BCC desktop launch проблемы
- ✅ Red-team security findings
- ✅ Context management и checkpoint integrity

## 🔍 ФОКУС ЭТОГО АУДИТА

**Выбери ОДНУ из следующих тем (или предложи новую):**

### Вариант A: Local Model Intelligence Audit
- Качество генерации локальных моделей (7B/13B/70B)
- Сравнение с cloud моделями (GPT-4, Claude, etc.)
- Quantization impact на качество ответов
- Context window utilization efficiency
- Prompt engineering для локальных моделей

### Вариант B: Multi-Agent Orchestration Audit
- Эффективность коммуникации между агентами
- Resource contention и deadlock detection
- Task distribution алгоритмы
- Self-learning loop effectiveness
- Agent specialization и role assignment

### Вариант C: Security & Compliance Audit
- Secret management и token handling
- API key rotation и expiry
- Audit logging completeness
- Compliance с data residency требованиями
- Penetration testing результаты

### Вариант D: Deployment & CI/CD Audit
- Build pipeline optimization
- Test coverage и flaky tests
- Deployment rollback механизмы
- Environment parity (dev/staging/prod)
- Monitoring и alerting setup

### Вариант E: User Experience Audit
- Onboarding flow для новых пользователей
- Error messages clarity
- Performance perception (latency vs actual)
- Feature discoverability
- Accessibility compliance

### Вариант F: Hardware Optimization Audit
- GPU utilization patterns
- Memory bandwidth bottlenecks
- Thermal throttling impact
- Power efficiency metrics
- Hardware-specific optimizations (AMD NPU, etc.)

## 📐 Структура Аудита

Следуй этой структуре (адаптируй под выбранную тему):

### 1. Executive Summary
- Краткое резюме (3-5 предложений)
- Ключевые находки
- Общий статус (🟢/🟡/🔴)

### 2. Контекст и Объем
- Что аудируется
- Что НЕ входит в объем
- Связанные компоненты системы

### 3. Методология
- Как проводился аудит
- Инструменты и метрики
- Тестовые сценарии

### 4. Текущее Состояние
- Фактические метрики
- Наблюдения
- Evidence (скриншоты, логи, тесты)

### 5. Анализ и Находки
- Сильные стороны
- Слабые места
- Риски
- Возможности

### 6. Рекомендации
- Краткосрочные (1-2 недели)
- Среднесрочные (2-4 недели)
- Долгосрочные (1-2 месяца)

### 7. Технические Требования (ТЗ)
- Конкретные метрики для улучшения
- Acceptance criteria
- Timeline

### 8. Риски и Блокреры
- Технические риски
- Зависимости
- Митигация стратегии

### 9. Benchmark Suite
- Тесты для валидации
- Метрики для мониторинга
- Частота прогона

### 10. Приложения
- Ссылки на код
- Команды для воспроизведения
- Дополнительные ресурсы

## 🎯 Требования к Качеству

### Обязательно:
- [ ] Конкретные метрики (числа, проценты, время)
- [ ] Evidence для каждой находки (тесты, логи, скриншоты)
- [ ] Ссылки на相关文件 (код, документация)
- [ ] Acceptance criteria для рекомендаций
- [ ] Timeline для имплементации

### Запрещено:
- [ ] Общие фразы без evidence
- [ ] Повторение предыдущих аудитов
- [ ] Рекомендации без acceptance criteria
- [ ] Метрики без baseline и target значений
- [ ] «Вероятно」 вместо фактических данных

## 📤 Формат Вывода

### Файл:
- **Имя:** `YYYY-MM-DD__{topic}__audit__v1.md`
- **Путь:** `docs/audits/`
- **Ветка:** `{auditor}/{topic}-audit-{date}`

### Commit Message:
```
docs(audit): {краткое описание аудита}

- Фокус: {тема}
- Ключевые находки: {2-3 пункта}
- Рекомендации: {N} пунктов
- Acceptance: {критерии}
```

## 🔄 Процесс

1. **Выбор темы** — выбери фокус из списка выше или предложи новый
2. **Анализ кода** — прочитай相关文件 в репозитории
3. **Сбор метрик** — запусти тесты, собери данные
4. **Написание аудита** — следуй структуре выше
5. **Review** — проверь на полноту и конкретику
6. **Пуш** — создай ветку и запуш файл
7. **PR** — создай pull request с описанием

## 📊 Шкала Приоритетов

| Приоритет | Критерии | Пример |
|-----------|----------|--------|
| **P0 Critical** | Система не работает, данные теряются, security breach | Desktop launch fails, memory leak |
| **P1 High** | Работает, но критично degraded, данные под риском | Slow inference, context loss |
| **P2 Medium** | Работает, но есть проблемы UX/performance | High latency, confusing errors |
| **P3 Low** | Cosmetic, documentation, nice-to-have | Typos, missing examples |

## 🧪 Acceptance Test для Аудита

Перед публикацией проверь:

- [ ] Все метрики имеют baseline и target
- [ ] Каждая рекомендация имеет acceptance criteria
- [ ] Есть timeline для имплементации
- [ ] Ссылки на код рабочие
- [ ] Нет повторений с предыдущими аудитами
- [ ] Evidence для каждой находки
- [ ] Структура соблюдена
- [ ] Commit message информативный

---

## Пример Использования

```
Ты — senior engineer conducting audit для AiMaxBossman.

Следуй Master Audit Prompt v3.

Тема: **Вариант A: Local Model Intelligence Audit**

Задача:
1. Прочитай docs/audits/ чтобы не повторяться
2. Проанализируй код локальных LLM интеграций
3. Собери метрики качества генерации
4. Напиши аудит по структуре выше
5. Пуш в новую ветку

Требования:
- Конкретные метрики (perplexity, accuracy, latency)
- Сравнение 7B vs 13B vs 70B моделей
- Quantization impact analysis
- Acceptance criteria для каждой рекомендации
```

---

## Ссылки

- **Репозиторий:** https://github.com/molotroka123-cell/AiMaxBossman
- **Предыдущий промт:** `docs/audits/AUDIT_PROMPT_v2.md`
- **Список аудитов:** `docs/audits/` directory
- **Benchmark suite:** `docs/benchmark/current-scorecard.md`

---

**Версия:** 3.0  
**Следующий review:** 2026-10-07  
**Owner:** @molotroka123-cell
