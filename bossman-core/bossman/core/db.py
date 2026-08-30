"""Compatibility shim: canonical db layer re-export.

V2 working memory (and any future core subsystem) imports the shared Postgres
layer through this module so tests can patch a single `pool` attribute.
This is a re-export only — there is exactly ONE db engine (bossman.db).
"""
from ..db import execute, fetch, fetchrow, fetchval, pool  # noqa: F401
