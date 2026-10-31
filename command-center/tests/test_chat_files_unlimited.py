"""Owner 31.10: «присылать файлы (без ограничения) и возможность скачать» — no fixed size cap (only a free-disk
reserve), a download route, and `chat.share_file` for files the agent hands back."""
from __future__ import annotations

from collections import namedtuple
from pathlib import Path

from bcc.features import chat_share_file as sf
from bcc.features import chat_threads as ct

Usage = namedtuple("Usage", "total used free")


async def _upload(env, filename: str, content: bytes):
    return await env.client.post("/api/chat/attachments", params={"filename": filename}, content=content)


async def test_no_fixed_cap_a_big_file_is_stored_and_downloads_byte_exact(env, monkeypatch):
    monkeypatch.setattr(ct, "ATTACHMENT_ANALYZE_MAX_BYTES", 1024)       # «big» without writing 64 MB in a test
    blob = bytes(range(256)) * 40                                        # 10 KB, over the analysis size
    up = await _upload(env, "video.mp4", blob)
    assert up.status_code == 201, up.text
    meta = up.json()
    assert meta["size"] == len(blob) and meta["kind"] == "binary", "too big to read: stored as-is, not parsed"
    got = await env.client.get(f"/api/chat/attachments/{meta['id']}/file")
    assert got.status_code == 200 and got.content == blob
    assert "attachment" in got.headers["content-disposition"] and "video.mp4" in got.headers["content-disposition"]
    assert got.headers["x-content-type-options"] == "nosniff"


async def test_html_is_never_served_inline(env):
    up = (await _upload(env, "page.html", b"<script>alert(1)</script>")).json()
    got = await env.client.get(f"/api/chat/attachments/{up['id']}/file", params={"inline": 1})
    assert got.headers["content-disposition"].startswith("attachment")


async def test_the_only_floor_is_free_disk(env, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(10 ** 12, 0, ct.ATTACHMENT_DISK_RESERVE_BYTES + 100))
    assert (await _upload(env, "ok.bin", b"x" * 100)).status_code == 201
    full = await _upload(env, "big.bin", b"x" * 101)
    assert full.status_code == 507 and full.json()["error"]["code"] == "CHAT_ATTACHMENT_DISK_FULL"
    root = env.settings.data_dir / "chat" / "attachments"
    assert not any(p.name.endswith(".part") for p in root.iterdir()), "no .part left behind"


async def test_unknown_attachment_download_is_404(env):
    assert (await env.client.get("/api/chat/attachments/" + "0" * 22 + "/file")).status_code == 404


async def test_agent_shares_a_file_it_lists_under_the_turn_and_downloads(env, tmp_path):
    src = tmp_path / "work" / "report.csv"
    src.parent.mkdir()
    src.write_bytes(b"a,b\n1,2\n")
    pub = ct.share_file_into_chat(env.svc, src, task_id=777, note="итоги")
    assert pub["origin"] == "agent" and pub["name"] == "report.csv" and pub["note"] == "итоги"
    assert [f["id"] for f in ct._agent_files(env.svc, 777)] == [pub["id"]]
    assert ct._agent_files(env.svc, 778) == []
    got = await env.client.get(f"/api/chat/attachments/{pub['id']}/file")
    assert got.content == b"a,b\n1,2\n"


def test_share_refuses_secrets_bossman_data_relative_and_missing(tmp_path):
    data = tmp_path / "data"
    (data / "vault").mkdir(parents=True)
    (data / "bcc.db").write_bytes(b"x")
    ok = tmp_path / "ok.txt"
    ok.write_text("ok", encoding="utf-8")
    assert sf.refusal(str(ok), data) is None
    assert "данных Bossman" in sf.refusal(str(data / "bcc.db"), data)
    assert sf.refusal("report.pdf", data).startswith("нужен полный путь")
    assert sf.refusal(str(tmp_path / "nope.txt"), data).startswith("файл не найден")
    assert sf.refusal(str(tmp_path), data).startswith("это не файл")
    assert sf.refusal(str(Path.home() / ".ssh" / "id_ed25519"), data)
    assert sf.refusal(str(sf.MOBILE_DATA / "pairing.json"), data)
