---
name: web-trend-teacher
description: Autonomous Teacher loop that teaches Bossman to go out onto the live internet over HTTPS, harvest current trends across AI agents, generative video, WebGL 3D, and Python devtools, verify mastery via quiz, and commit I-1 temporal facts to FactStore.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
---

# Web Trend Teacher (Учитель Интернет-Трендов Bossman)

Use this skill whenever Bossman needs to go out onto the internet like a student guided by a Teacher, collect up-to-date trends, verify comprehension, and store self-contained temporal facts (`FactStore`).

## Workflow (`teacher.learn_web_trends`)

1. **Audit Web & Memory Readiness (`audit_web_learning_readiness`)**:
   - Inspect OSIRIS-governed HTTPS search backends (`hn-algolia`, `openalex`, `pypi`, `stackexchange`, `wikipedia-opensearch-ru`, `wikipedia-opensearch-en`).
   - Verify `StepStateGuard` (CUA-Recovery), `verify_criterion_with_proof` (AgentProofBench), and `FactStore` (`И-1` standard).
2. **Fetch Live Internet Signals Over HTTPS (`fetch_live_internet_trends`)**:
   - Poll `hn-algolia` (`search_by_date`), `openalex` (`publication_date:desc`), and `pypi` (`latest release JSON`) across 4 studio domains:
     - `ai_agents_and_llms`
     - `generative_video_and_cinema`
     - `webgl_3d_and_world_gen`
     - `python_and_devtools`
   - Scrub all external titles and notes with `pult_report.redact`.
3. **Distill Teacher Lesson Cards & Run Mastery Exam (`build_lesson_card_and_exam`)**:
   - Transform raw internet signals into `TrendLessonCard` entries with `teacher_brief_ru` and `actionable_studio_recipe_ru`.
   - Convert each trend into a self-contained `И-1` temporal fact (`15..80` words, `0` pronouns, anchored ISO date, exact proper tokens from `subject` and `object`).
   - Run the Teacher Mastery Exam (`mastery_score >= 0.85` required to pass).
4. **Verify & Commit to Temporal Memory (`run_web_trend_teacher_session`)**:
   - Enforce `StepStateGuard` before writing the curriculum digest (`<data_dir>/web_trend_teacher_runs/latest_digest.json (runtime; the repo copy is a frozen sample)`).
   - Verify the written curriculum via `verify_criterion_with_proof` (`apb-2026.10.31-trend-teacher-v1`).
   - Record verified facts in `FactStore` with `replace_current=True` so superseded trends are automatically closed with `status_ru="заменённый"`.

## Tools & API Endpoints

- Tool `teacher.learn_web_trends`: Execute a full live internet trend learning session.
- Tool `teacher.trend_status`: Inspect cumulative sessions, live web items fetched, and mastery metrics.
- Tool `teacher.recall_trends`: Recall active `trend:<domain>` facts from `FactStore`.
- REST `GET /api/web-trend-teacher/status`
- REST `GET /api/web-trend-teacher/curriculum`
- REST `POST /api/web-trend-teacher/teach`
