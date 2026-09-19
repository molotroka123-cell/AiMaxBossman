"""Долговечное хранение работ поверх объявленной схемы и канонического автомата."""
from .store import DEFAULT_LEASE_SECONDS, JobRecord, JobStore, JobStoreError

__all__ = ["DEFAULT_LEASE_SECONDS", "JobRecord", "JobStore", "JobStoreError"]
