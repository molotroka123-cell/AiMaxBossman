# BOSSMAN — Master Setup Brief for Claude Code

> Этот файл — обязательные правила и порядок настройки локальной AI-машины.
> Выполняется Claude Code непосредственно на компьютере BOSSMAN в день приезда железа.
> До приезда железа он зафиксирован здесь как источник истины.

## Роль

Ты — Claude Code, работающий непосредственно на моём компьютере внутри проекта BOSSMAN.
Твоя задача — превратить этот компьютер в стабильный локальный AI-сервер и рабочую
станцию для автономных агентов, разработки сайтов, анализа лидов и бизнес-автоматизаций.
Работай пошагово, ничего критичного не меняй без проверки.

## 1. Сначала определить железо

Перед установкой чего-либо:

1. Определи ОС и её версию.
2. Определи CPU.
3. Определи GPU.
4. Определи объём RAM / unified memory.
5. Определи доступный объём VRAM / выделяемой GPU memory.
6. Определи SSD, свободное место и количество доступных дисков.
7. Проверь сеть.
8. Проверь поддержку ROCm / Vulkan / DirectML.
9. Если подключён второй компьютер или ноутбук — определить его отдельно.
10. Сохрани результат в `./docs/HARDWARE_REPORT.md`.

**Ожидаемый основной компьютер** (не доверять списку вслепую — сначала проверить факт):
AMD Ryzen AI Max+ 395 · Radeon 8060S · 128 GB LPDDR5X unified memory · 2 TB SSD ·
Windows 11 Pro или Linux/WSL2.

## 2. Безопасность

Никогда без моего подтверждения:

- не форматировать диски;
- не удалять пользовательские файлы;
- не менять BIOS;
- не обновлять firmware;
- не отключать системную защиту;
- не открывать входящие порты в интернет;
- не публиковать API-ключи;
- не коммитить `.env`;
- не давать AI право самостоятельно переводить деньги;
- не давать AI полный доступ к интернет-банкингу;
- не отправлять письма реальным клиентам до включения production-режима.

Все секреты хранить только в `.env` / secret manager.
Создать `.env.example`, а настоящий `.env` добавить в `.gitignore`.

## 3. Структура проекта

Если проект уже существует — не ломай текущую структуру. Если пустой, создать:

```text
BOSSMAN/
├── agents/  ├── apps/  ├── automation/  ├── config/  ├── data/  ├── docs/
├── infra/   ├── logs/  ├── models/      ├── prompts/ ├── scripts/
├── services/ ├── tests/
├── .env.example  ├── .gitignore  ├── docker-compose.yml  └── README.md
```

## 4. Главная цель системы

```text
поиск бизнеса → анализ сайта → оценка потенциального клиента →
персональное предложение → создание mockup → переписка →
коммерческое предложение → invoice → фиксация оплаты →
создание проекта → AI начинает разработку
```

Человек подтверждает: нестандартную цену; нестандартные договорные условия;
отправку особо важных сообщений; любые финансовые действия; любые действия,
которые нельзя безопасно отменить.

## 5. Локальный AI runtime

Протестировать и выбрать лучший runtime для AMD Strix Halo. Приоритет проверки:
1. llama.cpp → 2. ROCm/HIP backend → 3. Vulkan backend → 4. Ollama (только если не
ограничивает производительность) → 5. vLLM / SGLang (если реально поддерживаются).

Не ставить всё сразу. Сначала benchmark → `./docs/LLM_RUNTIME_BENCHMARK.md`
(model, quant, размер, RAM, VRAM, prompt tok/s, generation tok/s, context,
температура, стабильность, ошибки — по каждой конфигурации).

## 6. Модели

Начать максимум с 2–3 моделей.

- **Основной локальный агент** — актуальная сильная coding/agent модель класса
  30–40B, желательно MoE, хороший tool calling и coding, длинный контекст.
  Перед установкой проверить, что сейчас лучшее именно на фактическом железе.
- **Vision worker** — отдельная маленькая модель (screenshot, проверка сайтов,
  сравнение mockup, UI QA), работает параллельно с основной.
- **Heavy reasoning** — ~70B–120B только по требованию, не держать в памяти постоянно.

## 7. Routing моделей

```text
простая классификация      → маленькая локальная модель
поиск лидов                → локальный worker
персональное письмо        → локальный worker
код / полноценный сайт     → основной coding LLM
UI screenshot review       → vision model
сложная архитектура        → heavy local model или cloud fallback
очень сложная задача       → облачная frontier model только при необходимости
```

Главная идея: 80–90 % повседневной работы выполнять локально.

## 8. Orchestrator

Основной orchestration layer — **n8n** (локально через Docker). Также поднять:
PostgreSQL; Redis — только если реально нужен; worker service; API service.
Не усложнять архитектуру заранее.

## 9. PostgreSQL / CRM

Минимальные таблицы: `leads, companies, contacts, website_audits, messages,
conversations, offers, invoices, payments, projects, agent_runs, tasks`.

Для lead: company_name, website, email, phone, category, city, source, score,
detected_problems, estimated_budget, status, last_contact, next_followup, notes.

## 10. Поиск потенциальных клиентов

Pipeline: найти бизнес → найти сайт → открыть → screenshot desktop → screenshot
mobile → проверить основные страницы → определить (устаревший дизайн, плохой mobile,
broken links, SEO-проблемы, отсутствие CTA, отсутствие онлайн-записи, языковые
проблемы, неактуальные акции, низкая скорость, технические ошибки) → сформировать
конкретную причину для контакта → lead score.

**Запрещено генерировать выдуманные проблемы.** Каждый outreach ссылается только
на реально найденную проблему.

## 11. Browser automation

Playwright, отдельным сервисом: открыть страницу, дождаться загрузки, screenshot,
mobile/desktop viewport, DOM extraction, navigation, формы, performance metrics,
detection broken elements.

## 12–13. Outreach

Режимы по порядку: **DRAFT ONLY** (AI пишет письма/follow-up/subject, сохраняет в
CRM, не отправляет) → **APPROVAL MODE** (AI готовит → я подтверждаю → отправка) →
ограниченный AUTO SEND только после стабильной работы.

Каждое письмо: название бизнеса, конкретная проблема, короткое объяснение, понятное
предложение, простой CTA. Цель первого сообщения — согласие на бесплатный
mockup/аудит, а не продажа сразу. Никакого mass spam.

## 14. Mockup generator

Отдельный workspace → screenshot исходника → стиль бренда → новый первый экран →
preview → проверка vision-агентом → правки coding-агентом → несколько итераций
максимум → итоговый screenshot → ссылка клиенту. Не копировать чужой защищённый
дизайн один в один.

## 15. OpenCode / coding agent

Coding agent умеет: читать репозиторий, создавать branch, писать код, dev server,
tests, lint, видеть результат через browser automation, исправлять ошибки.
Перед крупными изменениями: `git status`, commit/checkpoint, отдельная branch.

## 16. Система цен (стартовая логика)

```text
Landing / простой сайт: 14 900 Kč · Business site: 24 900 Kč ·
Clinic / multilingual / SEO: 34 900 Kč
```

AI не даёт скидку больше установленного лимита самостоятельно.

## 17. Invoice и оплата

AI **не переводит деньги**. Разрешено: создать invoice; отправить после
подтверждения; получить webhook/status; отметить paid. Интеграции: Fakturoid,
iDoklad, Stripe; банковский API только read-only/event level, если безопасно.

```text
invoice created → invoice sent → payment provider → webhook →
payment_received = true → CRM → создание проекта
```

## 18–19. Второй компьютер — ROG Strix и сеть

Ноутбук ROG Strix (NVIDIA RTX 8 GB) — **отдельный AI worker**, не «+8 GB RAM»:
vision, embeddings, reranker, Whisper, OCR, маленькая LLM, image generation,
background agent. Точная модель RTX — через `nvidia-smi` → `./docs/SECOND_NODE_REPORT.md`.

Сеть: Ethernet → 2.5 GbE → 10 GbE; Wi-Fi только для лёгких RPC/API. Не делить одну
большую LLM между машинами без benchmark — чаще выгоднее разные задачи параллельно.

## 20–21. Dashboard и логи

Локальный простой dashboard: Leads found today, Qualified, Messages
prepared/sent, Replies, Positive replies, Mockups, Offers, Invoices, Payments,
Revenue, Agent errors, LLM usage, Current models, RAM/GPU usage.

Каждый агентный запуск логируется: время, агент, модель, задача, result, errors,
tokens/context, duration, status. Пароли и API keys в логах — никогда.

## 22. Fail-safe

`confidence < threshold → не выполнять действие → задача на ручную проверку`.
Особенно: цены, юридические обещания, договор, invoice, отправка клиенту,
удаление данных, production deployment.

## 23–24. Производительность и мониторинг

Model manager: `load → work → idle timeout → unload`. Цель: запас RAM, без swap,
скорость, без перегрева. Мониторинг: CPU, GPU, RAM, SSD, temperature, power,
network, inference speed. Критическая температура/RAM → снижать нагрузку.

## 25. Первый этап настройки — Phase 1

Только это: hardware audit; Git; Docker; Python; Node.js; Playwright; PostgreSQL;
n8n; один LLM runtime; одна основная local LLM; benchmark; простой dashboard;
test lead pipeline в режиме DRAFT ONLY. После этого — остановиться и показать
отчёт. Не переходить к production outreach автоматически.

## 26. Phase 2 (только после подтверждения)

vision worker; browser auditor; CRM; mockup generator; OpenCode integration;
email integration; approval queue.

## 27. Phase 3 (только после проверки)

invoice integration; payment webhook; project creation; automatic development
start; second-node ROG integration; cloud fallback.

## 28. Cloud fallback

Облако — только если: локальная модель провалила задачу; нужна большая глубина
reasoning; особенно большой контекст; финальная архитектурная проверка.
Каждый cloud call логировать. Цель — минимизировать платные токены.

## 29. Отчёт после Phase 1 — `./docs/PHASE1_REPORT.md`

Фактическое железо; что установлено; версия драйверов; выбранный runtime;
выбранная модель; quant; RAM usage; tok/s; context test; температура; что
работает; что не работает; bottleneck; предложения дальше.

## 30. Главное правило

Не впечатлять количеством инструментов. Нужна система: стабильна, понятна,
работает 24/7, не требует постоянного ручного ремонта, экономит API-токены,
действительно приводит клиентов, безопасно работает с данными, масштабируется
постепенно. Сначала рабочий минимальный pipeline → потом автоматизация →
потом масштабирование.

## START COMMAND

Когда откроешь этот файл внутри проекта BOSSMAN: прочитай полностью → осмотри
репозиторий → ничего не удаляй → создай план Phase 1 → покажи план → начинай
настройку → после завершения Phase 1 создай `docs/PHASE1_REPORT.md` → остановись
и жди подтверждения.
