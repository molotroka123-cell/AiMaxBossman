# Skill: Compact

`CompactSkill` создаёт структурированный handoff длинной переписки и гидратирует его через memory plugins.

## Почему extractive-first

Свободное LLM summarization может незаметно поменять число, отрицание, версию или решение. Поэтому первая стадия копирует high-signal предложения из истории, сохраняет последние сообщения verbatim и добавляет durable memory с ID/status. Это даёт компактность без притворного обещания абсолютной lossless-компрессии.

## Handoff sections

- Active objective
- Preserved high-signal history
- Retrieved durable memory
- Recent transcript (verbatim)

## Quality checks

`recent_preserved`, `memory_provenance_preserved`, `objective_preserved`, `within_budget`, `nonempty`.

Интеграция Claude Code должна abort/fallback к большему context window, если mandatory quality check false.
