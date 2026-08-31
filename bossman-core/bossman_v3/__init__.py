"""Bossman V3 seven-module drop-in pack.

This package deliberately does not replace Bossman's canonical Gateway,
Policy, Approval, Tool Registry, EventBus, Memory, or Computer Operator.
It integrates through ports defined in :mod:`bossman_v3.contracts`.
"""
from .feature_flags import V3Flags

__all__ = ["V3Flags"]
