"""Обнаружение локальных моделей: опрос endpoint'ов и скан диска."""
import httpx

from bcc.discovery import _scan_files, discover


def _transport() -> httpx.MockTransport:
    """8080 отвечает списком моделей, 11434 — недоступен."""
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.port == 8080:
            return httpx.Response(200, json={"data": [
                {"id": "qwen3-35b"}, {"id": "qwen3-coder-30b"}]})
        raise httpx.ConnectError("connection refused", request=request)
    return httpx.MockTransport(handle)


async def test_probe_marks_online_and_offline(tmp_path):
    result = await discover(
        endpoints=[("llama.cpp", "http://127.0.0.1:8080/v1"),
                   ("Ollama", "http://127.0.0.1:11434/v1")],
        model_dirs=[str(tmp_path)], transport=_transport())
    by_label = {r["label"]: r for r in result["endpoints"]}
    assert by_label["llama.cpp"]["ok"] is True
    assert by_label["llama.cpp"]["models"] == ["qwen3-35b", "qwen3-coder-30b"]
    assert by_label["Ollama"]["ok"] is False and by_label["Ollama"]["detail"]
    assert result["online"] == 1
    # живые endpoint'ы — первыми
    assert result["endpoints"][0]["ok"] is True


async def test_registered_providers_marked(tmp_path):
    result = await discover(
        endpoints=[("llama.cpp", "http://127.0.0.1:8080/v1")],
        known_providers=[{"base_url": "http://127.0.0.1:8080/v1"}],
        model_dirs=[str(tmp_path)], transport=_transport())
    assert result["endpoints"][0]["registered"] is True


async def test_extra_url_probed(tmp_path):
    result = await discover(
        extra_urls=["http://127.0.0.1:8080/v1"],
        endpoints=[], model_dirs=[str(tmp_path)], transport=_transport())
    assert result["endpoints"][0]["ok"] and result["endpoints"][0]["label"] == "указан вручную"


def test_scan_finds_gguf(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "Qwen3-35B-Q8_0.gguf").write_bytes(b"x" * 1024)
    (tmp_path / "readme.txt").write_text("не модель")
    files = _scan_files([str(tmp_path)])
    assert len(files) == 1
    assert files[0]["path"].endswith("Qwen3-35B-Q8_0.gguf")
    assert files[0]["size_gb"] == 0.0  # 1 КБ округляется до 0.00 ГБ


def test_scan_missing_dir_is_empty():
    assert _scan_files(["/nonexistent/dir"]) == []


# ---------------------------------------------------------------- прокси и localhost

def test_local_urls_never_go_through_a_proxy():
    """Дефект с боевой машины владельца: все локальные endpoint'ы показывали
    «не ответил за 2.5 с» при работающей Ollama.

    Причина: httpx по умолчанию читает HTTP_PROXY/HTTPS_PROXY из окружения, и
    запрос к 127.0.0.1:11434 уходил на прокси, который про этот адрес ничего не
    знает — соединение висит до таймаута. Диагноз выходил ложный: «модель не
    отвечает» при полностью исправной модели.
    """
    from bcc.providers import OpenAICompatAdapter, is_local_url

    for url in ("http://127.0.0.1:11434/v1", "http://localhost:1234/v1",
                "http://192.168.1.50:8080/v1", "http://host.docker.internal:8080/v1",
                "http://[::1]:8080/v1"):
        assert is_local_url(url) is True, url
        assert OpenAICompatAdapter(base_url=url)._client(2.0).trust_env is False, url

    # внешние провайдеры прокси по-прежнему используют: в корпоративной сети
    # без него до OpenRouter не достучаться
    for url in ("https://openrouter.ai/api/v1", "https://api.anthropic.com"):
        assert is_local_url(url) is False, url
        assert OpenAICompatAdapter(base_url=url)._client(2.0).trust_env is True, url


def test_local_probe_ignores_proxy_env(monkeypatch):
    """Прокси в окружении не должен ломать обнаружение локальных моделей."""
    from bcc.providers import OpenAICompatAdapter

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")

    local = OpenAICompatAdapter(base_url="http://127.0.0.1:11434/v1")._client(2.0)
    assert local.trust_env is False       # к Ollama — напрямую
    remote = OpenAICompatAdapter(base_url="https://openrouter.ai/api/v1")._client(2.0)
    assert remote.trust_env is True       # наружу — через прокси, как настроено
