"""Artifact identity / hash verification / locality (§17).

Артефакт идентифицируется содержимым (sha256). После переноса файл
проверяется по хэшу; несовпадение — это не артефакт, и шаг не запускается.
Реестр `fleet_artifacts` знает, на каких узлах какой хэш есть, чтобы
планировщик считал стоимость переноса.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .models import ArtifactDescriptor, PRIVATE
from .store import FleetStore


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def describe_file(path: str | Path, *, artifact_id: str, media_type: str = "application/octet-stream",
                  privacy: str = PRIVATE) -> ArtifactDescriptor:
    p = Path(path)
    return ArtifactDescriptor(artifact_id, sha256_file(p), p.stat().st_size, media_type, privacy)


def verify_file(path: str | Path, expected_sha256: str) -> bool:
    return Path(path).exists() and sha256_file(path) == expected_sha256


class ArtifactRegistry:
    def __init__(self, store: FleetStore) -> None:
        self.store = store

    def publish(self, d: ArtifactDescriptor, *, node_id: str) -> None:
        cur = self.store.artifact(d.sha256) or {"artifact_id": d.artifact_id, "size_bytes": d.size_bytes,
                                                 "media_type": d.media_type, "privacy": d.privacy, "nodes": []}
        if node_id not in cur["nodes"]:
            cur["nodes"].append(node_id)
        self.store.save_artifact(d.sha256, cur)
        node = self.store.node(node_id)
        if node is not None:
            node.artifacts.add(d.sha256)
            self.store.save_node(node)

    def locations(self, sha256: str) -> list[str]:
        cur = self.store.artifact(sha256)
        return list(cur["nodes"]) if cur else []

    def transfer_bytes(self, hashes: list[str], node_id: str) -> int:
        total = 0
        for h in hashes:
            cur = self.store.artifact(h)
            if cur and node_id not in cur["nodes"]:
                total += int(cur["size_bytes"])
        return total
