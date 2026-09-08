"""Higgsfield browser adapter built on Social Farm's existing DOM safety layer.

No anti-bot bypass lives here.  The adapter uses the owner's persistent browser
profile, semantic selector packs, challenge detection, bounded polling and the
normal download sandbox.  Any CAPTCHA/security checkpoint is terminal and sent
back to the owner.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from ..browser.challenge import detect_challenge
from ..browser.dom import DomPort
from ..browser.selectors import SelectorAction, SelectorPack
from .higgsfield_browser_contracts import (
    BrowserGenerationObservation,
    BrowserGenerationRequest,
    BrowserGenerationState,
    SubmissionReceipt,
)


@dataclass(frozen=True, slots=True)
class HiggsfieldAdapterConfig:
    generation_url: str
    downloads_dir: Path
    ready_text: tuple[str, ...] = ("create", "generate", "image", "video")
    auth_text: tuple[str, ...] = ("sign in", "log in", "continue with google")
    rate_limit_text: tuple[str, ...] = ("too many requests", "rate limit", "try again later")
    result_text: tuple[str, ...] = ("download", "export", "save")


class HiggsfieldBrowserAdapter:
    provider_name = "higgsfield-browser"

    def __init__(self, *, dom: DomPort, selectors: SelectorPack, config: HiggsfieldAdapterConfig) -> None:
        self.dom = dom
        self.selectors = selectors
        self.config = config
        self._submitted_at: dict[str, float] = {}

    async def _challenge_or_text(self) -> tuple[object, str]:
        markup = await self.dom.markup()
        text = await self.dom.visible_text(12000)
        url = await self.dom.current_url()
        return detect_challenge(markup=markup, text=text, url=url), text.lower()

    async def _find_action(self, action_name: str) -> dict[str, object] | None:
        action = self.selectors.get(action_name)
        if action is None:
            return None
        for strategy in action.strategies:
            matches = await self.dom.find(strategy.kind, strategy.value)
            visible = [item for item in matches if not item.get("disabled")]
            if visible:
                return visible[0]
        return None

    async def _require_action(self, action_name: str) -> dict[str, object]:
        found = await self._find_action(action_name)
        if found is None:
            raise RuntimeError(f"UI_CHANGED:{action_name}")
        return found

    async def prepare(self, request: BrowserGenerationRequest) -> BrowserGenerationObservation:
        current = await self.dom.current_url()
        if not current.startswith(self.config.generation_url):
            await self.dom.navigate(self.config.generation_url)

        challenge, text = await self._challenge_or_text()
        if challenge.present:
            return BrowserGenerationObservation(
                request.job_id,
                BrowserGenerationState.HUMAN_CHALLENGE,
                time.time(),
                safe_message=challenge.describe(),
                evidence={"challenge": challenge.kind.value, "provider": challenge.provider},
            )
        if any(marker in text for marker in self.config.auth_text):
            return BrowserGenerationObservation(
                request.job_id,
                BrowserGenerationState.NEEDS_OWNER_AUTH,
                time.time(),
                safe_message="Higgsfield browser session requires owner authentication",
                evidence={"url": await self.dom.current_url()},
            )

        prompt = await self._find_action("prompt_input")
        generate = await self._find_action("generate_submit")
        if prompt is None or generate is None:
            return BrowserGenerationObservation(
                request.job_id,
                BrowserGenerationState.UI_CHANGED,
                time.time(),
                safe_message="Higgsfield generation controls did not match the selector pack",
                evidence={"selector_pack": self.selectors.version},
            )
        return BrowserGenerationObservation(
            request.job_id,
            BrowserGenerationState.READY,
            time.time(),
            evidence={"selector_pack": self.selectors.version},
        )

    async def submit(self, request: BrowserGenerationRequest) -> SubmissionReceipt:
        prompt = await self._require_action("prompt_input")
        await self.dom.fill(str(prompt["ref"]), request.prompt)

        if request.media_kind.value == "video" and request.duration_seconds is not None:
            duration = await self._find_action("duration_input")
            if duration is not None:
                await self.dom.fill(str(duration["ref"]), str(request.duration_seconds))

        aspect = await self._find_action("aspect_ratio_input")
        if aspect is not None:
            await self.dom.fill(str(aspect["ref"]), request.aspect_ratio)

        submit = await self._require_action("generate_submit")
        await self.dom.click(str(submit["ref"]))
        self._submitted_at[request.job_id] = time.time()
        return SubmissionReceipt(
            job_id=request.job_id,
            provider=self.provider_name,
            submitted_at_epoch_s=self._submitted_at[request.job_id],
            evidence={
                "selector_pack": self.selectors.version,
                "url": await self.dom.current_url(),
                "submit_control": str(submit.get("accessible_name") or submit.get("label") or "generate"),
            },
        )

    async def poll(self, request: BrowserGenerationRequest, receipt: SubmissionReceipt) -> BrowserGenerationObservation:
        challenge, text = await self._challenge_or_text()
        if challenge.present:
            return BrowserGenerationObservation(
                request.job_id,
                BrowserGenerationState.HUMAN_CHALLENGE,
                time.time(),
                safe_message=challenge.describe(),
                evidence={"challenge": challenge.kind.value},
            )
        if any(marker in text for marker in self.config.rate_limit_text):
            return BrowserGenerationObservation(
                request.job_id,
                BrowserGenerationState.RATE_LIMITED,
                time.time(),
                safe_message="Higgsfield reported a rate limit; no bypass attempted",
            )

        download = await self._find_action("result_download")
        if download is not None:
            return BrowserGenerationObservation(
                request.job_id,
                BrowserGenerationState.OUTPUT_READY,
                time.time(),
                evidence={"result_control": str(download.get("ref", ""))},
            )

        if any(marker in text for marker in ("failed", "generation failed", "something went wrong")):
            return BrowserGenerationObservation(
                request.job_id,
                BrowserGenerationState.FAILED,
                time.time(),
                safe_message="Higgsfield generation failed",
            )
        return BrowserGenerationObservation(
            request.job_id,
            BrowserGenerationState.WAITING_PROVIDER,
            time.time(),
        )

    async def collect(self, request: BrowserGenerationRequest, receipt: SubmissionReceipt) -> Path:
        before = {path.resolve() for path in self.config.downloads_dir.glob("*") if path.is_file()}
        download = await self._require_action("result_download")
        await self.dom.click(str(download["ref"]))

        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            candidates = [
                path for path in self.config.downloads_dir.glob("*")
                if path.is_file() and path.resolve() not in before and not path.name.endswith((".crdownload", ".part"))
            ]
            if candidates:
                candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
                return candidates[0]
            import asyncio
            await asyncio.sleep(0.25)
        raise RuntimeError("Higgsfield download did not appear in the browser sandbox")


__all__ = ["HiggsfieldAdapterConfig", "HiggsfieldBrowserAdapter"]
