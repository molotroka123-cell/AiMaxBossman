# Рой исследований 2026-09-23 — сводка (8 линий, строгий отбор)

Облако: huggingface.co, arxiv.org, pirateface.co, artificialanalysis.ai, лидерборды MERA/BenCzechMark и др. —
**BLOCKED** (403 / EGRESS_BLOCKED). Открывался в основном raw.githubusercontent.com. Метки: **VERIFIED** (прочитано
сейчас), **SNIPPET** (только поисковая выдача), **RECALLED** (память). Ни одна модель не запускалась, ни одна
скорость на машине владельца не измерена. Датасеты и скилы — отдельный файл `R4_R5_R7_datasets_skills.md`.

## Итог в одной таблице
| Направление | Выбор | Статус доказательств |
|---|---|---|
| OCR/документы | **PaddleOCR-VL-1.6** (0.9B, Apache-2.0, GGUF, cs+ru в списке языков) | OmniDocBench 96.34, MDPBench RU 75.1 — VERIFIED (GitHub) |
| Unlimited-OCR (ссылка владельца) | **не брать** | pirateface.co — неофициальная копия; оригинал github.com/baidu/Unlimited-OCR (MIT); MDPBench RU **36.8**; в llama.cpp его R-SWA отключён |
| Кодинг, основной | **GPT-OSS-120B — KEEP** | 7/7 на насыщенном бейк-оффе; Apache-2.0 VERIFIED |
| Кодинг, претенденты | **Qwen3.8-Flash-Next** (UD-IQ4_XS ~93.7 ГБ), **Qwen3-Coder-Next** (IQ4_XS ~42.7 ГБ, ~62 т/с на Linux) | размеры/скорость VERIFIED по стороннему гайду (Linux); баллы SWE — SNIPPET |
| Tool calling | Qwen3.5-122B-A10B (кандидат), для скорости Qwen3.6-35B-A3B / GPT-OSS | все баллы BFCL/τ² для моделей ≤128 ГБ — SNIPPET; независимые таблицы их не содержат |
| Чешский/русский текст | INSUFFICIENT_EVIDENCE по рейтингам; кандидаты Gemma 4 31B, Qwen3.x, GigaChat3-10B (MIT), EuroLLM-22B | лидерборды заблокированы; нужен свой набор форм |
| Фото (чистая лицензия) | Qwen-Image-2512 + Qwen-Image-Edit-2511, Z-Image-Turbo, FLUX.2 klein 4B (все Apache-2.0) | лицензии VERIFIED; места на аренах SNIPPET |
| Видео | **LTX-2.5** (лицензия принимается владельцем, без исключения ЕС), **Wan2.2** (Apache-2.0) | лицензии VERIFIED |
| Локальное дообучение | Windows: **нет** (ROCm Windows без training — SNIPPET); Linux: LoRA 27B возможна, ~4 дня/прогон | гайд strix-halo-llm-finetune VERIFIED |

## Запреты по лицензиям для владельца в Чехии
* **MiniMax H3** — территория лицензии исключает ЕС (несколько SNIPPET, LICENSE на HF заблокирован) → не использовать.
* **HunyuanVideo 1.5** — «DOES NOT APPLY IN THE EUROPEAN UNION» (VERIFIED) → не использовать.
* MiniMax-M2.7, Ideogram 4, FLUX.2 dev, APIGen-MT (CC-BY-NC) — только некоммерчески и после явного согласия.

## Поправки к нашим модельным профилям (`tools/model_profiles.json`)
* **Qwen3.8-Flash-Next**: по R2 поддержка влита в основной llama.cpp 27.08.2026 (PR #27742, VERIFIED), MTP ещё WIP.
  Запись «нужен форк» устарела; входит ли PR в b10964 — проверить `llama-server --version` + загрузкой.
* **GLM-5.3-Flash**: PR поддержки в llama.cpp не влиты (VERIFIED) → REJECT до слияния; профиль остаётся optional.
* **SnowLLM 0.3.2**: R2 не подтвердил существование модели/версии; в профиле UNPINNED с условиями — не качать до подтверждения.
* **Qwen3.6-35B-A3B FP8**: FP8 не формат GGUF для llama.cpp → для этого рантайма нужен GGUF (Q4_K_M ~21 ГБ).
* **Qwen3.8-27B у владельца ~11.8 т/с против ~20 т/с в стороннем замере** — разрыв не объяснён, стоит проверить флаги.

## Правила настройки sidecar/llama.cpp из R3 (исходники llama.cpp — VERIFIED)
1. Только родной chat template; при старте проверять `/props` и один эталонный запрос с tools.
2. В цикле `tool_choice: "required"` (finish — тоже инструмент) → внесено в `local_sidecar` (с откатом на `auto`).
3. `response_format`/`json_schema` — только поддерживаемое подмножество; перепроверять на стороне Bossman.
4. Reasoning-модели: рассуждение в `reasoning_content`; проба с маленьким `max_tokens` даёт пустой content (наш фикс d6e25fb4).
5. KV-кэш не ниже q8_0: сильная квантизация KV портит tool calling.

## Бейк-офф на машине владельца (после OWNER_RUN)
Насыщенный набор A–G (7/7) моделей больше не различает: добавить 5–10 задач построже (многофайловый фикс с
pytest в репозитории Bossman, восстановление после ошибки инструмента, контекст ≥32K), 3 прогона на модель,
критерий замены GPT-OSS: выше pass rate при tg ≥ 25 т/с без DeviceLost/свопа.
