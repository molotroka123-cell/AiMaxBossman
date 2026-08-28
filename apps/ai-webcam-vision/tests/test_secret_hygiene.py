"""Credential hygiene: the primary requirement of this application.

The canary test at the bottom is the load-bearing one: a uniquely identifiable
password is pushed through a real failing connection, and then every emission
channel is searched for it.
"""

from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path

import pytest

from ai_webcam_vision.config import Settings
from ai_webcam_vision.errors import CaptureError, VisionError
from ai_webcam_vision.logging_setup import configure_logging
from ai_webcam_vision.secretstore import (
    Secret,
    SecretUrl,
    StreamTarget,
    build_stream_url,
    register_secret_value,
    scrub,
)


def test_secret_never_renders_its_value():
    secret = Secret("hunter2-very-secret-value", "camera_password")
    rendered = [repr(secret), str(secret), f"{secret}", f"{secret!r}", f"{secret!s}", format(secret, ">30")]
    for text in rendered:
        assert "hunter2-very-secret-value" not in text
    assert secret.reveal() == "hunter2-very-secret-value"


def test_secret_is_not_serialisable():
    import pickle

    with pytest.raises(TypeError):
        pickle.dumps(Secret("hunter2-very-secret-value"))


def test_secret_inside_container_repr_is_safe():
    secret = Secret("container-secret-value", "camera_password")
    assert "container-secret-value" not in repr({"password": secret})
    assert "container-secret-value" not in repr([secret])


def test_url_assembly_masks_empty_username():
    """The legacy pack leaked here: an empty username defeated its regex."""
    target = StreamTarget("rtsp", "10.0.0.5", 554, "stream2")
    url = build_stream_url(target, "", Secret("EmptyUserLeakProbe", "camera_password"))
    assert "EmptyUserLeakProbe" not in url.public
    assert url.public == "rtsp://***:***@10.0.0.5:554/stream2"
    assert url.reveal().startswith("rtsp://:EmptyUserLeakProbe@")


def test_url_assembly_percent_encodes_and_masks():
    target = StreamTarget("rtsp", "10.0.0.5", 554, "stream2")
    url = build_stream_url(target, "user@name", Secret("p@ss/word:1234", "camera_password"))
    assert "p%40ss%2Fword%3A1234" in url.reveal()
    assert "p@ss/word:1234" not in url.public
    assert "p%40ss%2Fword%3A1234" not in url.public


def test_scrub_removes_registered_literal_outside_url_form():
    """The pack only masked URL shapes; bare occurrences went through."""
    register_secret_value("BareLiteralSecret123")
    assert "BareLiteralSecret123" not in scrub("ffmpeg: auth failed for BareLiteralSecret123")


def test_scrub_masks_userinfo_for_any_scheme():
    assert scrub("http://u:p@example.test/x") == "http://***:***@example.test/x"
    assert scrub("rtsp://:onlypass@h/x") == "rtsp://***:***@h/x"


def test_errors_are_scrubbed_at_construction():
    register_secret_value("ErrorPathSecret456")
    error = CaptureError("connection to rtsp://u:ErrorPathSecret456@host/stream2 failed")
    assert "ErrorPathSecret456" not in str(error)
    assert "ErrorPathSecret456" not in repr(error)
    assert "ErrorPathSecret456" not in "".join(error.args)


def test_settings_public_dict_has_no_secret_values(base_env):
    env = dict(base_env)
    env["AWV_CAMERA_PASSWORD"] = "PublicDictProbe789"
    env["AWV_CRM_TOKEN"] = "CrmTokenProbe789"
    env["AWV_API_TOKEN"] = "ApiTokenProbe789"
    settings = Settings.from_env(env)
    rendered = json.dumps(settings.public_dict())
    assert "PublicDictProbe789" not in rendered
    assert "CrmTokenProbe789" not in rendered
    assert "ApiTokenProbe789" not in rendered
    assert settings.public_dict()["camera"]["password_configured"] is True


def test_settings_repr_hides_secrets(base_env):
    env = dict(base_env)
    env["AWV_CAMERA_PASSWORD"] = "ReprProbeSecret000"
    settings = Settings.from_env(env)
    assert "ReprProbeSecret000" not in repr(settings)


def test_logging_filter_scrubs_message_and_args(tmp_path: Path):
    log_file = tmp_path / "redaction.log"
    logger = configure_logging("DEBUG", log_file)
    register_secret_value("LogFilterProbe321")
    logger.info("connecting to %s", "rtsp://u:LogFilterProbe321@10.0.0.5:554/stream2")
    try:
        raise RuntimeError("boom rtsp://u:LogFilterProbe321@10.0.0.5/stream2")
    except RuntimeError:
        logger.exception("capture failed")
    for handler in logger.handlers:
        handler.flush()
    text = log_file.read_text(encoding="utf-8")
    assert "LogFilterProbe321" not in text
    assert "***" in text
    logging.getLogger("ai_webcam_vision").handlers.clear()


# --------------------------------------------------------------------------
# The canary
# --------------------------------------------------------------------------

# A password that exists nowhere else in the world. If this string turns up in
# a log line, an API response, an exception, or a state file, the application
# leaks credentials.
CANARY_VALUE = "Tapo-Canary-4f19ab7c-NEVER-EMIT"  # ci-secret-scan: allow


def _collect_files(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for path in root.rglob("*"):
        if path.is_file():
            out[str(path)] = path.read_bytes().decode("utf-8", "replace")
    return out


def test_canary_password_never_escapes(tmp_path, ffmpeg_path, monkeypatch):
    """Full failure scenario against a real, refused RTSP endpoint.

    ffmpeg itself prints the credentialed URL to stderr, so this exercises the
    exact path where the credential wants to escape.
    """
    from fastapi.testclient import TestClient

    from ai_webcam_vision.api import build_app
    from ai_webcam_vision.runtime.service import VisionService

    from conftest import closed_port, wait_for_job

    state_dir = tmp_path / "state"
    log_file = tmp_path / "app.log"
    env = {
        "AWV_ROOM_ID": "canary-room",
        "AWV_CAMERA_MODE": "rtsp",
        "AWV_CAMERA_HOST": "127.0.0.1",
        "AWV_CAMERA_PORT": str(closed_port()),
        "AWV_CAMERA_USERNAME": "canary_user",
        "AWV_CAMERA_PASSWORD": CANARY_VALUE,
        "AWV_FFMPEG_PATH": ffmpeg_path,
        "AWV_STATE_DIR": str(state_dir),
        "AWV_LOG_FILE": str(log_file),
        "AWV_LOG_LEVEL": "DEBUG",
        "AWV_CONNECT_TIMEOUT_SECONDS": "3",
        "AWV_CAPTURE_TIMEOUT_SECONDS": "5",
        "AWV_RETRY_MAX_ATTEMPTS": "2",
        "AWV_RETRY_BASE_DELAY_SECONDS": "0",
    }
    settings = Settings.from_env(env)
    service = VisionService(settings)
    app = build_app(settings, service=service)

    emissions: list[str] = []

    with TestClient(app) as client:
        # 1. A job that must fail against the refused port.
        created = client.post("/api/v1/jobs", json={"type": "baseline"})
        assert created.status_code == 202
        job = wait_for_job(client, created.json()["id"])
        assert job["status"] == "failed"
        assert job["error"], "the failure must be reported, not swallowed"
        emissions.append(json.dumps(job))

        # 2. A probe job, which renders ffmpeg stderr into a result payload.
        probe = client.post("/api/v1/jobs", json={"type": "probe"})
        probe_job = wait_for_job(client, probe.json()["id"])
        emissions.append(json.dumps(probe_job))

        # 3. Every read endpoint of the contract.
        for path in (
            "/healthz",
            "/api/v1/health",
            "/api/v1/capabilities",
            "/api/v1/metrics",
            "/api/v1/jobs",
            "/api/v1/artifacts",
            "/api/v1/rooms/canary-room/metrics/today",
        ):
            response = client.get(path)
            assert response.status_code == 200, path
            emissions.append(response.text)

        # 4. An error surfaced directly by the HTTP layer.
        sample = client.post("/api/v1/jobs", json={"type": "sample"})
        sample_job = wait_for_job(client, sample.json()["id"])
        assert sample_job["status"] == "failed"
        emissions.append(json.dumps(sample_job))

    # 5. Everything written to disk, including the SQLite database.
    for name, content in _collect_files(state_dir).items():
        emissions.append(name)
        emissions.append(content)
    if log_file.exists():
        emissions.append(log_file.read_text(encoding="utf-8"))

    haystack = "\n".join(emissions)
    assert CANARY_VALUE not in haystack, "camera password escaped into an emitted channel"
    assert "canary_user:" not in haystack
    # And the evidence that the scenario really ran: a failure was reported.
    assert "capture_failed" in haystack or "capture_timeout" in haystack
    assert "***" in haystack


def test_canary_traceback_is_scrubbed(tmp_path, ffmpeg_path):
    """The exception path alone, without the HTTP layer."""
    import asyncio

    from conftest import closed_port

    env = {
        "AWV_CAMERA_MODE": "rtsp",
        "AWV_CAMERA_HOST": "127.0.0.1",
        "AWV_CAMERA_PORT": str(closed_port()),
        "AWV_CAMERA_USERNAME": "canary_user",
        "AWV_CAMERA_PASSWORD": CANARY_VALUE,
        "AWV_FFMPEG_PATH": ffmpeg_path,
        "AWV_STATE_DIR": str(tmp_path / "state"),
        "AWV_CONNECT_TIMEOUT_SECONDS": "3",
        "AWV_CAPTURE_TIMEOUT_SECONDS": "5",
    }
    settings = Settings.from_env(env)
    from ai_webcam_vision.transport import build_source

    source = build_source(settings)

    async def run():
        with pytest.raises(VisionError) as excinfo:
            await source.grab()
        exc = excinfo.value
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    text = asyncio.run(run())
    assert CANARY_VALUE not in text
    assert "***" in text or "Connection refused" in text
