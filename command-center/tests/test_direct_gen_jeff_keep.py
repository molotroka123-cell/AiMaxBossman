"""Owner 21.10: during a dense photo stream Jeff keeps answering. The «Gemma по запросу» prompt helper may be the SAME
Ollama model as Jeff (gemma … uncensored); after an assist, and before every generation job, direct-gen unloaded it and
Jeff went silent. Jeff's current model (<pit home>/jeff-model.json) and BOSSMAN_OLLAMA_KEEP_MODELS are never unloaded;
other helper models still are, and which model the helper uses does not change. No network, no real Ollama."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bcc.direct_gen.service import DirectGenService
from bcc.pit import jeff_model
from bcc.providers import ChatResult

GEMMA = "gemma4-31b-uncensored:latest"
REPO = Path(__file__).resolve().parents[2]


class _Bus:
    async def emit(self, *a, **kw):
        return None


class _Settings:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir


class _CoreSvc:
    def __init__(self, data_dir: Path):
        self.settings = _Settings(data_dir)
        self.bus = _Bus()


class _Helper:
    def __init__(self):
        self.used: list[str] = []

    async def chat(self, model, messages, **kw):
        self.used.append(model)
        return ChatResult(text="Edited: " + messages[-1]["content"], model=model)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    for name in ("BOSSMAN_PIT_LOCAL_MODELS", jeff_model.KEEP_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BOSSMAN_OLLAMA_URL", "http://127.0.0.1:9")      # never the owner's live Ollama
    data = tmp_path / "data"
    data.mkdir()
    service = DirectGenService(_CoreSvc(data), media_dir=tmp_path / "media")
    service.unloaded = []

    def hook(name):
        service.unloaded.append(name)
        return [name] if name else []
    service.ollama_unload = hook
    return service


async def _lookup(helper, name):
    return helper, name


async def test_assist_never_unloads_jeffs_current_model_and_still_uses_it(svc):
    jeff_model.set_override(svc.data_dir.parent, GEMMA)               # owner 21.10: Jeff on local Gemma
    helper = _Helper()
    svc.qwen_lookup = lambda: _lookup(helper, GEMMA)                    # «Gemma по запросу» = Jeff's model
    out = await svc.assist("owner", "a red kite over a beach")
    assert out["model"] == GEMMA and helper.used == [GEMMA]            # the helper choice is unchanged
    assert svc.unloaded == [] and out["unloaded"] is False             # Jeff's model stays loaded
    assert await svc._unload_ollama(None) == [] and svc.unloaded == []  # job path: last assist model = Jeff's


async def test_assist_still_unloads_another_helper_model(svc):
    jeff_model.set_override(svc.data_dir.parent, GEMMA)
    helper = _Helper()
    svc.qwen_lookup = lambda: _lookup(helper, "qwen3:8b")
    out = await svc.assist("owner", "a red kite over a beach")
    assert out["model"] == "qwen3:8b" and svc.unloaded == ["qwen3:8b"] and out["unloaded"] is True
    assert await svc._unload_ollama("Qwen3:8B") == ["Qwen3:8B"]         # before a job: still unloaded as today


async def test_every_photo_job_no_longer_evicts_jeff_via_the_helper_lookup(svc):
    """_run_sd unloads the job's assist model before EVERY sd job; a DIRECT job has none, so the fallback asked
    qwen_lookup() -> «gemma … uncensored» = Jeff's model, and each photo of the stream evicted Jeff."""
    jeff_model.set_override(svc.data_dir.parent, GEMMA)
    svc.qwen_lookup = lambda: _lookup(_Helper(), GEMMA)
    assert await svc._unload_ollama(None) == [] and svc.unloaded == []
    svc.qwen_lookup = lambda: _lookup(_Helper(), "qwen3:8b")             # a different helper: unloaded as before
    assert await svc._unload_ollama(None) == ["qwen3:8b"]


async def _assisted_photo_job(env, tmp_path, monkeypatch, free_gib):
    """A real queued photo job (fake sd-cli) whose assist model is Jeff's Gemma; returns (job, unload calls)."""
    from .test_direct_gen_photo import NEUTRAL as PHOTO_PROMPT, FakeSd, create as create_photo, finish as finish_photo, \
        install as install_sd
    for name in ("BOSSMAN_PIT_LOCAL_MODELS", jeff_model.KEEP_ENV):
        monkeypatch.delenv(name, raising=False)
    sd = install_sd(env, tmp_path, FakeSd, free=free_gib * 1024 ** 3)
    jeff_model.set_override(sd.data_dir.parent, GEMMA)
    calls: list = []
    sd.ollama_unload = lambda name: calls.append(name) or [name]
    helper = _Helper()
    sd.qwen_lookup = lambda: _lookup(helper, GEMMA)
    a = (await env.client.post("/api/direct-gen/assist", json={"prompt": PHOTO_PROMPT})).json()
    r = await create_photo(env, mode="ASSISTED", assist_id=a["assist_id"], assist_choice="suggested")
    assert r.status_code in (200, 201, 202), r.text
    return await finish_photo(env, r.json()["job_id"]), calls


async def test_photo_job_start_keeps_jeffs_model_when_ram_allows(env, tmp_path, monkeypatch):
    """Live 14:26: every queued photo with assist_model == Jeff's Gemma evicted it at job start."""
    job, calls = await _assisted_photo_job(env, tmp_path, monkeypatch, free_gib=64)
    assert job["status"] == "completed" and job["provenance"]["assist_model"] == GEMMA
    assert calls == []                                                   # neither after the assist nor at job start


async def test_photo_job_start_unloads_jeffs_model_only_when_free_ram_is_below_the_need(env, tmp_path, monkeypatch):
    job, calls = await _assisted_photo_job(env, tmp_path, monkeypatch, free_gib=1)   # need ~2 GiB (fake weights)
    assert calls == [GEMMA]                                              # unloaded once, at job start
    assert job["status"] == "failed" and job["error"]["code"] == "insufficient_memory"   # fake RAM stays low


async def test_jeffs_default_model_and_keep_env_are_protected(svc, monkeypatch):
    assert await svc._unload_ollama("bossman-uncensored-chat") == []    # no choice file: Jeff's configured default
    assert await svc._unload_ollama("qwen3:8b") == ["qwen3:8b"]
    monkeypatch.setenv(jeff_model.KEEP_ENV, "qwen3:8b, bge-m3")
    assert await svc._unload_ollama("qwen3:8b") == [] and await svc._unload_ollama("bge-m3:latest") == []
    assert await svc._unload_ollama("llava:13b") == ["llava:13b"]


async def test_real_http_path_never_posts_an_unload_for_jeffs_model(svc, monkeypatch):
    jeff_model.set_override(svc.data_dir.parent, GEMMA)
    del svc.ollama_unload
    posted: list[str] = []

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url):
            class R:
                def json(self):
                    return {"models": [{"name": GEMMA}, {"name": "qwen3:8b"}]}
            return R()

        async def post(self, url, json=None):
            posted.append((json or {}).get("model"))

            class R:
                status_code = 200
            return R()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    assert await svc._unload_ollama(GEMMA) == [] and posted == []
    assert await svc._unload_ollama("qwen3:8b") == ["qwen3:8b"] and posted == ["qwen3:8b"]


def test_keep_models_follow_repointed_jeff_dir_and_never_raise(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_PIT_LOCAL_MODELS", raising=False)
    monkeypatch.delenv(jeff_model.KEEP_ENV, raising=False)
    cc, other = tmp_path / "cc", tmp_path / "jeff"
    (cc / "pit-v1.7").mkdir(parents=True)
    (cc / "pit-v1.7" / "config.json").write_text(json.dumps({"data_dir": str(other), "local_models": ["qwen3:8b"]}))
    assert jeff_model.effective_model(cc) == "qwen3:8b"
    jeff_model.set_override(other, GEMMA)
    assert jeff_model.effective_model(cc) == GEMMA and jeff_model.ollama_keep_models(cc) == {GEMMA}
    (cc / "pit-v1.7" / "config.json").write_text("[1, 2")                # broken config: default, no exception
    assert jeff_model.ollama_keep_models(cc) == {"bossman-uncensored-chat:latest"}
    assert jeff_model.ollama_keep_models(None) == set()
    assert jeff_model.ollama_key("hf.co/x/Model") == "hf.co/x/model:latest" and jeff_model.ollama_key("") == ""


@pytest.mark.parametrize("layout", ["empty", "config", "choice", "banned_choice", "repointed"])
def test_governor_mirror_resolves_the_same_jeff_model(tmp_path, monkeypatch, layout):
    """tools/resource_governor is stdlib-only and mirrors effective_model; both must name the same model."""
    monkeypatch.delenv("BOSSMAN_PIT_LOCAL_MODELS", raising=False)
    sys.path.insert(0, str(REPO / "tools" / "resource_governor"))
    try:
        import governor
    finally:
        sys.path.remove(str(REPO / "tools" / "resource_governor"))
    cc = tmp_path / "cc"
    pit = cc / "pit-v1.7"
    pit.mkdir(parents=True)
    if layout in ("config", "choice", "banned_choice"):
        (pit / "config.json").write_text(json.dumps({"local_models": ["qwen3:8b"]}))
    if layout == "choice":
        jeff_model.set_override(cc, GEMMA)
    if layout == "banned_choice":
        (pit / "jeff-model.json").write_text(json.dumps({"local_model": "liquid/lfm-2"}))
    if layout == "repointed":
        other = tmp_path / "jeff"
        (pit / "config.json").write_text(json.dumps({"data_dir": str(other)}))
        jeff_model.set_override(other, GEMMA)
    assert governor.jeff_effective_model(pit, env={}) == jeff_model.effective_model(cc)
