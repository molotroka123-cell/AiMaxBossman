"""Owner 22.10: free OpenRouter models rotate — Bossman must swap dead ones itself, never to a paid id."""
from bcc import free_models as fm

LIVE = {"data": [{"id": "poolside/laguna-s-2.1:free"}, {"id": "cohere/north-mini-code:free"},
                 {"id": "nvidia/nemotron-3.5-content-safety:free"}, {"id": "openai/gpt-5:paid"}]}


def _workers():
    return {"dead": {"endpoint": "https://openrouter.ai/api/v1", "model": "qwen/qwen3-coder:free"},
            "alive": {"endpoint": "https://openrouter.ai/api/v1", "model": "cohere/north-mini-code:free"},
            "nim": {"endpoint": "https://integrate.api.nvidia.com/v1", "model": "nvidia/x"}}


def test_retired_free_model_is_swapped_for_live_free(tmp_path):
    w = _workers()
    st = fm.refresh_workers(w, "k", tmp_path, force=True, fetch=lambda u, k: LIVE, probe=lambda m, k: "ok")
    assert w["dead"]["model"] == "poolside/laguna-s-2.1:free" and w["dead"]["configured_model"] == "qwen/qwen3-coder:free"
    assert w["alive"]["model"] == "cohere/north-mini-code:free"
    assert w["nim"]["model"] == "nvidia/x"                                    # non-OpenRouter untouched
    assert st["changes"][0]["reason"].startswith("retired")
    assert (tmp_path / "free-models" / "status.json").is_file()


def test_rate_limited_model_falls_through_and_never_picks_paid_or_safety():
    w = _workers()
    probe = lambda m, k: "429" if m == "poolside/laguna-s-2.1:free" else "ok"  # noqa: E731
    w["alive"]["model"] = "poolside/laguna-s-2.1:free"
    fm.refresh_workers(w, "k", None, force=True, fetch=lambda u, k: LIVE, probe=probe)
    assert w["alive"]["model"] == "cohere/north-mini-code:free"
    for spec in w.values():
        assert "content-safety" not in spec["model"] and ":paid" not in spec["model"]


def test_no_live_replacement_keeps_configured():
    w = {"x": {"endpoint": "https://openrouter.ai/api/v1", "model": "a/b:free"}}
    st = fm.refresh_workers(w, "k", None, force=True, fetch=lambda u, k: {"data": []}, probe=lambda m, k: "404")
    assert w["x"]["model"] == "a/b:free" and "no live free replacement" in st["workers"]["x"]["reason"]
