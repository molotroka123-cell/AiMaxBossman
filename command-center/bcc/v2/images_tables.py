"""Persistent tables for BOSSMAN Images.

Imported by bcc.features.images during feature discovery. Because Services loads
features before Database.create_all(), these tables are registered on the same
canonical metadata before schema creation.
"""
from __future__ import annotations

import sqlalchemy as sa

from ..db import metadata, utcnow


image_collections = sa.Table(
    "image_collections", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(180), nullable=False, unique=True),
    sa.Column("parent_id", sa.Integer, sa.ForeignKey("image_collections.id", ondelete="SET NULL")),
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

image_jobs = sa.Table(
    "image_jobs", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("kind", sa.String(32), nullable=False, default="generate"),
    sa.Column("status", sa.String(24), nullable=False, default="queued"),
    sa.Column("prompt", sa.Text, nullable=False),
    sa.Column("negative_prompt", sa.Text, default=""),
    sa.Column("model_alias", sa.String(240), nullable=False, default="mock-image"),
    sa.Column("aspect_ratio", sa.String(24), default="1:1"),
    sa.Column("width", sa.Integer, default=1024),
    sa.Column("height", sa.Integer, default=1024),
    sa.Column("steps", sa.Integer, default=30),
    sa.Column("seed", sa.Integer),
    sa.Column("count", sa.Integer, default=1),
    sa.Column("collection_id", sa.Integer, sa.ForeignKey("image_collections.id", ondelete="SET NULL")),
    sa.Column("source_asset_id", sa.Integer),
    sa.Column("reference_asset_ids", sa.JSON, default=list),
    sa.Column("tags", sa.JSON, default=list),
    sa.Column("options", sa.JSON, default=dict),
    sa.Column("progress", sa.Float, default=0.0),
    sa.Column("error", sa.Text, default=""),
    sa.Column("created_at", sa.DateTime, default=utcnow),
    sa.Column("updated_at", sa.DateTime, default=utcnow),
    sa.Column("started_at", sa.DateTime),
    sa.Column("finished_at", sa.DateTime),
)

image_assets = sa.Table(
    "image_assets", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("source_job_id", sa.Integer, sa.ForeignKey("image_jobs.id", ondelete="SET NULL")),
    sa.Column("title", sa.String(240), default=""),
    sa.Column("prompt", sa.Text, default=""),
    sa.Column("negative_prompt", sa.Text, default=""),
    sa.Column("model_alias", sa.String(240), default=""),
    sa.Column("aspect_ratio", sa.String(24), default="1:1"),
    sa.Column("width", sa.Integer, default=0),
    sa.Column("height", sa.Integer, default=0),
    sa.Column("seed", sa.Integer),
    sa.Column("mime_type", sa.String(100), default="image/svg+xml"),
    sa.Column("file_path", sa.Text, nullable=False),
    sa.Column("file_bytes", sa.Integer, default=0),
    sa.Column("favorite", sa.Boolean, default=False),
    sa.Column("status", sa.String(24), default="ready"),
    sa.Column("collection_id", sa.Integer, sa.ForeignKey("image_collections.id", ondelete="SET NULL")),
    sa.Column("tags", sa.JSON, default=list),
    sa.Column("meta", sa.JSON, default=dict),
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

image_templates = sa.Table(
    "image_templates", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(180), nullable=False, unique=True),
    sa.Column("prompt", sa.Text, nullable=False),
    sa.Column("negative_prompt", sa.Text, default=""),
    sa.Column("model_alias", sa.String(240), default="mock-image"),
    sa.Column("aspect_ratio", sa.String(24), default="1:1"),
    sa.Column("width", sa.Integer, default=1024),
    sa.Column("height", sa.Integer, default=1024),
    sa.Column("steps", sa.Integer, default=30),
    sa.Column("options", sa.JSON, default=dict),
    sa.Column("created_at", sa.DateTime, default=utcnow),
)
