"""SearXNG optional configuration cannot crash boot or silently change target."""
import pytest

from bcc.features import osiris
from bcc import plugin_security
from bcc.features.web_research import config


@pytest.mark.parametrize("address", [
    "http://[::1", "http://localhost:nope", "http://localhost:65536",
    "http://localhost:0", "http://localhost:8888/search",
    "http://localhost:8888/searx", "http://bad host:8888",
    "http://localhost:8888/?secret=do-not-echo",
    "http://owner:do-not-echo@localhost:8888", "http://localhost\\evil:8888",
])
def test_invalid_service_address_is_diagnosed_not_rewritten(monkeypatch, address):
    monkeypatch.setattr(config, "_env_errors", [])
    monkeypatch.setenv("BOSSMAN_WEB_SEARXNG_URL", address)
    assert config._env_searxng("BOSSMAN_WEB_SEARXNG_URL") == ""
    assert config.env_errors()
    assert "do-not-echo" not in str(config.env_errors())


@pytest.mark.parametrize("address, expected", [
    ("http://127.0.0.1:8888/", "http://127.0.0.1:8888"),
    ("http://[::1]:8888", "http://[::1]:8888"),
    ("https://search.example.com", "https://search.example.com"),
])
def test_valid_service_address_preserves_origin(monkeypatch, address, expected):
    monkeypatch.setattr(config, "_env_errors", [])
    monkeypatch.setenv("BOSSMAN_WEB_SEARXNG_URL", address)
    assert config._env_searxng("BOSSMAN_WEB_SEARXNG_URL") == expected
    assert not config.env_errors()


def test_configuration_never_claims_a_live_service(monkeypatch):
    monkeypatch.setattr(config, "SEARXNG_URL", "http://127.0.0.1:8888")
    setup = config.as_dict()["searxng_setup"]
    assert setup["configured"] is True
    assert setup["live_status"] == "not_verified_live"
    assert setup["required_formats"] == ["html", "json"]
    assert setup["restart_required_after_url_change"] is True


def test_private_passport_remains_denied_by_default_and_not_serialized():
    from dataclasses import asdict

    fields = dict(value={}, subject="query", source_id="searxng-local",
                  source_url="http://127.0.0.1:8888/search?q=test", method="api",
                  license="owner instance", observed_at=osiris.utcnow(), raw_ref="raw:123")
    with pytest.raises(plugin_security.PluginSecurityError):
        osiris.Observation(**fields)
    with pytest.raises(osiris.PassportError, match="origin mismatch"):
        osiris.Observation(**fields, private_source_origin="http://127.0.0.1:9999")
    row = osiris.Observation(**fields, private_source_origin="http://127.0.0.1:8888")
    assert "private_source_origin" not in row.as_dict()
    assert "private_source_origin" not in asdict(row)


def test_private_parser_requires_configured_exact_source(monkeypatch):
    from dataclasses import replace
    from bcc.features.web_research import sources

    monkeypatch.setattr(config, "SEARXNG_URL", "http://127.0.0.1:8888")
    backend = sources.BACKENDS_BY_ID["searxng-local"]
    source = sources._private_door_source(backend)
    kwargs = dict(url="http://127.0.0.1:8888/search?q=test", raw_ref="raw:123",
                  collected_at=osiris.utcnow(), fetched_at=osiris.utcnow(), shape="searxng")
    # Merely adding capability-looking fields to untrusted payload grants nothing.
    payload = {"results": [], "private_source_origin": config.SEARXNG_URL}
    with pytest.raises(plugin_security.PluginSecurityError):
        sources.serp_observations(source, "test", payload, **kwargs)
    with pytest.raises(osiris.PassportError, match="source mismatch"):
        sources.serp_observations(replace(source, id="other-source"), "test", payload,
                                 private_source_origin=config.SEARXNG_URL, **kwargs)
    with pytest.raises(osiris.PassportError, match="source mismatch"):
        sources.serp_observations(source, "test", payload,
                                 private_source_origin="http://127.0.0.1:9999", **kwargs)
