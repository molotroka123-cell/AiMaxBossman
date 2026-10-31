"""Owner 31.10: «как у тебя и чата гпт … присылать файлы (без ограничения) и возможность скачать».

The phone streams any file to Bossman (no base64, no 8 MB cap, no type filter) and downloads chat files back."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import conftest

UI = Path(__file__).resolve().parents[1] / "bossmobile" / "ui"
AID = "0123456789abcdef012345"


@pytest.fixture
def files_env(tmp_path, monkeypatch):
    real = conftest.FakeBossman.handler

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/chat/attachments" and request.method == "POST":
            self.uploaded = {"query": dict(request.url.params), "size": len(request.content),
                             "ctype": request.headers.get("content-type")}
            return httpx.Response(201, json={"attachment": {"id": AID, "name": request.url.params["filename"],
                                                            "size": len(request.content), "kind": "binary"}})
        if path == f"/api/chat/attachments/{AID}/file":
            return httpx.Response(200, content=b"PDFDATA", headers={
                "content-type": "application/pdf", "content-disposition": 'attachment; filename="report.pdf"'})
        if path.startswith("/api/chat/attachments/") and path.endswith("/file"):
            return httpx.Response(404, json={"error": {"message": "нет"}})
        return real(self, request)

    monkeypatch.setattr(conftest.FakeBossman, "handler", handler)
    env = conftest.Env(tmp_path)
    env.login()
    return env


def test_a_20_mb_video_streams_through_with_no_cap(files_env):
    blob = b"\x00\x01" * (10 * 1024 * 1024)                   # 20 MB: the old phone cap was 8 MB
    r = files_env.post("/m/api/chat/upload", params={"filename": "clip.mov"}, content=blob,
                       headers={"Content-Type": "video/quicktime"})
    assert r.status_code == 201, r.text
    assert r.json()["attachment"]["id"] == AID
    up = files_env.fake.uploaded
    assert up["size"] == len(blob) and up["query"]["filename"] == "clip.mov" and up["ctype"] == "video/quicktime"


def test_upload_needs_login_and_csrf(files_env):
    r = files_env.c.post("/m/api/chat/upload", params={"filename": "a.bin"}, content=b"x")
    assert r.status_code in (401, 403)


def test_download_streams_the_file_with_its_name(files_env):
    r = files_env.c.get(f"/m/api/chat/attachments/{AID}/file")
    assert r.status_code == 200 and r.content == b"PDFDATA"
    assert "report.pdf" in r.headers["content-disposition"] and r.headers["x-content-type-options"] == "nosniff"
    assert files_env.c.get("/m/api/chat/attachments/" + "f" * 22 + "/file").status_code == 404
    assert files_env.c.get("/m/api/chat/attachments/..%2F..%2Fbcc.db/file").status_code == 404


def test_ui_has_no_type_filter_no_8mb_cap_and_download_links():
    html = (UI / "index.html").read_text(encoding="utf-8")
    js = (UI / "app.js").read_text(encoding="utf-8")
    tag = next(line for line in html.splitlines() if 'id="chat-file-input"' in line)
    assert "accept=" not in tag
    assert "больше 8 МБ`" not in js and "/chat/upload?" in js and "x.send(file)" in js
    assert "/m/api/chat/attachments/${encodeURIComponent(f.id)}/file" in js and "showAgentFiles(b, turn.files)" in js
