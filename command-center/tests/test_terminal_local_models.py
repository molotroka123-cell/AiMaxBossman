"""The CMD catalog shows live Ollama tags without changing the saved registry."""
from bcc.terminal_cli.cli import list_items


class FakeClient:
    def get(self, path):
        if path == "/api/agents":
            return []
        if path == "/api/providers":
            return [
                {"id": 1, "kind": "openai_compat", "base_url": "http://127.0.0.1:11434/v1"},
                {"id": 2, "kind": "openai_compat", "base_url": "http://127.0.0.1:8081/v1"},
            ]
        if path == "/api/models":
            return ([{"id": i, "provider_id": 1, "name": f"ollama-{i}:latest",
                      "alias": f"ollama-{i}", "kind": "local"} for i in range(1, 5)] +
                    [{"id": 9, "provider_id": 2, "name": "stale-llama", "alias": "stale",
                      "kind": "local"}])
        raise AssertionError(path)

    def post(self, path, body):
        assert path == "/api/models/discover" and body == {}
        return {"endpoints": [
            {"label": "Ollama", "base_url": "http://127.0.0.1:11434/v1", "ok": True,
             "models": [f"ollama-{i}:latest" for i in range(1, 6)]},
            {"label": "local-main", "base_url": "http://127.0.0.1:8081/v1", "ok": False,
             "models": []},
        ]}


def test_cmd_catalog_includes_unregistered_ollama_and_marks_stale_endpoint():
    rows = list_items(FakeClient(), "models")
    ollama = [row for row in rows if row["name"].startswith("ollama-")]
    assert len(ollama) == 5
    assert sum(row["registered"] is False for row in ollama) == 1
    assert all(row["catalog_status"] == "listed" for row in ollama)
    stale = next(row for row in rows if row["name"] == "stale-llama")
    assert stale["catalog_status"] == "unavailable"
    assert stale["registered"] is True
