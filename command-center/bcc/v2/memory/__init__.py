from .obsidian import ObsidianVault
from .memsearch_bridge import MemSearchBridge, MemoryHit, MemSearchUnavailable
# Переранжирование — одна ответственность и один владелец: `reranker.py`.
# `LexicalReranker` раньше был и тут, и в `local_index.py`; экспортировалась
# копия из `local_index`, а вторая реализация была недостижима.
from .reranker import LexicalReranker, LocalCrossEncoderReranker, RerankerUnavailable
from .context_pack import ContextPack, ContextItem, build_context_pack, estimate_tokens
from .local_index import (
    DenseUnavailable,
    LocalMemoryBackend,
    chunk_markdown,
    load_dense_encoder,
    split_sections,
    tokenize,
)
from .service import MemoryBackend, ObsidianMemoryService

__all__ = [
    "ObsidianVault",
    "MemSearchBridge",
    "MemSearchUnavailable",
    "MemoryHit",
    "LocalCrossEncoderReranker",
    "RerankerUnavailable",
    "LocalMemoryBackend",
    "LexicalReranker",
    "DenseUnavailable",
    "load_dense_encoder",
    "chunk_markdown",
    "split_sections",
    "tokenize",
    "ContextPack",
    "ContextItem",
    "build_context_pack",
    "estimate_tokens",
    "MemoryBackend",
    "ObsidianMemoryService",
]
