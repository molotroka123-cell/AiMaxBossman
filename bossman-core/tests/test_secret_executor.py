from __future__ import annotations

import asyncio

from bossman.computer_operator.secret_executor import (
    BoundField, BrowserSecretBinding, PlaywrightSecretExecutor,
)


class Locator:
    def __init__(self, page, key):
        self.page, self.key = page, key
    def count(self):
        return 1
    @property
    def first(self):
        return self
    def fill(self, text, timeout=0):
        self.page.filled[self.key] = text
    def click(self, timeout=0):
        self.page.clicked = self.key
        self.page.url = "https://example.test/account"
    def is_visible(self):
        return True


class Page:
    def __init__(self):
        self.url = "https://example.test/login"
        self.filled = {}
        self.clicked = None
    def get_by_role(self, role, name=None, exact=False):
        return Locator(self, (role, name, exact))
    def get_by_text(self, text, exact=False):
        return Locator(self, ("text", text, exact))
    def wait_for_load_state(self, state, timeout=0):
        assert state == "domcontentloaded"


def test_exact_bound_browser_secret_executor_verifies_poststate():
    page = Page()
    binding = BrowserSecretBinding(
        page_url_prefix="https://example.test/login",
        fields=(BoundField("username", "textbox", "Email"),
                BoundField("password", "textbox", "Password")),
        submit_role="button", submit_name="Sign in",
        success_url_prefix="https://example.test/account",
    )
    executor = PlaywrightSecretExecutor(page, binding)
    result = asyncio.run(executor.apply(
        object(), {"username": bytearray(b"alice"), "password": bytearray(b"secret")}
    ))
    assert result.success and result.verified
    assert page.filled[("textbox", "Email", True)] == "alice"
    assert page.filled[("textbox", "Password", True)] == "secret"
    assert page.clicked == ("button", "Sign in", True)


def test_page_identity_change_refuses_secret_injection():
    page = Page()
    page.url = "https://evil.test/login"
    binding = BrowserSecretBinding(
        page_url_prefix="https://example.test/login",
        fields=(BoundField("password", "textbox", "Password"),),
        submit_role="button", submit_name="Sign in",
        success_text="Account",
    )
    result = asyncio.run(PlaywrightSecretExecutor(page, binding).apply(
        object(), {"password": bytearray(b"secret")}
    ))
    assert not result.success and result.code == "LOGIN_PAGE_IDENTITY_CHANGED"
    assert not page.filled
