"""B11 #7 — web_access не пропускает IPv4 в «сокращённой» записи.

Дефект: `check_public_url` распознавал IP только через `ipaddress.ip_address`,
который понимает лишь каноничную форму. `http://127.1/`, `http://0177.0.0.1/`,
`http://0x7f.0.0.1/`, `http://10.1/`, `http://192.168.1/` проходили как «домен»:
в нём есть точка и только [a-z0-9.-]. Браузеры и inet_aton читают такие хосты
как 127.0.0.1 / 10.0.0.1 / 192.168.0.1 (WHATWG URL: хост, оканчивающийся
числом, разбирается как IPv4). Правило «без localhost/LAN» обходилось.
"""
from __future__ import annotations

import pytest

from bcc.web_access.policy import PolicyRefusal, check_public_url


@pytest.mark.parametrize("url", [
    "http://127.1/", "http://0177.0.0.1/", "http://0x7f.0.0.1/", "http://10.1/",
    "http://192.168.1/", "https://1.2.3/", "http://example.0x10/", "http://2130706433./",
])
def test_numeric_shorthand_hosts_are_refused(url):
    with pytest.raises(PolicyRefusal):
        check_public_url(url)


@pytest.mark.parametrize("url", [
    "https://example.com/", "https://1password.com/", "https://web3.example.org/x",
    "https://123.example.com/a",
])
def test_ordinary_domains_still_allowed(url):
    assert check_public_url(url) == url
