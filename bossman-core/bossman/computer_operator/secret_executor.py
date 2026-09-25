"""Deterministic browser secret actuator for pre-bound login forms.

The model is used only before intake to identify safe field bindings. Secret
bytes are never sent to a model. This executor performs exact semantic fills
and verifies a fresh post-login condition.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApplyResult:
    success: bool
    verified: bool
    code: str = ""
    detail: str = ""


@dataclass(frozen=True)
class BoundField:
    secret_name: str
    role: str
    accessible_name: str

    def __post_init__(self):
        if self.role not in {"textbox"}:
            raise ValueError("only exact textbox bindings are supported")
        if not self.secret_name or not self.accessible_name:
            raise ValueError("secret field binding is incomplete")


@dataclass(frozen=True)
class BrowserSecretBinding:
    page_url_prefix: str
    fields: tuple[BoundField, ...]
    submit_role: str
    submit_name: str
    success_url_prefix: str = ""
    success_text: str = ""

    def __post_init__(self):
        if not self.page_url_prefix.startswith(("http://", "https://")):
            raise ValueError("page_url_prefix must be http(s)")
        if self.submit_role not in {"button"} or not self.submit_name:
            raise ValueError("submit binding is invalid")
        if not self.success_url_prefix and not self.success_text:
            raise ValueError("at least one independent success condition is required")


class PlaywrightSecretExecutor:
    """Exact semantic form fill on an already-open local Playwright page."""

    local_only = True
    network_isolated = True  # no arbitrary/provider network API; only the bound browser page is actuated
    model_sees_secret = False

    def __init__(self, page: Any, binding: BrowserSecretBinding):
        self.page = page
        self.binding = binding

    def _exact(self, role: str, name: str):
        loc = self.page.get_by_role(role, name=name, exact=True)
        count = loc.count()
        if count != 1:
            raise RuntimeError(f"bound target {role}:{name!r} has {count} matches")
        return loc.first

    async def apply(self, request, values: dict[str, bytearray]) -> SecretExecutionResult:
        current = str(self.page.url or "")
        if not current.startswith(self.binding.page_url_prefix):
            return ApplyResult(False, False, "LOGIN_PAGE_IDENTITY_CHANGED")

        expected = {f.secret_name for f in self.binding.fields}
        if set(values) != expected:
            return ApplyResult(False, False, "LOGIN_SECRET_BINDING_MISMATCH")

        # Decode only at the final actuator boundary. Python strings cannot be
        # physically zeroized, so references are kept as short-lived as possible.
        try:
            for field in self.binding.fields:
                text = values[field.secret_name].decode("utf-8")
                self._exact(field.role, field.accessible_name).fill(text, timeout=10_000)
                del text
            self._exact(self.binding.submit_role, self.binding.submit_name).click(timeout=10_000)
            self.page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except Exception:
            return ApplyResult(False, False, "LOGIN_ACTUATOR_ERROR")

        if self.binding.success_url_prefix and str(self.page.url or "").startswith(self.binding.success_url_prefix):
            return ApplyResult(True, True, "LOGIN_VERIFIED_BY_URL")
        if self.binding.success_text:
            try:
                loc = self.page.get_by_text(self.binding.success_text, exact=True)
                if loc.count() == 1 and loc.first.is_visible():
                    return ApplyResult(True, True, "LOGIN_VERIFIED_BY_POSTSTATE")
            except Exception:
                pass
        return ApplyResult(False, False, "LOGIN_POSTSTATE_NOT_VERIFIED")
