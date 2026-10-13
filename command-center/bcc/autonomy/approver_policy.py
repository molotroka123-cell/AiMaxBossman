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
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import constitution as const
from .policy import LEVELS

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
    min_independent: int = MIN_VERIFIER_FAMILIES   # v2: verifier families that must approve (this build runs 2)
    require_external: bool = True                  # v2: at least one verifier is a cloud model
    writer_may_verify: bool = False                # never true: the writer is not independent of itself
    auto_tier_requires: tuple[str, ...] = ("tests", "verifiers")
    max_level: str = "L2"                          # the START level cap; the build also hard-caps at L2
    source: str = "legacy"                         # legacy | block | fallback
    rule: str = ""                                 # which rule matched (for `constitution explain`)
    warnings: tuple[str, ...] = ()

    @property
    def is_v2(self) -> bool:
        return self.mode == "v2"

    def as_dict(self) -> dict:
        return {"mode": self.mode, "reason": self.reason, "constitution_sha": self.constitution_sha,
                "required": (f"tests green + >={self.min_independent} verifier families"
                             f"{' (>=1 external)' if self.require_external else ''}, writer never a verifier"
                             if self.is_v2 else "claude + codex"),
                "min_independent": self.min_independent if self.is_v2 else 2,
                "require_external": self.require_external, "writer_may_verify": self.writer_may_verify,
                "auto_tier_requires": list(self.auto_tier_requires), "max_level": self.max_level,
                "source": self.source, "rule": self.rule, "warnings": list(self.warnings)}

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
        if len(counted) < self.min_independent:
            return Evaluation(False, f"need approvals from >= {self.min_independent} independent verifier families "
                                     f"(have {len(counted)})", tuple(counted), fams, tuple(rejected))
        if self.require_external and not any(c.external for c in counted.values()):
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


# ---- machine-readable policy block --------------------------------------------------------------------------

POLICY_FENCE = "bossman-policy"
SCHEMA_VERSION = 1
_FENCE_OPEN = re.compile(r"^```" + POLICY_FENCE + r"[ \t]*\r?$", re.M)
_FENCE_BLOCK = re.compile(r"^```" + POLICY_FENCE + r"[ \t]*\r?\n(.*?)\r?\n```[ \t]*\r?$", re.S | re.M)
_TOP_KEYS = frozenset({"schema_version", "approvers", "writer_may_verify", "auto_tier_requires", "max_autonomy_level"})
_APPROVER_KEYS = frozenset({"mode", "named", "min_independent", "require_external"})
_AUTO_REQUIRED = ("tests", "verifiers")
#: Words that mark an attempt to touch an invariant the block can never change. Matched against every unknown key.
_HARD_WORDS = ("owner", "decision", "stop", "protect", "glob", "pin", "journal", "release", "money", "budget",
               "secret", "key", "constitution", "lease", "cap", "promotion", "safety")
#: What the block can never change (printed by `explain` and in README_RU). Enforced by the code paths that own
#: them (owner pin, STOP checks, protected globs, release tiers): the parser has no field that reaches them and
#: rejects the whole block if it names one.
HARD_INVARIANTS = (
    "the owner decision list (money, publications, keys, deleting data, disabling safety, constitution changes)",
    "owner STOP (global and autonomy)",
    "protected path globs and release tiers (the owner presses Release)",
    "the owner pin outside the repository (SHA-256 of the constitution file)",
    "the L2 start cap and 25 clean cycles + the owner's confirmation for promotion",
    "the writer is never its own verifier, nor of its family",
)


def _legacy_policy(line: str, sha: str) -> ApproverPolicy:
    """Text detection of Stages 1/2.2b: v2 only when rule 4 declares the independent-verification sentence and no
    longer names both CLIs."""
    if V2_SENTENCE in line and "both claude and codex" not in line.lower():
        return ApproverPolicy("v2", "pinned constitution declares independent verification", sha,
                              source="legacy", rule="legacy: rule 4 carries the independent-verification sentence")
    if line:
        return ApproverPolicy("v1", "pinned constitution requires Claude and Codex", sha, source="legacy",
                              rule="legacy: rule 4 has no independent-verification sentence (or still names both "
                                   "Claude and Codex) -> v1")
    return ApproverPolicy("v1", "constitution rule 4 not found: v1", sha, source="legacy",
                          rule="legacy: rule 4 not found -> v1")


def _fallback(why: str, sha: str) -> ApproverPolicy:
    return ApproverPolicy("v1", f"policy block rejected -> v1 (fail closed): {why}", sha, source="fallback",
                          rule="policy block present but unusable -> v1 semantics", warnings=(why,))


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _block_policy(raw: dict, sha: str, line: str) -> ApproverPolicy:
    """Validate a parsed block. Anything unknown / weaker than the floor of this build -> fallback to v1."""
    unknown = sorted(str(k) for k in raw if k not in _TOP_KEYS)
    if unknown:
        hard = [k for k in unknown if any(w in k.lower() for w in _HARD_WORDS)]
        if hard:
            return _fallback(f"the block names {hard}: owner decisions, STOP, protected globs, the pin and release "
                             f"tiers cannot be changed by the block", sha)
        return _fallback(f"unknown key(s) {unknown}", sha)
    if not _is_int(raw.get("schema_version")) or raw.get("schema_version") != SCHEMA_VERSION:
        return _fallback(f"unsupported schema_version {raw.get('schema_version')!r} (this build reads "
                         f"{SCHEMA_VERSION})", sha)
    ap = raw.get("approvers")
    if not isinstance(ap, dict):
        return _fallback("'approvers' is missing or not an object", sha)
    bad = sorted(str(k) for k in ap if k not in _APPROVER_KEYS)
    if bad:
        return _fallback(f"unknown approvers key(s) {bad}", sha)
    mode = ap.get("mode")
    warnings: list[str] = []
    if mode not in ("named", "independent"):
        return _fallback(f"unknown approvers.mode {mode!r} (named | independent)", sha)
    if raw.get("writer_may_verify", False) is not False:
        return _fallback("writer_may_verify must be false: a writer cannot verify its own change", sha)
    req = raw.get("auto_tier_requires", list(_AUTO_REQUIRED))
    if not isinstance(req, list) or not all(isinstance(x, str) for x in req) or set(req) != set(_AUTO_REQUIRED):
        return _fallback(f"auto_tier_requires must be exactly {list(_AUTO_REQUIRED)} (tests and verifiers cannot be "
                         f"removed, nothing else is supported)", sha)
    lvl = raw.get("max_autonomy_level", "L2")
    if lvl not in LEVELS:
        return _fallback(f"max_autonomy_level {lvl!r} is not one of {list(LEVELS)}", sha)
    eff = lvl
    if LEVELS.index(lvl) > LEVELS.index("L2"):
        eff = "L2"
        warnings.append(f"max_autonomy_level {lvl} is above the L2 start cap: the system starts at L2; promotion "
                        f"still needs 25 clean cycles and the owner's confirmation")
    low = line.lower()
    if mode == "named":
        named = ap.get("named", ["claude", "codex"])
        if not isinstance(named, list) or sorted(str(x).lower() for x in named) != ["claude", "codex"]:
            return _fallback("approvers.named must be exactly ['claude', 'codex'] in this build", sha)
        if V2_SENTENCE in line:
            return _fallback("the block says named claude+codex but rule 4 carries the independent-verification "
                             "sentence: contradictory text", sha)
        if "min_independent" in ap or "require_external" in ap:
            warnings.append("min_independent / require_external are ignored in mode 'named'")
        return ApproverPolicy("v1", "policy block: named approvers claude + codex", sha, max_level=eff,
                              source="block", rule="policy block: approvers.mode = named",
                              warnings=tuple(warnings))
    mi = ap.get("min_independent", MIN_VERIFIER_FAMILIES)
    if not _is_int(mi) or mi < MIN_VERIFIER_FAMILIES:
        return _fallback(f"min_independent {mi!r} is below the floor {MIN_VERIFIER_FAMILIES}", sha)
    if mi > MIN_VERIFIER_FAMILIES:
        return _fallback(f"min_independent {mi} is above what this build runs ({MIN_VERIFIER_FAMILIES} verifier "
                         f"stages); a higher number would stall the loop, so it is refused", sha)
    if ap.get("require_external", True) is not True:
        return _fallback("require_external must be true: at least one verifier must be a cloud model", sha)
    if ap.get("named") not in (None, []):
        warnings.append("approvers.named is ignored in mode 'independent'")
    if "both claude and codex" in low:
        return _fallback("the block says independent but rule 4 still requires both Claude and Codex: "
                         "contradictory text", sha)
    return ApproverPolicy("v2", "policy block: independent verifiers", sha, min_independent=mi,
                          require_external=True, max_level=eff, source="block",
                          rule="policy block: approvers.mode = independent", warnings=tuple(warnings))


def policy_from_text(text: str, *, sha: str = "") -> ApproverPolicy:
    """The effective approver policy of a constitution TEXT (no pin check here; ``load_policy`` adds that).

    * a ```bossman-policy JSON block (schema_version 1) governs when present and valid;
    * a present but invalid / ambiguous / weakening block -> v1 (fail closed) with the reason in ``warnings``;
    * no block -> the legacy text detection (v2 sentence in rule 4 -> independent, otherwise Claude + Codex).
    """
    line = _rule4_line(text)
    opens = _FENCE_OPEN.findall(text)
    if not opens:
        return _legacy_policy(line, sha)
    if len(opens) > 1:
        return _fallback("more than one bossman-policy block (ambiguous)", sha)
    m = _FENCE_BLOCK.search(text)
    if not m:
        return _fallback("the bossman-policy block is not closed", sha)
    try:
        raw = json.loads(m.group(1))
    except ValueError as exc:
        return _fallback(f"invalid JSON: {str(exc)[:120]}", sha)
    if not isinstance(raw, dict):
        return _fallback("the block must be a JSON object", sha)
    return _block_policy(raw, sha, line)


def explain(path: str | None = None, pin_path: str | None = None) -> dict:
    """What the constitution file at ``path`` WOULD mean as the policy, without pinning anything. Reads only."""
    cpath = Path(path or const.default_constitution_path())
    try:
        data = cpath.read_bytes()
    except OSError as exc:
        return {"ok": False, "path": str(cpath), "error": f"unreadable: {type(exc).__name__}",
                "policy": V1.as_dict(), "hard_invariants": list(HARD_INVARIANTS)}
    sha = hashlib.sha256(data).hexdigest()
    pol = policy_from_text(data.decode("utf-8", "replace"), sha=sha)
    try:
        pinned = const.read_pin(pin_path)
    except (OSError, ValueError):
        pinned = None
    start = min(LEVELS.index(pol.max_level), LEVELS.index("L2"))
    return {"ok": True, "path": str(cpath), "sha": sha, "pinned_sha": pinned or "",
            "pinned": pinned == sha, "effective": ("this file is the pinned constitution" if pinned == sha
                                                   else "NOT pinned: this is a preview; the loop runs the pinned file"),
            "policy": pol.as_dict(), "start_level": LEVELS[start], "hard_invariants": list(HARD_INVARIANTS)}


def format_explain(rep: dict) -> str:
    pol = rep["policy"]
    lines = [f"Constitution: {rep.get('path', '')}"]
    if not rep.get("ok"):
        return "\n".join([*lines, f"ERROR: {rep.get('error')}", "Effective policy if used: v1 (Claude + Codex)"])
    lines += [f"SHA-256:      {rep['sha']}", f"Pinned:       {'yes' if rep['pinned'] else 'no'} ({rep['effective']})",
              "", "EFFECTIVE POLICY",
              f"  approver mode:      {pol['mode']}  ({pol['required']})",
              f"  matched rule:       {pol['rule']}  [source: {pol['source']}]",
              f"  min verifiers:      {pol['min_independent']}",
              f"  external required:  {'yes' if pol['require_external'] else 'no'}",
              f"  writer may verify:  {'yes' if pol['writer_may_verify'] else 'no'}",
              f"  auto tier needs:    {', '.join(pol['auto_tier_requires'])}",
              f"  start level:        {rep['start_level']} (block level {pol['max_level']}, hard start cap L2)"]
    if pol["warnings"]:
        lines.append("WARNINGS")
        lines += [f"  - {w}" for w in pol["warnings"]]
    lines.append("NEVER changed by any constitution text (enforced in code):")
    lines += [f"  - {x}" for x in rep["hard_invariants"]]
    return "\n".join(lines)


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
           "STATE_ALIASES", "V1", "HARD_INVARIANTS", "POLICY_FENCE", "SCHEMA_VERSION", "explain",
           "format_explain", "DEFAULT_VERIFIERS", "default_verifiers", "V1_PAIR", "V2_SENTENCE", "VerifierInfo", "family_of", "load_policy",
           "policy_from_text", "verifier_info"]
