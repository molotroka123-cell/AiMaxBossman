"""Required-approver policy, derived from the PINNED and verified constitution (rule 4).

Two policies exist and the constitution text selects one:

* ``v1`` - the text of rule 4 requires "both Claude and Codex": exactly Claude AND Codex must approve the same
  immutable SHA (the behaviour before Stage 2.2b, unchanged).
* ``v2`` - the pinned text of rule 4 declares :data:`V2_SENTENCE`: all tests green AND approvals from at least
  two verifier models of DIFFERENT families, at least one of them external (a cloud model, not local-only), none
  of them the writer or of the writer's family.

The policy is v2 only when the constitution file is readable, its SHA-256 equals the owner's pin and the sentence is
inside rule 4. A missing / unreadable / mismatching pin, v1 text or any doubt -> v1 (and the loop is BLOCKED anyway
by ``constitution.verify`` without a pin). Nothing here writes the pin or the constitution; this module only reads.

Verifier ids: ``claude`` / ``codex`` (the subscription CLIs) or ``<provider>:<model>`` (for example
``openrouter:google/gemma-3-27b-it:free``, ``nvidia:nvidia/nemotron-3-ultra``, ``local:qwen3-coder``). The family
comes from the model name; an unknown provider or an unknown family never counts (fail closed).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import constitution as const

#: Exact sentence the pinned constitution must carry in rule 4 for the v2 gate.
V2_SENTENCE = "independent verification of the same immutable SHA"
V1_PAIR = ("claude", "codex")
MIN_VERIFIER_FAMILIES = 2

#: Aliases of the two review states (the state names themselves stay CLAUDE_REVIEW / CODEX_REVIEW for the
#: stored goals, the journal and the contract; under v2 they are simply "verifier 1" and "verifier 2").
STATE_ALIASES = {"VERIFY_1": "CLAUDE_REVIEW", "VERIFY_2": "CODEX_REVIEW"}

EXTERNAL_PROVIDERS = frozenset({"cli", "openrouter", "nvidia", "nim", "mistral", "google", "gemini", "groq",
                                "cerebras", "together", "deepseek", "zhipu", "github", "huggingface", "cloud"})
LOCAL_PROVIDERS = frozenset({"local", "ollama", "lmstudio", "llamacpp", "vllm", "llama.cpp"})

#: (keyword in the lower-cased model name, family). First match wins; order puts the more specific names first.
_FAMILY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("nemotron", "nvidia"), ("gpt-oss", "openai"), ("claude", "anthropic"), ("codex", "openai"),
    ("gpt", "openai"), ("openai", "openai"), ("qwen", "qwen"), ("qwq", "qwen"), ("gemma", "google"),
    ("gemini", "google"), ("llama", "meta"), ("mixtral", "mistral"), ("mistral", "mistral"),
    ("devstral", "mistral"), ("codestral", "mistral"), ("magistral", "mistral"), ("ministral", "mistral"),
    ("glm", "zhipu"), ("deepseek", "deepseek"), ("kimi", "moonshot"), ("moonshot", "moonshot"),
    ("minimax", "minimax"), ("phi-", "microsoft"), ("grok", "xai"), ("command-r", "cohere"),
    ("cohere", "cohere"), ("olmo", "ai2"), ("hunyuan", "tencent"), ("ernie", "baidu"), ("liquid", "liquid"),
    ("lfm", "liquid"), ("nvidia", "nvidia"),
)
_ID = re.compile(r"^[A-Za-z0-9._-]+:[A-Za-z0-9._/:@+-]+$")


@dataclass(frozen=True)
class VerifierInfo:
    id: str
    valid: bool
    provider: str = ""
    model: str = ""
    family: str = ""
    external: bool = False
    reason: str = ""


def family_of(name: str) -> str:
    """Model family of a model / agent name ('' when unknown). A provider prefix is ignored."""
    n = str(name or "").strip().lower()
    if not n:
        return ""
    if n in V1_PAIR:
        return {"claude": "anthropic", "codex": "openai"}[n]
    if ":" in n:
        head, _, rest = n.partition(":")
        if head in EXTERNAL_PROVIDERS or head in LOCAL_PROVIDERS:
            n = rest
    for key, fam in _FAMILY_KEYWORDS:
        if key in n:
            return fam
    return ""


def verifier_info(verifier_id: str) -> VerifierInfo:
    vid = str(verifier_id or "").strip()
    low = vid.lower()
    if low in V1_PAIR:
        return VerifierInfo(vid, True, "cli", low, family_of(low), True)
    if not _ID.match(vid):
        return VerifierInfo(vid, False, reason="verifier id must be claude, codex or <provider>:<model>")
    provider, _, model = low.partition(":")
    if provider in EXTERNAL_PROVIDERS:
        external = True
    elif provider in LOCAL_PROVIDERS:
        external = False
    else:
        return VerifierInfo(vid, False, provider, model, reason=f"unknown provider {provider!r}")
    fam = family_of(model)
    if not fam:
        return VerifierInfo(vid, False, provider, model, "", external, f"unknown model family for {model!r}")
    return VerifierInfo(vid, True, provider, model, fam, external)


@dataclass(frozen=True)
class Evaluation:
    ok: bool
    reason: str
    counted: tuple[str, ...] = ()
    families: tuple[str, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ApproverPolicy:
    mode: str = "v1"                 # v1 | v2
    reason: str = "v1: Claude and Codex"
    constitution_sha: str = ""

    @property
    def is_v2(self) -> bool:
        return self.mode == "v2"

    def as_dict(self) -> dict:
        return {"mode": self.mode, "reason": self.reason, "constitution_sha": self.constitution_sha,
                "required": ("tests green + >=2 verifier families (>=1 external), writer never a verifier"
                             if self.is_v2 else "claude + codex")}

    def valid_reviewer(self, reviewer: str) -> bool:
        """May this id record a review under the policy? v1: only claude / codex."""
        if not self.is_v2:
            return reviewer in V1_PAIR
        return verifier_info(reviewer).valid

    def evaluate(self, approved: Iterable[str], author: str) -> Evaluation:
        """Are these (already SHA+diff-bound, APPROVE) reviewers enough? ``author`` is the candidate's writer."""
        ids = sorted({str(a) for a in approved})
        if not self.is_v2:
            ok = set(V1_PAIR) <= set(ids)
            return Evaluation(ok, "" if ok else "both Claude and Codex approvals for the current SHA+diff are required",
                              tuple(i for i in ids if i in V1_PAIR))
        writer = str(author or "").strip()
        wfam = family_of(writer)
        if not writer or not wfam:
            return Evaluation(False, "candidate author unknown: independence from the writer cannot be shown")
        wlow = writer.lower()
        counted: dict[str, VerifierInfo] = {}
        rejected: list[tuple[str, str]] = []
        for vid in ids:
            info = verifier_info(vid)
            if not info.valid:
                rejected.append((vid, info.reason))
            elif vid.lower() == wlow or info.model == wlow:
                rejected.append((vid, "the writer cannot verify its own change"))
            elif info.family == wfam:
                rejected.append((vid, f"same family as the writer ({wfam})"))
            elif info.family in {c.family for c in counted.values()}:
                rejected.append((vid, f"family {info.family} already counted"))
            else:
                counted[vid] = info
        fams = tuple(sorted(c.family for c in counted.values()))
        if len(counted) < MIN_VERIFIER_FAMILIES:
            return Evaluation(False, f"need approvals from >= {MIN_VERIFIER_FAMILIES} independent verifier families "
                                     f"(have {len(counted)})", tuple(counted), fams, tuple(rejected))
        if not any(c.external for c in counted.values()):
            return Evaluation(False, "at least one verifier must be external (not local-only)", tuple(counted),
                              fams, tuple(rejected))
        return Evaluation(True, "", tuple(counted), fams, tuple(rejected))


V1 = ApproverPolicy()

#: Default ordered verifier list for the v2 loop (free OpenRouter ids of different families). Not verified live
#: here: override with BOSSMAN_AUTONOMY_VERIFIERS="id1,id2,..." (owner machine). Free-only is enforced by the
#: transport (only ':free' ids go out).
DEFAULT_VERIFIERS = ("openrouter:nvidia/nemotron-3-ultra-550b-a55b:free", "openrouter:google/gemma-3-27b-it:free",
                     "openrouter:z-ai/glm-4.5-air:free")


def default_verifiers(env: dict | None = None) -> tuple[str, ...]:
    import os
    raw = str((env if env is not None else os.environ).get("BOSSMAN_AUTONOMY_VERIFIERS", "")).strip()
    ids = tuple(x.strip() for x in raw.split(",") if x.strip())
    return ids or DEFAULT_VERIFIERS


def _rule4_line(text: str) -> str:
    for line in text.splitlines():
        if re.match(r"^\s*4\.\s", line):
            return line
    return ""


def policy_from_text(text: str, *, sha: str = "") -> ApproverPolicy:
    """v2 only when rule 4 declares the independent-verification sentence and no longer names both CLIs."""
    line = _rule4_line(text)
    if V2_SENTENCE in line and "both claude and codex" not in line.lower():
        return ApproverPolicy("v2", "pinned constitution declares independent verification", sha)
    return ApproverPolicy("v1", "pinned constitution requires Claude and Codex" if line
                          else "constitution rule 4 not found: v1", sha)


def load_policy(path: str | None = None, pin_path: str | None = None) -> ApproverPolicy:
    """The policy of the PINNED constitution. Unreadable / unpinned / mismatching -> v1 (never v2 by accident)."""
    try:
        cpath = Path(path or const.default_constitution_path())
        data = cpath.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        pinned = const.read_pin(pin_path)
    except (OSError, ValueError):
        return ApproverPolicy("v1", "constitution or pin unreadable: v1")
    if pinned is None:
        return ApproverPolicy("v1", "constitution not pinned: v1", sha)
    if pinned != sha:
        return ApproverPolicy("v1", "constitution differs from the pin: v1", sha)
    return policy_from_text(data.decode("utf-8", "replace"), sha=sha)


__all__ = ["ApproverPolicy", "EXTERNAL_PROVIDERS", "Evaluation", "LOCAL_PROVIDERS", "MIN_VERIFIER_FAMILIES",
           "STATE_ALIASES", "V1", "DEFAULT_VERIFIERS", "default_verifiers", "V1_PAIR", "V2_SENTENCE", "VerifierInfo", "family_of", "load_policy",
           "policy_from_text", "verifier_info"]
