"""Схема базы данных и миграции.

Спека даёт список таблиц и колонок, но ни одного типа, почти ни одного
ограничения и никакого DDL (`DIGEST_CORE` G1). Типы, внешние ключи и индексы
выбраны здесь и обоснованы в `docs/PRE_IMPLEMENTATION_AUDIT.md` §5.

Отличия от списка в спеке — все намеренные и все перечислены:

* `approvals` получили `content_revision_id`, `approved_content_hash`,
  `capability`, `account_id`, `policy_version`. Без них требование «одобрение
  привязано к хешу ревизии» неисполнимо: сравнивать не с чем (решение C5).
* `content_revisions` получили `asset_ids`, `target_account_ids`,
  `schedule_at` — конвейер требует, чтобы ревизия несла состав, а хранить его
  было негде (C4).
* `policy_rules` получили `enabled`, `priority`, `hard_deny` (C8, G11).
* Добавлены таблицы, без которых объявленное поведение нереализуемо (G13):
  `outbox` (транзакционная отправка), `published_media` (шаг 13 конвейера),
  `sync_cursors`, `rate_limit_state`, `health_snapshots`, `actors`.

`audit_events` защищена триггерами от UPDATE и DELETE. Правило «строки аудита
не изменяются» словами не держится: его держит база (G19).
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA_VERSION = 1

# ---------------------------------------------------------------- v1

_V1 = """
-- Кто действует. На эту таблицу ссылаются approvals, ревизии, профили и аудит.
CREATE TABLE IF NOT EXISTS actors (
    id            TEXT PRIMARY KEY,
    type          TEXT NOT NULL CHECK (type IN ('HUMAN','AGENT','SYSTEM')),
    display_name  TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_accounts (
    id                        TEXT PRIMARY KEY,
    provider                  TEXT NOT NULL,
    provider_account_id       TEXT NOT NULL,
    handle                    TEXT NOT NULL DEFAULT '',
    account_type              TEXT NOT NULL DEFAULT '',
    -- ССЫЛКА на секрет, а не секрет. Значения токенов в доменной базе нет.
    auth_ref                  TEXT NOT NULL DEFAULT '',
    browser_session_ref       TEXT NOT NULL DEFAULT '',
    timezone                  TEXT NOT NULL DEFAULT 'UTC',
    locale                    TEXT NOT NULL DEFAULT '',
    status                    TEXT NOT NULL DEFAULT 'PENDING_CONNECT',
    policy_profile_id         TEXT,
    generation_profile_id     TEXT,
    capability_snapshot_version INTEGER NOT NULL DEFAULT 0,
    health_state              TEXT NOT NULL DEFAULT 'UNKNOWN',
    adapter_preference        TEXT NOT NULL DEFAULT 'OFFICIAL_ONLY'
                              CHECK (adapter_preference IN
                                     ('OFFICIAL_ONLY','OFFICIAL_THEN_BROWSER','BROWSER_ONLY')),
    late_publish_behavior     TEXT NOT NULL DEFAULT 'ASK'
                              CHECK (late_publish_behavior IN ('PUBLISH_LATE','SKIP','ASK')),
    media_profile_id          TEXT,
    rate_limit_bucket_id      TEXT,
    webhook_subscription_state TEXT NOT NULL DEFAULT 'NONE',
    last_sync_at              TEXT,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL,
    disabled_at               TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_accounts_provider_id
    ON social_accounts (provider, provider_account_id);

CREATE TABLE IF NOT EXISTS capability_snapshots (
    id                   TEXT PRIMARY KEY,
    account_id           TEXT NOT NULL REFERENCES social_accounts (id) ON DELETE RESTRICT,
    version              INTEGER NOT NULL,
    provider_api_version TEXT,
    adapter_version      TEXT NOT NULL DEFAULT '',
    observed_at          TEXT NOT NULL,
    expires_at           TEXT,
    raw_ref              TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_snapshot_account_version
    ON capability_snapshots (account_id, version);

CREATE TABLE IF NOT EXISTS capabilities (
    snapshot_id                 TEXT NOT NULL REFERENCES capability_snapshots (id)
                                ON DELETE CASCADE,
    name                        TEXT NOT NULL,
    status                      TEXT NOT NULL,
    source                      TEXT NOT NULL DEFAULT '',
    reason                      TEXT,
    permission_requirements_json TEXT NOT NULL DEFAULT '[]',
    metadata_json               TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (snapshot_id, name)
);

CREATE TABLE IF NOT EXISTS policy_profiles (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    version      INTEGER NOT NULL DEFAULT 1,
    active       INTEGER NOT NULL DEFAULT 1,
    created_by   TEXT REFERENCES actors (id),
    created_at   TEXT NOT NULL,
    effective_at TEXT
);

CREATE TABLE IF NOT EXISTS policy_rules (
    id             TEXT PRIMARY KEY,
    profile_id     TEXT NOT NULL REFERENCES policy_profiles (id) ON DELETE CASCADE,
    scope          TEXT NOT NULL CHECK (scope IN ('SYSTEM','PROVIDER','ACCOUNT','ACTION')),
    provider       TEXT,
    account_id     TEXT REFERENCES social_accounts (id) ON DELETE RESTRICT,
    capability     TEXT NOT NULL,
    decision       TEXT NOT NULL CHECK (decision IN ('AUTO','ASK','DENY')),
    condition_json TEXT NOT NULL DEFAULT '{}',
    hard_deny      INTEGER NOT NULL DEFAULT 0,
    enabled        INTEGER NOT NULL DEFAULT 1,
    priority       INTEGER NOT NULL DEFAULT 0,
    rule_order     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rules_profile ON policy_rules (profile_id, capability);

CREATE TABLE IF NOT EXISTS content_projects (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL DEFAULT '',
    brief               TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'DRAFT',
    current_revision_id TEXT,
    created_by          TEXT REFERENCES actors (id),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_revisions (
    id                     TEXT PRIMARY KEY,
    project_id             TEXT NOT NULL REFERENCES content_projects (id) ON DELETE CASCADE,
    revision_no            INTEGER NOT NULL,
    caption                TEXT NOT NULL DEFAULT '',
    -- состав ревизии: без него одобрение не к чему привязывать (решение C4)
    asset_ids              TEXT NOT NULL DEFAULT '[]',
    target_account_ids     TEXT NOT NULL DEFAULT '[]',
    schedule_at            TEXT,
    metadata_json          TEXT NOT NULL DEFAULT '{}',
    content_hash           TEXT NOT NULL,
    approved_at            TEXT,
    approved_by            TEXT REFERENCES actors (id),
    supersedes_revision_id TEXT REFERENCES content_revisions (id),
    created_at             TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_revision_project_no
    ON content_revisions (project_id, revision_no);

CREATE TABLE IF NOT EXISTS media_assets (
    id              TEXT PRIMARY KEY,
    project_id      TEXT REFERENCES content_projects (id) ON DELETE SET NULL,
    parent_asset_id TEXT REFERENCES media_assets (id),
    type            TEXT NOT NULL,
    mime            TEXT NOT NULL DEFAULT '',
    checksum_sha256 TEXT NOT NULL,
    byte_size       INTEGER NOT NULL DEFAULT 0,
    width           INTEGER,
    height          INTEGER,
    duration_ms     INTEGER,
    codec_json      TEXT NOT NULL DEFAULT '{}',
    source_type     TEXT NOT NULL DEFAULT 'UPLOAD'
                    CHECK (source_type IN ('UPLOAD','GENERATED','DERIVED','PROVIDER')),
    source_provider TEXT,
    storage_ref     TEXT NOT NULL,
    immutable       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assets_checksum ON media_assets (checksum_sha256);

CREATE TABLE IF NOT EXISTS generation_jobs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES content_projects (id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    provider    TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT 'QUEUED',
    input_ref   TEXT,
    output_ref  TEXT,
    cost_json   TEXT NOT NULL DEFAULT '{}',
    attempts    INTEGER NOT NULL DEFAULT 0,
    started_at  TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS social_jobs (
    id                  TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL REFERENCES social_accounts (id) ON DELETE RESTRICT,
    capability          TEXT NOT NULL,
    state               TEXT NOT NULL DEFAULT 'DRAFT',
    adapter_preference  TEXT NOT NULL DEFAULT 'OFFICIAL_ONLY',
    payload_ref         TEXT,
    content_revision_id TEXT REFERENCES content_revisions (id),
    idempotency_key     TEXT NOT NULL,
    policy_profile_id   TEXT REFERENCES policy_profiles (id),
    policy_version      INTEGER NOT NULL DEFAULT 0,
    approval_id         TEXT,
    schedule_at         TEXT,
    not_before          TEXT,
    deadline            TEXT,
    lease_owner         TEXT,
    lease_expires_at    TEXT,
    checkpoint          TEXT,
    attempts            INTEGER NOT NULL DEFAULT 0,
    -- скаляр; сами идентификаторы объектов — в external_effects (решение C2)
    external_state      TEXT NOT NULL DEFAULT 'NONE'
                        CHECK (external_state IN ('NONE','UNKNOWN','CONFIRMED','ABSENT')),
    last_error_json     TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    terminal_at         TEXT
);
-- Тот же ключ на том же аккаунте — то же намерение. Это и есть защита от
-- второй публикации: она стоит в базе, а не в коде вызывающего.
CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_account_idem
    ON social_jobs (account_id, idempotency_key);
CREATE INDEX IF NOT EXISTS ix_jobs_ready ON social_jobs (state, not_before);
CREATE INDEX IF NOT EXISTS ix_jobs_account_state ON social_jobs (account_id, state);
CREATE INDEX IF NOT EXISTS ix_jobs_lease ON social_jobs (lease_expires_at);

CREATE TABLE IF NOT EXISTS job_attempts (
    id                  TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL REFERENCES social_jobs (id) ON DELETE CASCADE,
    attempt_no          INTEGER NOT NULL,
    adapter             TEXT NOT NULL DEFAULT '',
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    result_class        TEXT,
    retry_class         TEXT,
    provider_request_id TEXT,
    redacted_error_ref  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_attempt_job_no ON job_attempts (job_id, attempt_no);

CREATE TABLE IF NOT EXISTS external_effects (
    id                 TEXT PRIMARY KEY,
    job_id             TEXT NOT NULL REFERENCES social_jobs (id) ON DELETE RESTRICT,
    provider           TEXT NOT NULL,
    effect_type        TEXT NOT NULL,
    provider_object_id TEXT,
    idempotency_key    TEXT NOT NULL DEFAULT '',
    state              TEXT NOT NULL DEFAULT 'UNKNOWN'
                       CHECK (state IN ('UNKNOWN','CONFIRMED','ABSENT')),
    observed_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_effects_job ON external_effects (job_id);

CREATE TABLE IF NOT EXISTS published_media (
    id                  TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL REFERENCES social_jobs (id) ON DELETE RESTRICT,
    account_id          TEXT NOT NULL REFERENCES social_accounts (id) ON DELETE RESTRICT,
    content_revision_id TEXT REFERENCES content_revisions (id),
    provider_object_id  TEXT NOT NULL,
    published_at        TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_published_provider_object
    ON published_media (account_id, provider_object_id);

CREATE TABLE IF NOT EXISTS approvals (
    id                    TEXT PRIMARY KEY,
    job_id                TEXT NOT NULL REFERENCES social_jobs (id) ON DELETE CASCADE,
    status                TEXT NOT NULL DEFAULT 'PENDING'
                          CHECK (status IN ('PENDING','APPROVED','REJECTED','EXPIRED')),
    -- привязка к точной ревизии и её хешу (решение C5)
    content_revision_id   TEXT REFERENCES content_revisions (id),
    approved_content_hash TEXT NOT NULL DEFAULT '',
    capability            TEXT NOT NULL DEFAULT '',
    account_id            TEXT REFERENCES social_accounts (id),
    policy_version        INTEGER NOT NULL DEFAULT 0,
    preview_ref           TEXT,
    requested_at          TEXT NOT NULL,
    expires_at            TEXT,
    decided_at            TEXT,
    actor_id              TEXT REFERENCES actors (id),
    decision_note         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_approvals_status ON approvals (status, expires_at);

-- Транзакционная отправка: строка пишется в ТОЙ ЖЕ транзакции, что и
-- изменение домена. «Записали в базу, потом позвали очередь и понадеялись» —
-- ровно тот способ потерять работу или сделать её дважды (53_EVENT_OUTBOX_INBOX).
CREATE TABLE IF NOT EXISTS outbox (
    id            TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    payload_json  TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    lease_owner   TEXT,
    lease_expires_at TEXT,
    dispatched_at TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_outbox_pending ON outbox (dispatched_at, created_at);

CREATE TABLE IF NOT EXISTS webhook_receipts (
    id                TEXT PRIMARY KEY,
    provider          TEXT NOT NULL,
    received_at       TEXT NOT NULL,
    signature_valid   INTEGER NOT NULL DEFAULT 0,
    provider_event_id TEXT,
    dedup_key         TEXT NOT NULL,
    payload_ref       TEXT,
    processed_at      TEXT
);
-- Частичный уникальный индекс: при отсутствующем идентификаторе события
-- обычное ограничение не работало бы вовсе (G17).
CREATE UNIQUE INDEX IF NOT EXISTS ux_receipt_provider_event
    ON webhook_receipts (provider, provider_event_id)
    WHERE provider_event_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_receipt_dedup
    ON webhook_receipts (provider, dedup_key);

CREATE TABLE IF NOT EXISTS provider_events (
    id                TEXT PRIMARY KEY,
    account_id        TEXT REFERENCES social_accounts (id) ON DELETE RESTRICT,
    provider          TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    provider_event_id TEXT,
    dedup_key         TEXT NOT NULL,
    event_time        TEXT,
    payload_ref       TEXT,
    normalized_json   TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_provider_events_dedup
    ON provider_events (provider, dedup_key);

CREATE TABLE IF NOT EXISTS conversations (
    id                       TEXT PRIMARY KEY,
    account_id               TEXT NOT NULL REFERENCES social_accounts (id) ON DELETE RESTRICT,
    provider_conversation_id TEXT NOT NULL,
    state                    TEXT NOT NULL DEFAULT 'OPEN'
                             CHECK (state IN ('OPEN','WAITING_US','WAITING_EXTERNAL',
                                              'RESOLVED','ARCHIVED')),
    last_message_at          TEXT,
    eligibility_json         TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_conversation_provider
    ON conversations (account_id, provider_conversation_id);

CREATE TABLE IF NOT EXISTS messages (
    id                  TEXT PRIMARY KEY,
    conversation_id     TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    provider_message_id TEXT,
    direction           TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
    sender_ref          TEXT NOT NULL DEFAULT '',
    body_ref            TEXT,
    media_json          TEXT NOT NULL DEFAULT '[]',
    received_at         TEXT,
    sent_at             TEXT,
    status              TEXT NOT NULL DEFAULT 'RECEIVED'
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_message_provider
    ON messages (conversation_id, provider_message_id)
    WHERE provider_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS comments (
    id                  TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL REFERENCES social_accounts (id) ON DELETE RESTRICT,
    media_provider_id   TEXT NOT NULL DEFAULT '',
    provider_comment_id TEXT NOT NULL,
    parent_comment_id   TEXT,
    author_ref          TEXT NOT NULL DEFAULT '',
    body_ref            TEXT,
    status              TEXT NOT NULL DEFAULT 'VISIBLE'
                        CHECK (status IN ('VISIBLE','HIDDEN','DELETED','REPLIED')),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_comment_provider
    ON comments (account_id, provider_comment_id);

CREATE TABLE IF NOT EXISTS insights (
    id                   TEXT PRIMARY KEY,
    account_id           TEXT NOT NULL REFERENCES social_accounts (id) ON DELETE RESTRICT,
    media_ref            TEXT,
    metric               TEXT NOT NULL,
    period               TEXT NOT NULL DEFAULT '',
    start_at             TEXT,
    end_at               TEXT,
    value_json           TEXT NOT NULL DEFAULT '{}',
    provider_api_version TEXT,
    collected_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_insights_account ON insights (account_id, metric, collected_at);

CREATE TABLE IF NOT EXISTS sync_cursors (
    account_id TEXT NOT NULL REFERENCES social_accounts (id) ON DELETE CASCADE,
    stream     TEXT NOT NULL,
    cursor     TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, stream)
);

CREATE TABLE IF NOT EXISTS rate_limit_state (
    bucket        TEXT PRIMARY KEY,
    account_id    TEXT REFERENCES social_accounts (id) ON DELETE CASCADE,
    state         TEXT NOT NULL DEFAULT 'NORMAL',
    remaining     INTEGER,
    reset_at      TEXT,
    cooldown_until TEXT,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS health_snapshots (
    id          TEXT PRIMARY KEY,
    account_id  TEXT REFERENCES social_accounts (id) ON DELETE CASCADE,
    component   TEXT NOT NULL,
    state       TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS browser_sessions (
    id                    TEXT PRIMARY KEY,
    account_id            TEXT NOT NULL REFERENCES social_accounts (id) ON DELETE RESTRICT,
    -- ССЫЛКА на контекст, а не cookie. Cookie в доменной базе не хранятся.
    session_ref           TEXT NOT NULL DEFAULT '',
    state                 TEXT NOT NULL DEFAULT 'NEW',
    selector_pack_version TEXT NOT NULL DEFAULT '',
    last_verified_at      TEXT,
    last_takeover_at      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_browser_session_account
    ON browser_sessions (account_id);

CREATE TABLE IF NOT EXISTS browser_action_traces (
    id                    TEXT PRIMARY KEY,
    account_id            TEXT NOT NULL REFERENCES social_accounts (id) ON DELETE RESTRICT,
    job_id                TEXT REFERENCES social_jobs (id) ON DELETE SET NULL,
    action                TEXT NOT NULL,
    selector_pack_version TEXT NOT NULL DEFAULT '',
    target_fingerprint    TEXT NOT NULL DEFAULT '',
    before_url            TEXT,
    after_url             TEXT,
    screenshot_ref        TEXT,
    result                TEXT NOT NULL DEFAULT '',
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id                   TEXT PRIMARY KEY,
    actor_type           TEXT NOT NULL DEFAULT 'SYSTEM',
    actor_id             TEXT,
    trace_id             TEXT,
    account_id           TEXT,
    job_id               TEXT,
    event_type           TEXT NOT NULL,
    capability           TEXT,
    policy_version       INTEGER,
    policy_decision      TEXT,
    object_ref           TEXT,
    redacted_detail_json TEXT NOT NULL DEFAULT '{}',
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_account ON audit_events (account_id, created_at);
CREATE INDEX IF NOT EXISTS ix_audit_job ON audit_events (job_id);

-- «Строки аудита не изменяются» — правило, которое словами не держится.
CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events append-only: строки аудита не изменяются');
END;

CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events append-only: строки аудита не удаляются');
END;

-- Опубликованная ревизия не меняется молча: она уже ушла наружу.
CREATE TRIGGER IF NOT EXISTS content_revisions_immutable
BEFORE UPDATE OF caption, asset_ids, target_account_ids, schedule_at, content_hash
ON content_revisions
BEGIN
    SELECT RAISE(ABORT,
        'ревизия неизменяема: правка создаёт новую ревизию, а не меняет существующую');
END;
"""

MIGRATIONS: dict[int, str] = {1: _V1}


def connect(path: str | Path) -> sqlite3.Connection:
    """Соединение с включёнными ключами и журналом WAL.

    `foreign_keys=ON` обязателен: в SQLite внешние ключи по умолчанию
    ВЫКЛЮЧЕНЫ, и объявленные в схеме ссылки без этого не проверяются вовсе —
    схема выглядела бы целостной, не будучи такой.
    """
    conn = sqlite3.connect(str(path), isolation_level=None,
                           detect_types=0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_meta ("
                 "key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Применить недостающие миграции по порядку. Возвращает итоговую версию.

    Понижение версии не поддерживается намеренно: база, созданная более новым
    кодом, обязана останавливать старый, а не молча работать с непонятной ей
    схемой.
    """
    version = current_version(conn)
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"база создана версией схемы {version}, а этот код знает только "
            f"{SCHEMA_VERSION}. Обновите приложение — работать со схемой из "
            f"будущего нельзя.")
    for step in sorted(MIGRATIONS):
        if step <= version:
            continue
        conn.executescript("BEGIN;\n" + MIGRATIONS[step] + "\nCOMMIT;")
        conn.execute("INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (str(step),))
        version = step
    return version


def open_database(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(target)
    migrate(conn)
    return conn


def table_names(conn: sqlite3.Connection) -> set[str]:
    with closing(conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")) as cur:
        return {r[0] for r in cur.fetchall()}


__all__ = ["MIGRATIONS", "SCHEMA_VERSION", "connect", "current_version", "migrate",
           "open_database", "table_names"]
