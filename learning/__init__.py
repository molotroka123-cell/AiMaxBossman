"""Bossman Learning Layer — структурированные инженерные записи (learning traces).

Не хранит скрытую цепочку рассуждений; хранит явные решения, доказательства и
переносимые уроки. Только VERIFIED попадает в канонический корпус."""
from .trace import (FORBIDDEN_FIELDS, STATUSES, LearningStore, ValidationError, case_id,
                    redact_text, validate)

__all__ = ["FORBIDDEN_FIELDS", "STATUSES", "LearningStore", "ValidationError", "case_id",
           "redact_text", "validate"]
