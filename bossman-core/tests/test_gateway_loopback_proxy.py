"""P0-A (аудит 10×10): loopback-проход шлюза не даётся запросам, пришедшим через
reverse-proxy / Tailscale Serve (они выглядят как 127.0.0.1), и «*» по алиасам
не появляется молча — оператор задаёт allowlist явно."""
import pytest
from fastapi import HTTPException

from bossman.gateway.auth import AuthManager
from bossman.gateway.config import ClientConfig, GatewayConfig


class _Req:
    def __init__(self, host, **headers):
        self.headers = {k.lower().replace("_", "-"): v for k, v in headers.items()}
        self.client = type("C", (), {"host": host})()


def _am(**kw):
    return AuthManager(GatewayConfig(clients={"core": ClientConfig("core", key="k" * 24)},
                                     allow_unauthenticated_loopback=True, **kw))


def test_direct_loopback_is_accepted_when_operator_enabled_it():
    assert _am().authenticate(_Req("127.0.0.1")).name == "loopback"


@pytest.mark.parametrize("header", ["x_forwarded_for", "x_real_ip", "forwarded", "x_forwarded_host", "via",
                                    "cf_connecting_ip"])
def test_forwarded_request_from_loopback_is_not_local(header):
    with pytest.raises(HTTPException) as exc:
        _am().authenticate(_Req("127.0.0.1", **{header: "203.0.113.9"}))
    assert exc.value.status_code == 401


def test_external_host_still_requires_bearer():
    with pytest.raises(HTTPException):
        _am().authenticate(_Req("203.0.113.9"))


def test_loopback_aliases_are_explicit_allowlist_when_configured():
    client = _am(loopback_allowed_aliases={"local-7b"}).authenticate(_Req("::1"))
    assert client.config.allowed_aliases == {"local-7b"}
    assert "*" not in client.config.allowed_aliases


def test_flag_off_by_default_even_for_direct_loopback():
    am = AuthManager(GatewayConfig(clients={"core": ClientConfig("core", key="k" * 24)}))
    with pytest.raises(HTTPException):
        am.authenticate(_Req("127.0.0.1"))
