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
