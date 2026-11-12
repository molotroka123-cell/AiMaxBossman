"""filesystem.read_any: whole-PC READ-ONLY reads for the PC operator, DENY list always wins (owner 11.11)."""
from __future__ import annotations

import subprocess
import sys

import pytest
import sqlalchemy as sa

from bcc.db import agents as agents_t
from bcc.features import fs_read_policy as pol
from bcc.features import pc_operator as po
from bcc.features import tools_fs_read as fr
from bcc.tools import ToolContext, decide_effect

from .helpers import make_stack

WIN = sys.platform == "win32"
HOSTS = r"C:\Windows\System32\drivers\etc\hosts"
GRANTED = {"permissions": {fr.PERM: True}}


def _ctx(agent):
    return ToolContext(svc=type("S", (), {})(), task={}, run_id=1, agent=agent)


async def _run(args, agent=GRANTED):
    return await fr._t_read_any(args, _ctx(agent))


@pytest.mark.skipif(not WIN, reason="Windows paths")
async def test_allowed_hosts_readme_and_drive_listing(tmp_path):
    r = await _run({"path": HOSTS})
    assert not r.error and r.content
    readme = tmp_path / "README.md"
    readme.write_text("# hello\nline2\n", encoding="utf-8")
    assert (await _run({"path": str(readme)})).content.startswith("# hello")
    r = await _run({"path": "C:\\"})
    assert not r.error and "Windows" in r.content and "Users" in r.content


DENY = [
    (r"C:\Users\x\AppData\Local\Google\Chrome\User Data\Default\Login Data", "FS_DENY_BROWSER"),
    (r"C:\Users\x\AppData\Local\Microsoft\Edge\User Data\Default\Cookies", "FS_DENY_BROWSER"),
    (r"C:\Users\x\AppData\Roaming\Mozilla\Firefox\Profiles\a.default\cookies.sqlite", "FS_DENY_BROWSER"),
    (r"C:\Users\x\AppData\Roaming\Telegram Desktop\tdata\D877F783D5D3EF8C", "FS_DENY_CHAT"),
    (r"C:\Users\x\AppData\Roaming\WhatsApp\x.txt", "FS_DENY_CHAT"),
    (r"C:\Users\x\AppData\Roaming\discord\Local Storage\a.log", "FS_DENY_CHAT"),
    (r"C:\Users\x\AppData\Local\Bossman\CommandCenter\bcc.db", "FS_DENY_CHAT"),
    (r"D:\data\bcc.db-wal", "FS_DENY_CHAT"),
    (r"C:\Users\x\AppData\Roaming\Microsoft\Protect\S-1-5\key", "FS_DENY_SECRET"),
    (r"C:\Users\x\AppData\Roaming\Microsoft\Credentials\abc", "FS_DENY_SECRET"),
    (r"C:\Users\x\.ssh\config", "FS_DENY_SECRET"),
    (r"C:\Users\x\.gnupg\pubring.kbx", "FS_DENY_SECRET"),
    (r"D:\p\server.pem", "FS_DENY_SECRET"), (r"D:\p\a.key", "FS_DENY_SECRET"), (r"D:\p\a.pfx", "FS_DENY_SECRET"),
    (r"D:\p\provider-keys.env", "FS_DENY_SECRET"), (r"D:\p\.env", "FS_DENY_SECRET"),
    (r"D:\p\.env.local", "FS_DENY_SECRET"), (r"D:\p\companion.env", "FS_DENY_SECRET"),
    (r"D:\p\vault.json", "FS_DENY_SECRET"), (r"D:\p\a.kdbx", "FS_DENY_SECRET"),
    (r"D:\p\wallet.dat", "FS_DENY_SECRET"), (r"D:\p\seed_phrase.txt", "FS_DENY_SECRET"),
    ("D:\\\u0413\u0440\u0435\u0447\u043a\u0430\\notes.txt", "FS_DENY_GRECHKA"), (r"D:\x\a.enc", "FS_DENY_SECRET"),
    (r"C:\Users\x\Pictures\a.txt", "FS_DENY_MEDIA"), (r"C:\Users\x\Videos", "FS_DENY_MEDIA"),
    (r"E:\DCIM\100", "FS_DENY_MEDIA"),
    (r"C:\Users\x\AppData\Local\Microsoft\Outlook\a.ost", "FS_DENY_CHAT"),
    (r"D:\mail\a.pst", "FS_DENY_MAIL"),
    (r"C:\Windows\System32\config\SAM", "FS_DENY_HIVE"), (r"C:\Windows\System32\config\SYSTEM", "FS_DENY_HIVE"),
    (r"C:\Windows\System32\config\SECURITY", "FS_DENY_HIVE"),
    (r"C:\pagefile.sys", "FS_DENY_HIVE"), (r"C:\hiberfil.sys", "FS_DENY_HIVE"),
    (r"C:\Users\x\NTUSER.DAT", "FS_DENY_HIVE"),
    (r"C:\Windows\..\Users\x\.ssh\id_rsa", "FS_DENY_SECRET"),
    (r"C:\Users\x\AppData\Local\Google\Chrome\User Data\..\User Data\Default\History", "FS_DENY_BROWSER"),
    (r"C:\Users\x\f.txt:secret", "FS_BAD_PATH"), ("relative\\x", "FS_BAD_PATH"), (r"\\server\share\a", "FS_UNC"),
    ("", "FS_BAD_PATH"),
]


@pytest.mark.skipif(not WIN, reason="Windows paths")
@pytest.mark.parametrize("path,code", DENY)
async def test_every_deny_class_refused(path, code):
    r = await _run({"path": path})
    assert r.error and r.data["code"] == code, (path, r.content)


@pytest.mark.skipif(not WIN, reason="junction")
async def test_junction_into_denied_dir_refused(tmp_path):
    target = tmp_path / "x" / ".ssh"
    target.mkdir(parents=True)
    (target / "id_x").write_text("k", encoding="utf-8")
    link = tmp_path / "innocent"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("cannot create junction")
    res = await _run({"path": str(link / "id_x")})
    assert res.error and res.data["code"] == "FS_DENY_SECRET" and "junction" in res.content
    lst = await _run({"path": str(tmp_path)})
    assert "innocent" not in lst.content and lst.data["hidden_by_policy"] >= 1


async def test_listing_hides_denied_entries(tmp_path):
    (tmp_path / "ok.txt").write_text("a", encoding="utf-8")
    (tmp_path / "server.pem").write_text("a", encoding="utf-8")
    (tmp_path / ".env").write_text("A=1", encoding="utf-8")
    r = await _run({"path": str(tmp_path)})
    assert "ok.txt" in r.content and "server.pem" not in r.content and ".env" not in r.content
    assert r.data["hidden_by_policy"] == 2


async def test_redaction_size_cap_binary(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("key sk-abcdefghijklmnopqrstuvwx\nghp_" + "a" * 36 + "\nAKIA" + "A" * 16 + "\npassword=hunter22\n"
                 + "f" * 64 + "\ngit " + "a" * 40 + "\nTok" + "aB3" * 20 + "\nplain line\n", encoding="utf-8")
    r = await _run({"path": str(f)})
    for leaked in ("sk-abcdefghij", "ghp_aaaa", "AKIAAAAA", "hunter22", "f" * 64, "aB3aB3aB3aB3aB3"):
        assert leaked not in r.content, leaked
    assert "plain line" in r.content and "a" * 40 in r.content and r.data["redactions"] >= 5
    big = tmp_path / "big.log"
    big.write_text("x " * 300_000, encoding="utf-8")
    r = await _run({"path": str(big)})
    assert r.truncated and r.data["bytes"] == pol.MAX_READ_BYTES and r.more.startswith("offset=")
    b = tmp_path / "a.bin"
    b.write_bytes(b"\x00\x01\x02" * 100)
    r = await _run({"path": str(b)})
    assert r.error and r.data["code"] == "FS_BINARY"


async def test_not_found_and_read_only_surface(tmp_path):
    assert (await _run({"path": str(tmp_path / "nope.txt")})).data["code"] == "FS_NOT_FOUND"
    spec = fr.SPECS[0]
    assert spec.category == "read" and not ({"content", "text", "data", "command"} & set(spec.input_schema))


async def test_agent_without_grant_refused(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    for agent in ({"permissions": {}}, {"permissions": {fr.PERM: False}}, {"permissions": {fr.PERM: "true"}}, {}):
        r = await _run({"path": str(f)}, agent)
        assert r.error and r.data["code"] == "FS_NOT_GRANTED"


async def test_operator_default_on_toggle_and_local_agents(env):
    await make_stack(env.client)
    v = (await env.client.get("/api/operator")).json()
    assert v["read_everything"] is True
    async with env.svc.db.session() as s:
        op = dict((await s.execute(sa.select(agents_t).where(agents_t.c.role == po.ROLE))).first()._mapping)
    assert op["permissions"][po.READ_PERM] is True and po.TOOL_NAME in op["tools"] and po.READ_MARK in op["system_prompt"]
    v = (await env.client.put("/api/operator", json={"read_everything": False})).json()
    assert v["read_everything"] is False
    assert (await env.client.get("/api/operator")).json()["read_everything"] is False   # explicit off sticks
    v = (await env.client.put("/api/operator", json={"read_everything": True})).json()
    assert v["read_everything"] is True
    async with env.svc.db.session() as s:
        rows = [dict(r._mapping) for r in (await s.execute(sa.select(agents_t))).fetchall()]
    for a in rows:
        has = (a["permissions"] or {}).get(po.READ_PERM) is True
        assert has == (po.TOOL_NAME in (a["tools"] or [])), a["id"]


async def test_effect_floor_not_granted_never_silent():
    spec = fr.SPECS[0]
    assert decide_effect(spec, {"path": "C:\\x"}, {"permissions": {fr.PERM: True}})[0] == "auto"
    assert decide_effect(spec, {"path": "C:\\x"}, {"permissions": {}})[0] == "ask"
