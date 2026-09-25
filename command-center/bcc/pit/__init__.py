"""Bossman 1.7 Personal Identity Training.

This package is deliberately a same-product extension: it contains pure policy,
identity, routing and memory primitives that plug into the existing Command
Center backend. It does not create a second task engine, provider registry,
approval system or canonical database.
"""

from .identity import derive_person_key, validate_person_key
from .models import ConsentState, MemoryCandidate, Sensitivity

__all__ = [
    "ConsentState",
    "MemoryCandidate",
    "Sensitivity",
    "derive_person_key",
    "validate_person_key",
]
