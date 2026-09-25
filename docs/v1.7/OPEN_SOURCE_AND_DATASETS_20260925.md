# Bossman 1.7 PIT — OSS и датасеты

Дата: 2026-09-25. Это shortlist механизмов и benchmark-источников. Он не заменяет существующий Bossman backend.

## Архитектура

Graphiti: temporal facts, provenance, source episodes, valid/invalid windows, hybrid retrieval.
LangMem: memory extraction отдельно от ответа, background consolidation, prompt refinement.
Mem0: per-user memory scoping и retrieval only of relevant facts.
Letta: persistent memory blocks и model-independent state как reference.
DSPy/GEPA: shadow optimization prompts с train/holdout метрикой.

## Персонализация

LaMP: основной benchmark profile retrieval и personalized generation/classification.
LongLaMP: personalized long-form generation.
PRISM: stated preferences и ratings реальных LLM interactions для research/evaluation individual differences.
PersonaHub: synthetic personas только для масштабных isolation/collision/cold-start тестов.

## Общий quality/ranking

HelpSteer2: human ratings по helpfulness, correctness, coherence, complexity, verbosity.
OASST2: multilingual conversation trees для replay/follow-up tests.
SHP-2: миллионы collective response preferences для generic ranker.
UltraFeedback: synthetic scored completions только как baseline evaluator.

## Training ladder

0. High-recall collection, retrieval и outcome labels без обучения весов.
1. Garbage sorter: KEEP / DROP / TTL / MERGE / SUPERSEDE.
2. Personalized retrieval/ranking: LaMP/LongLaMP + own holdout.
3. Prompt optimization через DSPy/GEPA в shadow mode.
4. Optional LoRA/QLoRA на поведении персонализации, не на сырых фактах конкретных людей.
5. Transfer test на новых добровольных пользователях.

Train/eval делить group-by-user. Один и тот же человек не должен одновременно давать train и независимый transfer result.

Точные revisions записаны в docs/v1.7/contracts/source-refs.json. Источник с непроверенной лицензией остаётся research-reference до отдельной проверки.