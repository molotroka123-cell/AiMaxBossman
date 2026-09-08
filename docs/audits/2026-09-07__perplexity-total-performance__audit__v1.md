# 🔍 AiMaxBossman — Тотальный аудит производительности и анализ «умности" локальных моделей

**Автор:** Perplexity AI (Sonar Pro / Llama 3.1 70B via GitHub MCP)  
**Дата:** 2026-09-07 06:17 CEST  
**Статус:** ВХОДНОЙ АУДИТ / НЕ РЕАЛИЗОВАНО
**Исходный commit:** `ddea21112f89c978df50aae8948c5955d7dada2e`

> Важное уточнение lane-интегратора: численные значения startup/RAM/CPU/tokens/s и прогнозы прироста ниже считаются гипотезами до воспроизводимого baseline на exact SHA и целевой машине. Некоторые предлагаемые фиксы (например Gateway reuse) уже могут быть реализованы в текущем коде и должны быть перепроверены перед изменениями. Сам аудит сохранён без превращения прогнозов в факты.

---

## 📊 Executive Summary

**Главный вывод:** Bossman не делает модели «умнее" на уровне архитектуры — он делает их **эффективнее на 40-60%** за счёт:
1. Умного контекста (retrieval, summarisation, skill selection)
2. Оптимизированного inference (KV cache, batching, GPU residency)
3. Снижения overhead (gateway reuse, lazy loading, connection pooling)

**Заявленный аудитом прирост «умности":** 15-25% за счёт лучшего контекста, не за счёт модели. **Не измерено на текущем Bossman.**

---

## 🎯 Часть 1: Аудит производительности

### 1.1 Оценочное состояние (аудит ссылался на SHA `b57b4ce`)

| Компонент | Оценка аудита | Статус в master plan |
|-----------|--------|---------|
| Startup cold | 8-12 с | NEEDS_MEASUREMENT |
| Startup warm | 3-5 с | NEEDS_MEASUREMENT |
| Idle CPU | 15-20% | NEEDS_MEASUREMENT |
| Idle RAM | 3-4 GB | NEEDS_MEASUREMENT |
| Model 8B | 30-40 t/s | HARDWARE/MODEL DEPENDENT |
| Model 70B | 8-15 t/s | HARDWARE/MODEL DEPENDENT |
| UI jank | 20-30% | NEEDS_MEASUREMENT |
| Video export | ~1x realtime | NEEDS_MEASUREMENT |

### 1.2 Кандидаты на узкие места

1. eager startup / импорт и инициализация необязательных подсистем;
2. Command Center polling + широкие refresh/re-render;
3. connection setup/transport overhead — **перепроверить: текущий Gateway уже имеет reusable AsyncClient**;
4. FFmpeg contention с интерактивной работой;
5. Ollama residency/context/KV-cache tuning;
6. отсутствие lazy loading необязательных компонентов;
7. sync telemetry/write amplification вне обязательной truth durability;
8. лишние prompt/skill/tool tokens;
9. лишние expensive observations в Computer Use.

---

## 🚀 Предложения исходного аудита, перенесённые в backlog

### A. Lazy startup
Измерить import/startup phases и lazy-load только доказанно необязательные UIA/media/Fleet/capability процессы после UI_READY.

### B. Model residency / Ollama tuning
Тестировать `keep_alive`, max loaded models, parallelism, Flash Attention, KV cache **только A/B на одном model/quant/context/hardware**. Не применять универсальные значения вслепую.

### C. Media admission
Разделить preview и batch export, ограничить конкуренцию по измеренному CPU/GPU/disk pressure, сохранив responsive Stop/Pause и корректность файлов.

### D. Command Center refresh
Сначала измерить request rate, payload size, script/render cost и DOM mutations. Затем adaptive polling/deltas/event-driven updates + periodic reconciliation.

### E. Telemetry
Батчить только non-authoritative telemetry. TaskJournal/evidence/effect-boundary durability нельзя переводить в lossy queue ради скорости.

### F. Skill/tool context selection
Передавать только релевантные инструкции и схемы, измеряя tokens/TTFT и held-out quality. Policy/evidence context не урезать.

### G. Observation delta/reuse
Продолжить уже начатую оптимизацию Computer Use только там, где freshness/effect-boundary tests доказывают безопасность. Stale-state completion недопустим.

---

## Что master plan отклоняет как недоказанное

- «40–60% ускорение за 2–3 часа» — прогноз, не release claim.
- «idle Bossman = 3–4 GB» — пока не измерено на полном process tree.
- «Gateway создаёт новый клиент на каждый запрос» — на рассмотренной актуальной линии это уже не соответствует GatewayClient; проверять конкретные другие HTTP paths отдельно.
- «все Linux Chromium не умеют H264/AAC» — нельзя обобщать одну CI-сборку на все браузеры.
- «Bossman даёт +30–40% интеллекта / GPT-4 уровень» — без paired same-model evaluation это маркетинговая гипотеза.

Полный авторский исходник сохранён в commit `ddea211...`; этот файл — нормализованная копия для общего optimization lane с маркировкой доказанности.
