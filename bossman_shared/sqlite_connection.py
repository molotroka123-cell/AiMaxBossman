"""Owned SQLite handles with ordinary transaction semantics and explicit close.

The stdlib Connection context manager commits/rolls back but does not close.
Stores that open a new connection for every operation must close on scope exit
rather than relying on cyclic GC. Bare connection users still own explicit close.
"""
from __future__ import annotations

import sqlite3
from types import TracebackType


class OwnedConnection(sqlite3.Connection):
    """A one-operation connection; a completed context cannot be re-entered."""

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc_value: BaseException | None,
                 traceback: TracebackType | None) -> bool:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()
