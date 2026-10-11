"""Prompt, output parser and the model ladder for Vibe Studio.

What may leave the machine (cloud rungs): the system prompt below (a constant),
and a spec made of the app title plus the owner's request texts for this app
(original + later modifications).  Never repo source, chats, photos, logs,
secrets, the previous generated code (only LOCAL rungs see it), or anything
else.  ``build_messages`` is the single place a payload is assembled and
``assert_cloud_payload`` re-checks it right before a cloud call.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .safety import (ALLOWED_EXT, MAX_FILE_BYTES, MAX_FILES, MAX_REQUEST_CHARS, UnsafePath,
                     safe_rel_path, secret_findings)

TIER_ORDER = {"local": 0, "free": 1, "paid": 2}
PAID_ORDER = ("glm", "haiku", "mistral")      # owner order: GLM 5.3 Flash, Haiku 5.5, Mistral Large 4

SYSTEM_PROMPT = """You are Vibe Studio's app builder. You build a SMALL, SELF-CONTAINED static web app from a request.

Output format (exactly, no prose outside it):
<<<PLAN>>>
3-6 short bullet lines: what the app does and the files you will write.
<<<FILE index.html>>>
...complete file content...
<<<FILE app.js>>>
...
<<<END>>>

Hard rules (a static scanner rejects the build if any is broken):
- One folder, at most 6 files, file names like index.html, app.js, style.css (no sub-folders needed).
- Vanilla HTML/CSS/JS only. NO external resources: no CDN, no web fonts, no http(s):// URLs, no network calls of any kind
  (no fetch, XMLHttpRequest, WebSocket, EventSource, sendBeacon, import(), iframes, forms with an action).
- No eval, no new Function, no string arguments to setTimeout/setInterval, no document.write.
- Never store secrets; localStorage keys must be plain names like "items" or "settings".
- Load your own files with <link rel="stylesheet" href="style.css"> and <script src="app.js"></script> only.
- The page must work at about 430x320 and larger, show an empty state, and print real error text instead of hiding errors.
- No alert()/confirm()/prompt(). Use plain DOM. Keep it under 40 KB per file.
"""


@dataclass
class Candidate:
    tier: str                    # local | free | paid
    label: str                   # e.g. "qwen3-coder (local)"
    adapter: Any                 # object with async chat(model, messages, **kw) -> ChatResult
    model: str                   # provider-side model name
    family: str = ""             # paid family key (glm|haiku|mistral)

    @property
    def is_local(self) -> bool:
        return self.tier == "local"


@dataclass
class ParsedBuild:
    plan: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)


class ParseError(ValueError):
    pass


def build_spec(title: str, requests: list[str]) -> dict:
    return {"title": title[:80], "requests": [r[:MAX_REQUEST_CHARS] for r in requests]}


def build_messages(spec: dict, *, previous_files: dict[str, str] | None = None,
                   rejected: list[str] | None = None) -> list[dict]:
    """Messages for one generation. ``previous_files`` must only be passed to LOCAL rungs."""
    lines = [f"App title: {spec['title']}"]
    reqs = spec["requests"]
    if len(reqs) == 1:
        lines.append("Request:\n" + reqs[0])
    else:
        lines.append("Original request:\n" + reqs[0])
        for i, r in enumerate(reqs[1:], 1):
            lines.append(f"Change request {i} (apply on top of everything above):\n{r}")
    if previous_files:
        lines.append("The current version of the app (modify it, keep what already works):")
        for path in sorted(previous_files):
            lines.append(f"<<<FILE {path}>>>\n{previous_files[path]}")
        lines.append("<<<END>>>")
    if rejected:
        lines.append("Your previous attempt was REJECTED by the safety scan for these reasons, fix them:\n"
                     + "\n".join(f"- {r[:200]}" for r in rejected[:8]))
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(lines)}]


def assert_cloud_payload(messages: list[dict], spec: dict) -> None:
    """Right before a non-local call: the payload is exactly system prompt + spec text, nothing else."""
    if len(messages) != 2 or messages[0].get("content") != SYSTEM_PROMPT or messages[1].get("role") != "user":
        raise PermissionError("cloud payload shape violation")
    user = str(messages[1].get("content") or "")
    if "<<<FILE" in user or "<<<END" in user:
        raise PermissionError("cloud payload carries file contents")
    if secret_findings(user):
        raise PermissionError("cloud payload contains secret-like text")
    expected = build_messages(spec, rejected=None)[1]["content"]
    if not user.startswith(expected):
        raise PermissionError("cloud payload differs from the spec")


_FILE_HDR = re.compile(r"^<<<FILE[ \t]+([^\r\n>]+?)[ \t]*>>>[ \t]*\r?\n", re.M)
_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_+-]*\r?\n(.*?)\r?\n```\s*$", re.S)


def parse_output(text: str) -> ParsedBuild:
    """Parse the FILE-block format. Raises ParseError with a model-readable reason."""
    raw = (text or "").replace("\r\n", "\n")
    out = ParsedBuild()
    plan_m = re.search(r"<<<PLAN>>>\s*\n(.*?)(?=<<<FILE|\Z)", raw, re.S)
    if plan_m:
        out.plan = [ln.strip("-* \t") for ln in plan_m.group(1).splitlines() if ln.strip()][:8]
    heads = list(_FILE_HDR.finditer(raw))
    if not heads:
        raise ParseError("no <<<FILE name>>> blocks found in the answer")
    for i, m in enumerate(heads):
        name = m.group(1).strip().strip("`'\"")
        end = heads[i + 1].start() if i + 1 < len(heads) else len(raw)
        body = raw[m.end():end]
        body = re.sub(r"<<<END>>>\s*$", "", body.rstrip()).rstrip("\n")
        fence = _FENCE.match(body)
        if fence:
            body = fence.group(1)
        try:
            name = safe_rel_path(name)
        except UnsafePath as exc:
            raise ParseError(str(exc)) from None
        if name in out.files:
            raise ParseError(f"file {name} appears twice")
        out.files[name] = body + "\n"
    if len(out.files) > MAX_FILES:
        raise ParseError(f"too many files ({len(out.files)})")
    if "index.html" not in out.files:
        raise ParseError("index.html is missing")
    return out


# --------------------------------------------------------------------- ladder
class Ladder:
    """Provides ordered Candidate lists. Subclass or duck-type ``candidates``."""

    async def candidates(self, *, local_only: bool) -> list[Candidate]:  # pragma: no cover
        raise NotImplementedError


class RegistryLadder(Ladder):
    """local -> free cloud -> approved paid (GLM 5.3 Flash, Haiku 5.5, Mistral Large 4).

    Every adapter comes from ``Registry.adapter_for`` and is therefore already
    wrapped by the free-only gate and ``GovernedAdapter`` (price gate, privacy
    boundary, long-run caps and STOP).  Paid rungs exist only while the owner's
    long_run profile is enabled; otherwise those models are refused by the gate
    and are not even offered."""

    PER_TIER = 3

    def __init__(self, registry: Any):
        self.registry = registry

    async def candidates(self, *, local_only: bool) -> list[Candidate]:
        from .. import long_run
        from ..provider_governance import (banned_model_refusal, free_only_refusal,
                                           is_governed_local)
        models = await self.registry.list_models()
        buckets: dict[str, list[tuple[Any, ...]]] = {"local": [], "free": [], "paid": []}
        for m in models:
            if m.get("status") in ("offline", "error"):
                continue
            name = str(m.get("name") or "")
            lowered = f"{name} {m.get('alias') or ''}".lower()
            if any(t in lowered for t in ("embed", "whisper", "tts", "rerank", "diffusion", "flux", "sdxl")):
                continue
            try:
                adapter, row = await self.registry.adapter_for(int(m["id"]))
            except Exception:  # noqa: BLE001 - one broken registration must not hide the others
                continue
            provider = getattr(adapter, "provider", None) or {}
            model = getattr(adapter, "model", None) or row
            if banned_model_refusal(model):
                continue
            coding = 0 if (row.get("caps") or {}).get("coding") or "code" in lowered else 1
            if is_governed_local(provider, model):
                buckets["local"].append((coding, int(m["id"]), adapter, name, ""))
                continue
            if local_only:
                continue
            family = long_run.classify(name, provider.get("base_url") or "")
            if family and long_run.enabled():
                buckets["paid"].append((PAID_ORDER.index(family), int(m["id"]), adapter, name, family))
            elif not free_only_refusal(provider, model) and (name.endswith(":free")
                                                            or (model.get("caps") or {}).get("free_tier")):
                buckets["free"].append((coding, int(m["id"]), adapter, name, ""))
        out: list[Candidate] = []
        for tier in ("local", "free", "paid"):
            for _, _, adapter, name, family in sorted(buckets[tier], key=lambda t: (t[0], t[1]))[: self.PER_TIER]:
                out.append(Candidate(tier=tier, label=f"{name} ({tier})", adapter=adapter, model=name,
                                     family=family))
        return out


def dump_spec(spec: dict) -> str:
    return json.dumps(spec, ensure_ascii=False, sort_keys=True)
