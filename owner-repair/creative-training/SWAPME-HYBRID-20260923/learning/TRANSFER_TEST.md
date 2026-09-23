# TRANSFER_TEST — hybrid video recipe, TEST Bossman (:8810)

Status: **NOT_RUN** (prepared 2026-09-23; GPU busy rendering, no LLM/Studio calls made)

## Prepared objects (TEST Bossman, http://127.0.0.1:8810)
- Agent: **id 6** "Видеопродюсер" — model_id 3 (`bossman-main-qwen38-27b-q5:latest`), tools
  `memory.search`, `memory.fact.search`, `studio.status`, max_steps 8. System prompt contains
  only the behaviour (search memory for the hybrid video recipe first; output storyboard,
  cloud/local split with reasons, budget, local preset, EDL; no diffusion typography) — **no recipe content**.
- Memory fact: **id 3** — subject "hybrid video recipe", predicate "verified workflow",
  source_kind human, source_note "SwapMe 23.09 teacher-verified run".
  Note: FactStore enforces 15–80 words per fact (bcc/v2/memory/facts.py MAX_WORDS=80), so the
  stored summary is 78 words / 435 chars (statement = object). Dropped vs the full recipe:
  grade/sound details, "weak local → change task first", 24 fps/cfg decimals, reference-still roles.
  Kept: decision rules (cloud for walking/dolly/object physics, local I2V from cloud last frame for
  close-ups/static inserts, typography via compositor), presets, $1.165/5 s, $2.33 vs $3.50, EDL pattern.

## Command the coordinator runs after restart
```
"C:\Users\asd\Bossman Test 0923\bm-teacher.cmd" exec --agent 6 --text "Подготовь другой 15-секундный hybrid promo plan для SwapMe (тема: обмен наличных на крипту за 5 минут), максимум 10 секунд cloud. Только план и EDL, без генерации." --output-format json --max-seconds 900
```

## Scoring rubric (1 point each, 7 max; item 8 is a veto)
| # | Criterion | Pass evidence |
|---|-----------|---------------|
| 1 | Recalls recipe from memory | tool trace shows `memory.fact.search` / `memory.search` returning fact#3 BEFORE the plan; plan cites it |
| 2 | Cloud/local split with reasons | every shot labelled cloud or local with a rule-based reason (motion/physics → cloud; close-up/static continuation → local I2V from cloud last frame) |
| 3 | Cost computed with $1.165 per 5 s | cloud ≤ 10 s ⇒ ≤ 2 shots ⇒ ≤ $2.33, arithmetic shown |
| 4 | Local preset | `wan2.2-ti2v-5b`, 640x1152, 24 steps (65 frames / fixed seed / I2V start frame = bonus) |
| 5 | Typography via compositor | text/logo/CTA/end card explicitly via compositor (PIL/ffmpeg), never diffusion |
| 6 | EDL present | timecoded list totalling 15.00 s, cloud total ≤ 10 s, cuts respect the cloud→local handoff |
| 7 | New creative for the new theme | storyboard is about cash → crypto in 5 minutes, not the old SwapMe storyboard |
| 8 | VETO: copies the old storyboard verbatim | = **not transfer**, score 0 regardless of other items |

Also record: run wall time, steps used (≤ 8), whether it tried `studio.generate` (not granted — must not),
and whether the JSON output was valid. Pass threshold proposed: ≥ 6/7 and no veto.

## Result
NOT_RUN
