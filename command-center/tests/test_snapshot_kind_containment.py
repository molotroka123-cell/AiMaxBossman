"""P0 (EOD re-audit 2026-09-23, C1): `kind` снапшота шёл в имя каталога как есть.

`{"kind": "/../../../x"}` создавал каталог ВНЕ data dir и клал туда полную копию
базы. `kind` — метка, а не путь: допускается только [a-z0-9_-]{1,16}, иначе 400,
и итоговый каталог обязан лежать внутри каталога снапшотов.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.features.snapshot import SNAPSHOT_DIRNAME

BAD_KINDS = ["/../../../x", "..\\..\\..\\y", "/../../zz", "../esc", "a/b", "C:\\x", "..", "x\x00y"]


@pytest.mark.parametrize("kind", BAD_KINDS)
async def test_path_like_kind_is_refused_and_nothing_escapes(env, kind):
    data_dir = Path(env.svc.settings.data_dir).resolve()
    before = {p.resolve() for p in data_dir.parent.rglob("*")}
    r = await env.client.post("/api/snapshots", json={"kind": kind})
    assert r.status_code == 400, (r.status_code, r.text)
    after = {p.resolve() for p in data_dir.parent.rglob("*")}
    snapshots = (data_dir / SNAPSHOT_DIRNAME).resolve()
    created = [p for p in after - before if not p.is_relative_to(snapshots)]
    assert created == [], created


async def test_plain_kind_still_works_inside_snapshot_dir(env):
    r = await env.client.post("/api/snapshots", json={"kind": "manual"})
    assert r.status_code == 200, r.text
    path = Path(r.json()["path"]).resolve()
    assert path.is_relative_to((Path(env.svc.settings.data_dir) / SNAPSHOT_DIRNAME).resolve())
