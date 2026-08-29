"""Обнаружение локальных моделей: опрос endpoint'ов и скан диска."""
import asyncio
import json
import os
from pathlib import Path
from unittest import mock

import httpx
import pytest

from bcc import discovery
_RUNNER_HANG = pytest.mark.skipif(
    os.environ.get("BCC_CI_SKIP_RUNNER_HANGS") == "1",
    reason="зависает ТОЛЬКО на GitHub-раннере (asyncio teardown), локально идёт за ~2.5с; открыт баг на воспроизведение — см. docs/context/NEXT.md")

from bcc.discovery import (KNOWN_ENDPOINTS, _scan_files, default_model_dirs, discover,
                           expand_dir, model_dirs_from_env)


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
    (tmp_path / "readme.txt").write_text("не модель", encoding="utf-8")
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


# ---------------------------------------------------------------- занятый порт

@_RUNNER_HANG
async def test_open_port_that_stays_silent_is_not_called_absent():
    """Дефект с боевой машины: 11434 держал форвардер WSL2.

    Он ПРИНИМАЕТ соединение и молчит, а живая Ollama слушала 11435. BCC писал
    и про занятый, и про свободный порт один текст «не ответил за 2.5 с» —
    диагноз указывал на отсутствующий сервер вместо занятого порта, и найти
    настоящую причину по нему было нельзя.
    """
    async def silent(reader, writer):
        await asyncio.sleep(30)           # молчит ровно как форвардер

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        result = await discover(endpoints=[("занятый", f"http://127.0.0.1:{port}/v1")],
                                model_dirs=[])
        detail = result["endpoints"][0]["detail"]
        assert result["online"] == 0
        assert "занят другим процессом" in detail, detail
        assert str(port) in detail
    finally:
        server.close()
        await server.wait_closed()


async def test_closed_port_says_the_server_is_not_running():
    """Свободный порт — это «не запущено», и текст обязан отличаться."""
    sock = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = sock.sockets[0].getsockname()[1]
    sock.close()
    await sock.wait_closed()              # порт освободили — теперь он закрыт

    result = await discover(endpoints=[("свободный", f"http://127.0.0.1:{port}/v1")],
                            model_dirs=[])
    detail = result["endpoints"][0]["detail"]
    assert "не запущен" in detail and "закрыт" in detail, detail


def test_ollama_spare_port_is_probed_by_default():
    """Если 11434 занят, Ollama уходит на 11435 — иначе её не найти никогда."""
    urls = [u for _, u in KNOWN_ENDPOINTS]
    assert "http://127.0.0.1:11434/v1" in urls
    assert "http://127.0.0.1:11435/v1" in urls


# ---------------------------------------------------------------- каталоги весов

def test_windows_paths_survive_the_env_split(monkeypatch):
    """`C:\\Users\\...` нельзя резать по ':' — путь распадался на 'C' и остаток."""
    monkeypatch.setattr(os, "pathsep", ";")
    monkeypatch.setenv("BCC_MODELS_DIRS", r"C:\Users\timur\.ollama\models;D:\models")
    assert model_dirs_from_env() == [r"C:\Users\timur\.ollama\models", r"D:\models"]


def test_empty_env_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("BCC_MODELS_DIRS", "   ")
    assert "~/.ollama/models" in model_dirs_from_env()


def test_ollama_store_is_read_from_manifests_not_from_gguf(tmp_path):
    """Ollama хранит веса блобами по хешу — маска `*.gguf` не видит ничего.

    Совет «укажи BCC_MODELS_DIRS на каталог Ollama» до этого давал пустой
    список: файлов с таким расширением там просто нет.
    """
    manifest = tmp_path / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5" / "7b"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"layers": [
        {"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:a", "size": 4_700_000_000},
        {"mediaType": "application/vnd.ollama.image.params", "digest": "sha256:b", "size": 100},
    ]}), encoding="utf-8")
    (tmp_path / "blobs").mkdir()
    (tmp_path / "blobs" / "sha256-a").write_bytes(b"x")

    found = _scan_files([str(tmp_path)])
    assert len(found) == 1
    assert found[0]["name"] == "qwen2.5:7b"
    assert found[0]["runner"] == "ollama"
    assert found[0]["size_gb"] == 4.7


def test_default_dirs_follow_the_operating_system(monkeypatch):
    """На Windows Linux-каталоги не существуют — скан находил ровно ноль.

    `/opt/bossman/models`, `/models`, `~/.cache/lm-studio/models` — путей с
    такими именами на машине разработчика нет и быть не может, и обнаружение
    честно показывало «моделей нет» при установленных Ollama и LM Studio.

    `os.name` подменяется ТОЛЬКО на время самого вызова: пока он равен "nt",
    `pathlib.Path()` на Linux падает с NotImplementedError, и упавшая проверка
    уронила бы не тест, а сам pytest при печати отчёта.
    """
    monkeypatch.delenv("BCC_MODELS_DIRS", raising=False)

    with mock.patch.object(os, "name", "nt"):
        win = default_model_dirs()
        win_env = model_dirs_from_env()
    with mock.patch.object(os, "name", "posix"):
        posix = default_model_dirs()

    assert win == win_env                        # пустой env — те же значения
    assert r"%USERPROFILE%\.ollama\models" in win
    assert r"%APPDATA%\LM Studio\models" in win
    assert "~/models" in win
    assert not [p for p in win if p.startswith("/")]   # ни одного Linux-пути

    assert posix == ["/opt/bossman/models", "/models", "~/models",
                     "~/.cache/lm-studio/models", "~/.ollama/models"]


def test_scanned_dirs_expand_variables_and_tilde(tmp_path, monkeypatch):
    """`%APPDATA%` без раскрытия — это каталог с процентами в имени, а не путь.

    Форма `$VAR` раскрывается и ntpath, и posixpath, поэтому проверяется здесь;
    `%VAR%` — забота ntpath.expandvars, и на Linux её не выполнить.
    """
    (tmp_path / "weights.gguf").write_bytes(b"x" * 1024)
    monkeypatch.setenv("BCC_TEST_MODELS_ROOT", str(tmp_path))

    assert expand_dir("~") == Path.home()
    assert expand_dir("$BCC_TEST_MODELS_ROOT") == tmp_path
    found = _scan_files(["$BCC_TEST_MODELS_ROOT"])
    assert [f["path"] for f in found] == [str(tmp_path / "weights.gguf")]


def test_one_file_is_listed_once(tmp_path, monkeypatch):
    """Регистронезависимая ФС: `*.gguf` и `*.GGUF` находят ОДИН и тот же файл.

    На NTFS и APFS модель попадала в список дважды — с тем же путём и тем же
    размером, как будто весов на диске вдвое больше, чем есть.
    """
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "Qwen3-35B-Q8_0.gguf").write_bytes(b"x" * 1024)
    only = str(tmp_path / "sub" / "Qwen3-35B-Q8_0.gguf")

    # регистронезависимую ФС на Linux не изобразить, но её эффект —
    # «две маски возвращают один и тот же путь» — воспроизводится точно
    monkeypatch.setattr(discovery, "MODEL_FILE_GLOBS", ["*.gguf", "*.GGUF", "*.gguf"])
    assert [f["path"] for f in _scan_files([str(tmp_path)])] == [only]

    # один и тот же каталог, названный в списке дважды, тоже не удваивает вывод
    assert [f["path"] for f in _scan_files([str(tmp_path), str(tmp_path) + os.sep])] == [only]


def test_ollama_scan_skips_manifests_without_a_model_layer(tmp_path):
    manifest = tmp_path / "manifests" / "library" / "broken" / "latest"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"layers": [{"mediaType": "application/vnd.ollama.image.license"}]}',
                        encoding="utf-8")
    (tmp_path / "manifests" / "library" / "junk.txt").write_text("не json", encoding="utf-8")
    (tmp_path / "blobs").mkdir()
    assert _scan_files([str(tmp_path)]) == []
