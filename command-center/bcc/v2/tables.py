"""BOSSMAN V2 SQLAlchemy table declarations.

Integration:
    import this module once from command-center/bcc/db.py after `metadata` and
    `utcnow` exist, OR move these declarations into db.py under one migration owner.

Do not let 15 parallel agents create competing versions of these tables.
"""
from __future__ import annotations

import sqlalchemy as sa

from ..db import metadata, utcnow

# 5 дублей (missions/resource_reservations/session_forks/skills/skill_versions)
# удалены — их owner — контрактные таблицы в bcc/db.py. Остальные runtime-таблицы
# (браузер/терминал/MCP/OpenCode/каталог/capability/evaluations) объявлены на ТОЙ ЖЕ
# core-metadata, поэтому их FK на providers/models/tasks/agents резолвятся, а
# create_all строит их одним проходом. Один owner на таблицу сохранён.

provider_catalog_models = sa.Table(
    "provider_catalog_models", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("provider_id", sa.Integer, sa.ForeignKey("providers.id", ondelete="CASCADE"), nullable=False),
    sa.Column("remote_id", sa.String(300), nullable=False),
    sa.Column("display_name", sa.String(300), default=""),
    sa.Column("context_window", sa.Integer, default=0),
    sa.Column("price_in", sa.Float, default=0.0),
    sa.Column("price_out", sa.Float, default=0.0),
    sa.Column("input_modalities", sa.JSON, default=list),
    sa.Column("output_modalities", sa.JSON, default=list),
    sa.Column("supported_parameters", sa.JSON, default=list),
    sa.Column("architecture", sa.JSON, default=dict),
    sa.Column("advertised_caps", sa.JSON, default=dict),
    sa.Column("raw_metadata", sa.JSON, default=dict),
    sa.Column("stale", sa.Boolean, default=False),
    sa.Column("last_synced_at", sa.DateTime, default=utcnow),
    sa.UniqueConstraint("provider_id", "remote_id", name="uq_provider_catalog_remote"),
)

model_capability_checks = sa.Table(
    "model_capability_checks", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("model_id", sa.Integer, sa.ForeignKey("models.id", ondelete="CASCADE"), nullable=False),
    sa.Column("capability", sa.String(64), nullable=False),
    sa.Column("advertised", sa.Boolean),
    sa.Column("verified", sa.Boolean),
    sa.Column("detail", sa.Text, default=""),
    sa.Column("checked_at", sa.DateTime, default=utcnow),
)







evaluations = sa.Table(
    "evaluations", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id", ondelete="CASCADE")),
    sa.Column("run_id", sa.Integer, sa.ForeignKey("task_runs.id", ondelete="CASCADE")),
    sa.Column("reviewer_agent_id", sa.Integer, sa.ForeignKey("agents.id", ondelete="SET NULL")),
    sa.Column("iteration", sa.Integer, default=0),
    sa.Column("passed", sa.Boolean, default=False),
    sa.Column("score", sa.Float),
    sa.Column("feedback", sa.Text, default=""),
    sa.Column("artifacts", sa.JSON, default=list),
    sa.Column("created_at", sa.DateTime, default=utcnow),
)


browser_profiles = sa.Table(
    "browser_profiles", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(120), nullable=False, unique=True),
    sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id", ondelete="SET NULL")),
    sa.Column("policy", sa.JSON, default=dict),
    sa.Column("created_at", sa.DateTime, default=utcnow),
    sa.Column("updated_at", sa.DateTime, default=utcnow),
)

browser_sessions = sa.Table(
    "browser_sessions", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("profile_id", sa.Integer, sa.ForeignKey("browser_profiles.id", ondelete="SET NULL")),
    sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id", ondelete="SET NULL")),
    sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id", ondelete="SET NULL")),
    sa.Column("status", sa.String(24), default="created"),
    sa.Column("current_url", sa.Text, default=""),
    sa.Column("takeover", sa.Boolean, default=False),
    sa.Column("paused", sa.Boolean, default=False),
    sa.Column("last_action", sa.Text, default=""),
    sa.Column("created_at", sa.DateTime, default=utcnow),
    sa.Column("updated_at", sa.DateTime, default=utcnow),
    sa.Column("finished_at", sa.DateTime),
)

terminal_sessions = sa.Table(
    "terminal_sessions", metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id", ondelete="SET NULL")),
    sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id", ondelete="SET NULL")),
    sa.Column("mode", sa.String(24), nullable=False),
    sa.Column("cwd", sa.Text, nullable=False),
    sa.Column("command", sa.Text, nullable=False),
    sa.Column("status", sa.String(24), default="running"),
    sa.Column("pid", sa.Integer),
    sa.Column("exit_code", sa.Integer),
    sa.Column("started_at", sa.DateTime, default=utcnow),
    sa.Column("finished_at", sa.DateTime),
)



mcp_servers = sa.Table(
    "mcp_servers", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(120), nullable=False, unique=True),
    sa.Column("transport", sa.String(16), nullable=False),
    sa.Column("command", sa.JSON, default=list),
    sa.Column("url", sa.Text, default=""),
    sa.Column("cwd", sa.Text, default=""),
    sa.Column("env_keys", sa.JSON, default=list),
    sa.Column("enabled", sa.Boolean, default=True),
    sa.Column("status", sa.String(20), default="unknown"),
    sa.Column("status_detail", sa.Text, default=""),
    sa.Column("last_check", sa.DateTime),
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

mcp_tools = sa.Table(
    "mcp_tools", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("server_id", sa.Integer, sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("description", sa.Text, default=""),
    sa.Column("input_schema", sa.JSON, default=dict),
    sa.Column("enabled", sa.Boolean, default=True),
    sa.UniqueConstraint("server_id", "name", name="uq_mcp_server_tool"),
)

opencode_sessions = sa.Table(
    "opencode_sessions", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id", ondelete="SET NULL")),
    sa.Column("run_id", sa.Integer, sa.ForeignKey("task_runs.id", ondelete="SET NULL")),
    sa.Column("session_id", sa.String(200), nullable=False, unique=True),
    sa.Column("project_path", sa.Text, default=""),
    sa.Column("worktree_path", sa.Text, default=""),
    sa.Column("status", sa.String(24), default="active"),
    sa.Column("created_at", sa.DateTime, default=utcnow),
    sa.Column("updated_at", sa.DateTime, default=utcnow),
)
