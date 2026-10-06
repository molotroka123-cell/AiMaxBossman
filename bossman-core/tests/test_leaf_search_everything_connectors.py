"""authored_by_lane (opsplug): bossman.search_everything.connectors - a secret is never indexed silently."""
import pytest

from bossman.search_everything.connectors import (
    SecretPolicy, filesystem_documents, history_documents, memory_documents,
)

AWS = "AKIA" + "IOSFODNN7EXAMPLE"          # ci-secret-scan: allow (documented AWS example key id)
PRIVATE = "-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----"


@pytest.mark.parametrize("path", [
    ".env", "config/.env.production", "id_rsa", "deploy/server.pem", "certs/a.KEY", "my_secret_notes.md",
    "passwords.txt", "node_modules/pkg/index.js", ".git/config", "secrets/db.yaml", "credentials.json",
    "a/.ssh/known_hosts",
])
def test_secret_looking_paths_are_denied(path):
    assert SecretPolicy().is_secret_path(path) is True


@pytest.mark.parametrize("path", ["README.md", "src/app.py", "docs/guide.rst", "notes/day1.txt"])
def test_ordinary_paths_are_allowed(path):
    assert SecretPolicy().is_secret_path(path) is False


def test_secret_content_is_detected_by_redactor_and_extra_signatures():
    p = SecretPolicy()
    assert p.is_secret_content("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789") is True
    assert p.is_secret_content(PRIVATE) is True
    assert p.is_secret_content(f"access key {AWS} here") is True
    assert p.is_secret_content("plain prose about browsers and memory") is False
    assert p.is_secret_content("") is False and p.is_secret_content(None) is False


def test_is_secret_combines_path_and_text():
    p = SecretPolicy()
    assert p.is_secret(path=".env") and p.is_secret(text=PRIVATE) and not p.is_secret(path="a.py", text="x = 1")


def test_filesystem_documents_skips_secret_files_big_files_and_unknown_suffixes(tmp_path):
    (tmp_path / "ok.md").write_text("browser memory notes", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "code.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=abc", encoding="utf-8")
    (tmp_path / "leaky.md").write_text(f"deploy with {AWS}", encoding="utf-8")        # allowed by name, secret inside
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    (tmp_path / "huge.txt").write_text("x" * 5000, encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("module.exports=1", encoding="utf-8")
    docs = list(filesystem_documents(tmp_path, project="p", max_bytes=2000))
    ids = sorted(d.id.replace("\\", "/") for d in docs)
    assert ids == ["ok.md", "sub/code.py"]
    assert all(d.source == "filesystem" and d.project == "p" and d.metadata["sensitivity"] == "normal" for d in docs)


def test_memory_connector_accepts_dicts_and_objects_and_skips_empty():
    class Rec:
        memory_id, text, kind, metadata = "m2", "object memory", "fact", {"sensitivity": "private"}

    docs = list(memory_documents([{"memory_id": "m1", "text": " dict memory ", "kind": "note"},
                                  {"memory_id": "m0", "text": "   "}, Rec()], project="p"))
    assert [d.id for d in docs] == ["memory://m1", "memory://m2"]
    assert docs[0].text == "dict memory" and docs[0].metadata == {"sensitivity": "normal", "kind": "note"}
    assert docs[1].metadata["sensitivity"] == "private"          # inherited, not overwritten


def test_history_connector_builds_refs_and_sources():
    docs = list(history_documents([{"id": "r1", "text": "ran pytest", "source": "shell"}, {"content": "second"},
                                   {"text": ""}], project="p"))
    assert [d.id for d in docs] == ["history://r1", "history://1"]
    assert docs[0].source == "shell" and docs[1].source == "tool_call" and docs[0].project == "p"
