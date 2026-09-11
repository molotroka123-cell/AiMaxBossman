# 🔍 AiMaxBossman — AI Max Pro 128GB Hardware Audit

**Автор:** Perplexity AI (via GitHub MCP)  
**Дата:** 2026-09-07 06:12 CEST  
**Статус:** Готов к трекингу

---

## 📊 Аппаратная конфигурация

**AMD Ryzen AI Max Pro 395 (Strix Halo)**

| Компонент | Спецификация |
|-----------|-------------|
| **CPU** | 16 ядер / 32 потока, Zen 5, до 5.0+ GHz |
| **iGPU** | AMD Radeon 8060S, RDNA 3.5, 16 CU, до 2.4 GHz |
| **NPU** | XDNA 2, до 50 TOPS |
| **RAM** | 128 GB DDR5 (распаяно, shared с GPU) |
| **TDP** | 54-120W (configurable) |
| **ROCm** | ✅ Поддерживается (Ollama работает на AMD GPU) |

---

## 🧠 Модель Llama 3.1 70B на AI Max Pro 128GB

### Прогноз производительности

| Метрика | Значение |
|---------|----------|
| **Размер модели (Q4_K_M)** | ~40 GB |
| **Контекст 8K** | +2-4 GB KV cache |
| **Контекст 32K** | +8-12 GB KV cache |
| **Контекст 128K** | +32-40 GB KV cache (не рекомендуется) |
| **Ожидаемая скорость (8K)** | 8-15 tokens/s |
| **Ожидаемая скорость (32K)** | 5-10 tokens/s |
| **TTFT (8K)** | 2-5 с |
| **TTFT (32K)** | 5-10 с |
| **RAM usage (модель + система)** | 45-55 GB / 128 GB |
| **GPU occupancy** | ~60-70% (Radeon 8060S) |

### Сравнение с другими моделями

| Модель | Размер | Скорость | Качество | Рекомендация |
|--------|--------|----------|----------|--------------|
| **Llama 3.1 8B** | 4.7 GB | 30-40 t/s | Базовое | ✅ Для чата, простых задач |
| **Qwen 2.5 32B** | 18 GB | 15-20 t/s | Хорошее | ✅✅ Баланс скорость/качество |
| **Llama 3.1 70B** | 40 GB | 8-15 t/s | Отличное | ✅✅✅ Сложные задачи, код, анализ |
| **Llama 3.1 405B** | 230 GB | ❌ Не влезает | SOTA | ❌ Требуется remote GPU |

---

## 🎯 Сценарии использования

### 1. Чат / простые задачи

**Рекомендация:** Llama 3.1 8B или Qwen 2.5 32B

```bash
ollama pull llama3.1:8b
```

**Конфигурация:**
```bash
OLLAMA_CONTEXT_LENGTH=8192
OLLAMA_MAX_LOADED_MODELS=2
OLLAMA_NUM_PARALLEL=2
```

**Ожидаемая производительность:**
- TTFT: 200-500 мс
- Tokens/s: 20-40
- RAM: 10-25 GB

---

### 2. Кодинг / дебаг / рефакторинг

**Рекомендация:** Llama 3.1 70B

```bash
ollama pull llama3.1:70b
```

**Конфигурация:**
```bash
OLLAMA_CONTEXT_LENGTH=32768
OLLAMA_MAX_LOADED_MODELS=1
OLLAMA_NUM_PARALLEL=1
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KV_CACHE_TYPE=q8_0
```

**Ожидаемая производительность:**
- TTFT: 2-5 с
- Tokens/s: 8-15
- RAM: 45-55 GB

---

### 3. Анализ документов / RAG

**Рекомендация:** Llama 3.1 70B + 32K контекст

```bash
ollama pull llama3.1:70b
```

**Конфигурация:**
```bash
OLLAMA_CONTEXT_LENGTH=32768
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KV_CACHE_TYPE=q8_0
```

**Ожидаемая производительность:**
- TTFT: 5-10 с
- Tokens/s: 5-10
- RAM: 50-60 GB

---

## ⚙️ Ollama конфигурация для AI Max Pro 128GB

### ~/.ollama/config.json

```json
{
  "max_loaded_models": 2,
  "num_parallel": 2,
  "keep_alive": "15m"
}
```

### Переменные окружения (Windows PowerShell)

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS", "2", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "2", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "15m", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_FLASH_ATTENTION", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KV_CACHE_TYPE", "q8_0", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "32768", "User")
```

---

## 📈 Benchmark план

### 1. Базовый benchmark

```bash
ollama run llama3.1:8b "Write a 500-word essay about AI"
# Ожидай: 30-40 tokens/s

ollama run llama3.1:70b "Write a 500-word essay about AI"
# Ожидай: 8-15 tokens/s
```

### 2. Стресс-тест RAM

```bash
ollama run llama3.1:8b "Hello" &
ollama run llama3.1:70b "Hello" &
ollama ps
# Ожидай: обе модели в RAM, GPU occupancy ~80%
```

### 3. Контекст тест

```bash
ollama run llama3.1:70b --num_ctx 8192 "Summarize this 5000-word text..."
# Ожидай: 10-15 tokens/s

ollama run llama3.1:70b --num_ctx 32768 "Summarize this 20000-word text..."
# Ожидай: 5-10 tokens/s
```

### 4. GPU residency check

```bash
ollama ps
# Ожидай: Processor: GPU (не CPU)
# VRAM: 40-50 GB для 70B
```

---

## 🎯 Итоговая рекомендация

**Llama 3.1 70B на AI Max Pro 128GB:**

✅ **Да, это умная модель для твоего железа:**
- Влезает в 128 GB RAM (40 GB модель + 8-12 GB контекст + 40 GB система)
- Работает на GPU (Radeon 8060S, ROCm)
- 8-15 tokens/s — достаточно для чата, кодинга, анализа
- Качество 70B >> 8B/32B для сложных задач

⚠️ **Но:**
- Не ставь контекст >32K (RAM уйдёт в KV cache)
- Не запускай 2×¦70B одновременно (RAM не хватит)
- TTFT 2-5 с — нормально для 70B, но не для чата

**Оптимальная стратегия:**
- **Чат / простые задачи:** Llama 3.1 8B (30-40 t/s)
- **Кодинг / анализ:** Llama 3.1 70B (8-15 t/s)
- **RAG / документы:** Llama 3.1 70B + 32K контекст (5-10 t/s)

---

## 📚 Источники

- `REAL_WORKLOAD_HARDWARE_AUDIT.md`
- `LOCAL_MODEL_STRIX_HALO_2026-09-07.md`
- `MASTER_PROMPT_BENCHMARK_ENGINE.md`
- `LOCAL_MODEL_ORCHESTRATION_TZ.md`
- Ollama documentation

---

*Аудит сгенерирован автоматически Perplexity AI через GitHub MCP 2026-09-07.*
