"""Секреты: хранилище значений и редакция всего, что выходит наружу.

Домен, аудит, логи и мост знают только `auth_ref`. Значение достаётся ровно в
одном месте — в адаптере, в момент вызова провайдера.
"""
from .redaction import (MASK, assert_no_secret, audit_detail, redact,
                        safe_log_record)
from .vault import (InMemoryVault, LocalEncryptedVault, SecretHealth, SecretMetadata,
                    SecretNotFound, SecretOwnershipError, SecretRevoked, SecretValue,
                    SecretVault, VaultError, load_master_key)

__all__ = ["InMemoryVault", "LocalEncryptedVault", "MASK", "SecretHealth",
           "SecretMetadata", "SecretNotFound", "SecretOwnershipError", "SecretRevoked",
           "SecretValue", "SecretVault", "VaultError", "assert_no_secret",
           "audit_detail", "load_master_key", "redact", "safe_log_record"]
