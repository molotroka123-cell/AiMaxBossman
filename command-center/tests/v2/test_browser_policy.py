from bcc.v2.browser_control import BrowserPolicy

def test_browser_safe_defaults():
    p = BrowserPolicy.from_dict({})
    assert p.decision("navigate", url="https://example.com") == "auto"
    assert p.decision("login", url="https://example.com") == "ask"
    assert p.decision("payment", url="https://example.com") == "deny"

def test_browser_domain_block():
    p = BrowserPolicy.from_dict({
        "allowed_domains": ["*.example.com"],
        "blocked_domains": ["bank.example.com"],
    })
    assert p.domain_allowed("https://app.example.com")
    assert not p.domain_allowed("https://bank.example.com")
