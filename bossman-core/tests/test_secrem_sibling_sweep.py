"""SECREM sibling sweep (core): один набор контрпримеров против всех компонентов
границы. egress: toolkit.net.check_url; path: fs.read/fs.write/fs.search/media.probe."""
from __future__ import annotations

import pytest

from bossman.toolkit import ToolContext
from bossman.toolkit import net
from bossman.toolkit.files import fs_read, fs_search, fs_write
from bossman.toolkit.media import _path_arg_ok

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))          # tests/ не пакет в core
from _secrem.mutators import (EGRESS_ALWAYS_BLOCKED, EGRESS_PRIVATE,  # noqa: E402
                              PATH_TRAVERSAL_SEGMENTS, path_escapes)


@pytest.fixture(autouse=True)
def _no_overrides(monkeypatch):
    monkeypatch.delenv("BOSSMAN_HTTP_ALLOW_HOSTS", raising=False)
    monkeypatch.delenv("BOSSMAN_HTTP_ALLOW_PRIVATE", raising=False)


@pytest.mark.parametrize("url", EGRESS_ALWAYS_BLOCKED + EGRESS_PRIVATE)
def test_http_tool_refuses_every_blocked_target(url):
    # хосты-имена (metadata.google.internal/localhost) — по имени; литералы — по адресу
    with pytest.raises(net.EgressDenied):
        net.check_url(url)


@pytest.mark.parametrize("name", ["dotdot", "nested_dotdot", "symlink_file", "symlink_dir"])
async def test_fs_tools_refuse_every_escape_form(tmp_path, name):
    root = tmp_path / "ws"
    esc = path_escapes(root, tmp_path / "outside")[name]
    ctx = ToolContext(agent="t", workdir=root)
    rel = str(esc.relative_to(root)) if str(esc).startswith(str(root)) else str(esc)
    with pytest.raises(PermissionError):
        await fs_read({"path": rel}, ctx)
    with pytest.raises(PermissionError):
        await fs_write({"path": rel, "content": "x"}, ctx)
    res = await fs_search({"pattern": "outside", "glob": rel if name != "dotdot" else "../outside/*"}, ctx)
    assert "outside" not in res.content.replace("outside/", "")  # ни одной строки из outside


MEDIA_MUST_REJECT = {"../../evil", "..", "/tmp/x", "good/../evil", "..\\evil", "x\x00y"}


@pytest.mark.parametrize("seg", [s for s in PATH_TRAVERSAL_SEGMENTS if s])
def test_media_probe_barrier_rejects_traversal(seg):
    """Барьер ловит любой «..»-компонент (включая голый), абсолютные пути и NUL;
    прочие строки — литеральные имена внутри workdir (резолв делает путь абсолютным,
    поэтому «-flag» не становится опцией ffprobe)."""
    assert _path_arg_ok(seg) is (seg not in MEDIA_MUST_REJECT), repr(seg)


def test_media_probe_barrier_control():
    assert _path_arg_ok("clip.mp4") and not _path_arg_ok("../x.mp4") and not _path_arg_ok("/etc/passwd")
