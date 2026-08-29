from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

_TOKEN = re.compile(r"[\w\-./:]{2,}", re.UNICODE)


class Embedder(Protocol):
    dimension: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """Dependency-free deterministic fallback for tests/prototype.

    Production should inject a proper local multilingual embedding model through
    the same interface. This fallback makes Stage 2.222 runnable immediately.
    """
    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(x) for x in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            number = int.from_bytes(digest, "big")
            idx = number % self.dimension
            sign = -1.0 if (number >> 8) & 1 else 1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    return sum(x * y for x, y in zip(a, b))
