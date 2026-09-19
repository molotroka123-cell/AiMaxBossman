"""Additive Studio metadata on the canonical Images queue; no second worker."""
import sqlalchemy as sa
from bcc.db import metadata, utcnow

jobs = sa.Table('studio_jobs',metadata,
    sa.Column('job_id',sa.Integer,sa.ForeignKey('image_jobs.id'),primary_key=True),
    sa.Column('task_id',sa.Integer),sa.Column('agent_run_id',sa.Integer),
    sa.Column('plane',sa.JSON,nullable=False),sa.Column('surface',sa.String(16),nullable=False),
    sa.Column('request_id',sa.String(240)),sa.Column('reason',sa.String(80)),
    sa.Column('verdict',sa.String(24),default='PARTIAL'),
    sa.Column('submit_started',sa.Boolean,default=False),
    sa.Column('reserved_usd',sa.Float,default=0),sa.Column('budget_day',sa.String(10)),
    sa.Column('cost_usd',sa.JSON),sa.Column('policy_digest',sa.String(64)),
)
runs = sa.Table('studio_runs',metadata,
    sa.Column('id',sa.String(40),primary_key=True),
    sa.Column('job_id',sa.Integer,sa.ForeignKey('image_jobs.id')),
    sa.Column('legacy_asset_id',sa.Integer,unique=True),
    sa.Column('surface',sa.String(16),nullable=False),sa.Column('model',sa.String(240),nullable=False),
    sa.Column('provenance',sa.JSON,nullable=False),sa.Column('file_path',sa.Text,nullable=False),
    sa.Column('sha256',sa.String(64),nullable=False),sa.Column('file_bytes',sa.Integer,nullable=False),
    sa.Column('mime',sa.String(80),nullable=False),sa.Column('created_at',sa.DateTime,default=utcnow),
    sa.Column('favorite',sa.Boolean,default=False),sa.Column('deleted',sa.Boolean,default=False),
    sa.Column('collection_id',sa.Integer,sa.ForeignKey('image_collections.id')),
)
config = sa.Table('studio_config',metadata,sa.Column('key',sa.String(80),primary_key=True),sa.Column('value',sa.JSON,nullable=False))
budget = sa.Table('studio_budget',metadata,sa.Column('day',sa.String(10),primary_key=True),sa.Column('committed_usd',sa.Float,nullable=False,default=0))
