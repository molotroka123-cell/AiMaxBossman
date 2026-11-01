"""Owner 01.11 screenshot: «тут все главные папки компа должны выпадать» in the Auto Full picker. Every folder offered
must pass the same validation as enabling Auto Full; personal media folders and the home folder itself never appear."""
from __future__ import annotations

from pathlib import Path

from bcc import approval_scope as scope


async def test_main_folders_projects_and_no_personal_media(env, monkeypatch, tmp_path):
    home = tmp_path / "home"
    for name in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Bossman/Projects/alpha"):
        (home / name).mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("BCC_PROJECTS_ROOT", str(home / "Bossman" / "Projects"))
    got = await scope.auto_full_folder_choices(env.svc)
    paths = {Path(c["path"]).name: c["group"] for c in got}
    for name in ("Desktop", "Documents", "Downloads", "Bossman"):
        assert paths.get(name) == "Главные папки", (name, got)
    assert paths.get("alpha") == "Проекты" and paths.get("Projects") == "Проекты"
    assert "Pictures" not in paths and "Videos" not in paths            # personal media never offered
    assert all(Path(c["path"]) != home for c in got)                    # the home folder itself is refused
    for c in got:                                                       # nothing offered would be refused later
        assert scope.validate_auto_full_folder(c["path"]) == Path(c["path"])


async def test_the_route_returns_the_choices(env):
    r = await env.client.get("/api/approvals/auto-full/folders")
    assert r.status_code == 200 and isinstance(r.json()["folders"], list)
