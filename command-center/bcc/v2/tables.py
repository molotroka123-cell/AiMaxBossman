"""BOSSMAN V2 SQLAlchemy table declarations.

Integration:
    import this module once from command-center/bcc/db.py after `metadata` and
    `utcnow` exist, OR move these declarations into db.py under one migration owner.

Do not let 15 parallel agents create competing versions of these tables.
"""
from __future__ import annotations

import sqlalchemy as sa

from ..db import metadata, utcnow

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

missions = sa.Table(
    "missions", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(240), nullable=False),
    sa.Column("goal", sa.Text, nullable=False),
    sa.Column("status", sa.String(24), default="draft"),
    sa.Column("success_criteria", sa.JSON, default=dict),
    sa.Column("stop_conditions", sa.JSON, default=dict),
    sa.Column("max_workers", sa.Integer, default=1),
    sa.Column("cloud_budget_usd", sa.Float, default=0.0),
    sa.Column("cloud_spent_usd", sa.Float, default=0.0),
    sa.Column("starts_at", sa.DateTime),
    sa.Column("ends_at", sa.DateTime),
    sa.Column("checkpoint", sa.JSON),
    sa.Column("created_at", sa.DateTime, default=utcnow),
    sa.Column("updated_at", sa.DateTime, default=utcnow),
)

mission_tasks = sa.Table(
    "mission_tasks", metadata,
    sa.Column("mission_id", sa.Integer, sa.ForeignKey("missions.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("milestone", sa.String(200), default=""),
    sa.Column("weight", sa.Float, default=1.0),
)

mission_kpis = sa.Table(
    "mission_kpis", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mission_id", sa.Integer, sa.ForeignKey("missions.id", ondelete="CASCADE"), nullable=False),
    sa.Column("key", sa.String(100), nullable=False),
    sa.Column("label", sa.String(200), nullable=False),
    sa.Column("unit", sa.String(40), default=""),
    sa.Column("aggregation", sa.String(16), default="sum"),
    sa.Column("target", sa.Float),
    sa.Column("current", sa.Float, default=0.0),
    sa.UniqueConstraint("mission_id", "key", name="uq_mission_kpi_key"),
)

mission_kpi_events = sa.Table(
    "mission_kpi_events", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mission_id", sa.Integer, sa.ForeignKey("missions.id", ondelete="CASCADE"), nullable=False),
    sa.Column("kpi_id", sa.Integer, sa.ForeignKey("mission_kpis.id", ondelete="CASCADE"), nullable=False),
    sa.Column("value", sa.Float, nullable=False),
    sa.Column("source_event_id", sa.Integer, sa.ForeignKey("events.id", ondelete="SET NULL")),
    sa.Column("ts", sa.DateTime, default=utcnow),
)

resource_reservations = sa.Table(
    "resource_reservations", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("owner_kind", sa.String(40), nullable=False),
    sa.Column("owner_id", sa.String(120), nullable=False),
    sa.Column("memory_mb", sa.Integer, default=0),
    sa.Column("gpu_memory_mb", sa.Integer, default=0),
    sa.Column("status", sa.String(20), default="active"),
    sa.Column("created_at", sa.DateTime, default=utcnow),
    sa.Column("released_at", sa.DateTime),
)

governor_interventions = sa.Table(
    "governor_interventions", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mission_id", sa.Integer, sa.ForeignKey("missions.id", ondelete="SET NULL")),
    sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id", ondelete="SET NULL")),
    sa.Column("run_id", sa.Integer, sa.ForeignKey("task_runs.id", ondelete="SET NULL")),
    sa.Column("reason", sa.String(120), nullable=False),
    sa.Column("action", sa.String(48), nullable=False),
    sa.Column("detail", sa.JSON, default=dict),
    sa.Column("created_at", sa.DateTime, default=utcnow),
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

session_forks = sa.Table(
    "session_forks", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("parent_run_id", sa.Integer, sa.ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("child_run_id", sa.Integer, sa.ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("checkpoint_step", sa.Integer, default=0),
    sa.Column("agent_override_id", sa.Integer, sa.ForeignKey("agents.id", ondelete="SET NULL")),
    sa.Column("model_override_id", sa.Integer, sa.ForeignKey("models.id", ondelete="SET NULL")),
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

skills = sa.Table(
    "skills", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("skill_key", sa.String(100), nullable=False, unique=True),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("description", sa.Text, default=""),
    sa.Column("source", sa.String(40), default="agents"),
    sa.Column("source_path", sa.Text, default=""),
    sa.Column("enabled", sa.Boolean, default=True),
    sa.Column("current_version_id", sa.Integer),
    sa.Column("created_at", sa.DateTime, default=utcnow),
    sa.Column("updated_at", sa.DateTime, default=utcnow),
)

skill_versions = sa.Table(
    "skill_versions", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("skill_id", sa.Integer, sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False),
    sa.Column("version", sa.String(40), nullable=False),
    sa.Column("fingerprint", sa.String(64), nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("permissions", sa.JSON, default=dict),
    sa.Column("provenance", sa.JSON, default=dict),
    sa.Column("created_at", sa.DateTime, default=utcnow),
    sa.UniqueConstraint("skill_id", "fingerprint", name="uq_skill_fingerprint"),
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
