-- Bossman Core — схема БД `bossman` (раздел 11 ТЗ).
-- Применяется идемпотентно при старте Core (bossman/db.py).

CREATE EXTENSION IF NOT EXISTS vector;

-- Задачи: всё, что поступает из UI, голоса, Telegram, расписания или проекта.
CREATE TABLE IF NOT EXISTS tasks (
    id          BIGSERIAL PRIMARY KEY,
    agent       TEXT,                          -- NULL = Core выбирает агента сам
    source      TEXT NOT NULL DEFAULT 'ui',    -- ui | voice | telegram | schedule | project
    text        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',-- queued | running | waiting_approval | done | failed | cancelled | interrupted
    result      TEXT,
    project_id  BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

-- Прогоны: одна задача может выполняться несколько раз (retry).
CREATE TABLE IF NOT EXISTS runs (
    id                BIGSERIAL PRIMARY KEY,
    task_id           BIGINT NOT NULL REFERENCES tasks(id),
    agent             TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'running',
    steps             INT  NOT NULL DEFAULT 0,
    prompt_tokens     BIGINT NOT NULL DEFAULT 0,
    completion_tokens BIGINT NOT NULL DEFAULT 0,
    error             TEXT,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ
);

-- Каждый вызов модели: агент, алиас, дом/облако, токены по блокам, заполнение окна, кэш (10.7).
CREATE TABLE IF NOT EXISTS model_calls (
    id                BIGSERIAL PRIMARY KEY,
    run_id            BIGINT REFERENCES runs(id),
    agent             TEXT NOT NULL,
    alias             TEXT NOT NULL,
    is_cloud          BOOLEAN NOT NULL DEFAULT false,
    prompt_tokens     INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    block_tokens      JSONB,                   -- {"system":…,"refs":…,"retrieved":…,"history":…,"task":…}
    window_fill       REAL,                    -- 0..1 — заполнение реального потолка модели
    prefix_cache_hit  BOOLEAN,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Каждый вызов инструмента: аргументы, укороченный результат, кто подтвердил.
CREATE TABLE IF NOT EXISTS tool_calls (
    id             BIGSERIAL PRIMARY KEY,
    run_id         BIGINT REFERENCES runs(id),
    agent          TEXT NOT NULL,
    tool           TEXT NOT NULL,
    args           JSONB,
    result_preview TEXT,
    truncated      BOOLEAN NOT NULL DEFAULT false,
    status         TEXT NOT NULL DEFAULT 'ok', -- ok | denied | pending_approval | rejected | error
    approved_by    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Очередь подтверждений: облако, необратимые действия, merge.
CREATE TABLE IF NOT EXISTS approvals (
    id         BIGSERIAL PRIMARY KEY,
    task_id    BIGINT REFERENCES tasks(id),
    run_id     BIGINT REFERENCES runs(id),
    kind       TEXT NOT NULL,                  -- cloud | action | merge | escalate | preview_gate
    tool       TEXT,
    payload    JSONB,
    preview    TEXT,                           -- что именно уйдёт / произойдёт (видно до нажатия)
    status     TEXT NOT NULL DEFAULT 'pending',-- pending | approved | rejected | expired
    decided_by TEXT,
    decided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Журнал всех облачных вызовов: видно каждый байт, который ушёл.
CREATE TABLE IF NOT EXISTS cloud_calls (
    id                BIGSERIAL PRIMARY KEY,
    run_id            BIGINT REFERENCES runs(id),
    agent             TEXT NOT NULL,
    alias             TEXT NOT NULL,
    approval_id       BIGINT REFERENCES approvals(id),
    prompt_preview    TEXT,
    prompt_tokens     INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    approved_by       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- RAG-индекс по memory.md, журналам и сводкам (Qwen3-Embedding-0.6B → 1024).
CREATE TABLE IF NOT EXISTS agent_memory_index (
    id         BIGSERIAL PRIMARY KEY,
    agent      TEXT NOT NULL,
    path       TEXT NOT NULL,
    chunk      TEXT NOT NULL,
    embedding  vector(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Проекты (раздел 9): источник истины — state.json в папке проекта, БД — для панелей и учёта.
CREATE TABLE IF NOT EXISTS projects (
    id           BIGSERIAL PRIMARY KEY,
    slug         TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'draft',-- draft | awaiting_approval | approved | running | preview_gate | paused | done | failed
    brief        TEXT,
    budget_limit NUMERIC(12,2),               -- жёсткий лимит на проект
    spent        NUMERIC(12,2) NOT NULL DEFAULT 0,
    estimate     JSONB,                       -- оценка из плана: клипы × секунды × цена + токены
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_tasks (
    id          BIGSERIAL PRIMARY KEY,
    project_id  BIGINT NOT NULL REFERENCES projects(id),
    stage       TEXT NOT NULL,
    name        TEXT NOT NULL,
    tool        TEXT,
    model       TEXT,
    params      JSONB,
    status      TEXT NOT NULL DEFAULT 'pending', -- pending | running | done | failed | skipped | needs_approval
    attempts    INT NOT NULL DEFAULT 0,
    cost        NUMERIC(12,4) NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS artifacts (
    id              BIGSERIAL PRIMARY KEY,
    project_id      BIGINT REFERENCES projects(id),
    project_task_id BIGINT REFERENCES project_tasks(id),
    path            TEXT NOT NULL,             -- инструменты возвращают ссылки, не содержимое
    kind            TEXT NOT NULL,             -- clip | frame | audio | subtitles | doc | log | final
    meta            JSONB,                     -- размер, длительность, разрешение…
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Запись проверки каждого клипа/стыка (критерий приёмки 10).
CREATE TABLE IF NOT EXISTS qa_results (
    id              BIGSERIAL PRIMARY KEY,
    project_task_id BIGINT REFERENCES project_tasks(id),
    artifact_id     BIGINT REFERENCES artifacts(id),
    checker         TEXT NOT NULL,             -- vision_qa_local | gemini_qa | ffprobe
    verdict         TEXT NOT NULL,             -- pass | fail
    criteria        TEXT,
    notes           TEXT,
    attempt         INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tasks_status       ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_approvals_status   ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_model_calls_agent  ON model_calls(agent, created_at);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run     ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_project_tasks_proj ON project_tasks(project_id, status);
