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


def valid_vector(value, dimension: int | None = None) -> bool:
    return (isinstance(value, (list, tuple)) and bool(value)
            and (dimension is None or len(value) == dimension)
            and all(type(x) in (int, float) and math.isfinite(x) for x in value))


def cosine(a: list[float], b: list[float]) -> float:
    if not valid_vector(a) or not valid_vector(b, len(a)):
        return 0.0
    norm_a = math.sqrt(sum(x*x for x in a))
    norm_b = math.sqrt(sum(x*x for x in b))
    if not norm_a or not norm_b or not math.isfinite(norm_a * norm_b):
        return 0.0
    return max(-1.0, min(1.0, sum(x*y for x, y in zip(a,b)) / (norm_a * norm_b)))
