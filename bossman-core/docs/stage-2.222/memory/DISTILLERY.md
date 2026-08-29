# Knowledge Distillery

Distillery работает в несколько уровней: raw → extracted candidates → project summary → agent-specific memory → global reusable memory. Stage 2.222 включает conservative rule extractor, который создаёт candidates и никогда сам не делает их authoritative.

Позже Claude может подключить локальную LLM для более качественного extraction, но LLM output проходит schema validation, provenance validation, contradiction scan и golden-test regression gate.

Ночной distillation не должен запускаться, если RAM/disk pressure высокое. Он обязан быть pauseable и checkpointed.
