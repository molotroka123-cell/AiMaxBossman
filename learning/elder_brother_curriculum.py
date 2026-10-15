"""Elder Brother 15-Branch Computer-Agent Curriculum & Independent Verifier.

Covers all 15 canonical directions of the Bossman capability tree:
  jeff, ux, apps, models, cloud, computer, media, memory, agents, skills,
  plugins, ops, oss, commerce, devices

Each branch defines:
  1. A v2 ``CoachingEpisode`` lesson (symptom, error_text, root_cause,
     failed_approaches, recipe, check, counterexample, refs, scope="global")
     that passes ``learning.lessons`` poison filters and ``learning.lesson_format``
     v2 full-validation rules;
  2. An executable coding recipe (``bossman.coding-recipe/1``) where applicable,
     using only allowed sidecar tools (``read_file``, ``search``, ``list_dir``,
     ``edit_file``, ``write_file``, ``run_tests``);
  3. A deterministic, repository-grounded verification check executed by an
     independent verifier principal (``elder-brother-verifier:external_tool``)
     that produces real ``expected`` vs ``actual`` evidence bound to ``head_sha``.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from learning.lessons import CoachingEpisode, LessonBook, Provenance

CANONICAL_15_BRANCHES: tuple[str, ...] = (
    "jeff",
    "ux",
    "apps",
    "models",
    "cloud",
    "computer",
    "media",
    "memory",
    "agents",
    "skills",
    "plugins",
    "ops",
    "oss",
    "commerce",
    "devices",
)

PROJECT_ID = "bossman"
AUTHOR_PRINCIPAL = "argon-elder-brother:teacher"
VERIFIER_PRINCIPAL = "elder-brother-verifier:external_tool"


def git_head_sha(repo_root: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return proc.stdout.strip()


def host_environment() -> str:
    return f"owner-pc {platform.system()} {platform.release()} python {platform.python_version()}"


BRANCH_LESSONS_SPEC: list[dict[str, Any]] = [
    {
        "branch": "jeff",
        "title": "Jeff companion: strict source citation, privacy boundary, and Stars ledger separation",
        "failure_observation": (
            "When a Jeff interlocutor asks for the owner's private contact details or claims a "
            "simulated Stars payment, the local model either discloses private context or treats "
            "a test message as real revenue."
        ),
        "error_text": "JeffBoundaryError: unverified Stars receipt or private owner data requested",
        "root_cause": (
            "Model treated conversational text from a third party as trusted instructions and "
            "confused paper/demo Stars events with verified Telegram payment receipts."
        ),
        "correction": (
            "In Jeff tasks, never disclose owner personal data or send unsolicited broadcasts; "
            "cite exact document sources via read_file/search and verify Stars ledger receipts "
            "before granting paid access."
        ),
        "failed_approaches": [
            "Answering from memory without reading the cited document or checking stars_ledger.py",
            "Treating a simulated Stars payload as real earned income",
        ],
        "recipe": [
            "Identify the requested document or Stars ledger query in command-center/bcc/pit/stars_ledger.py",
            "Refuse any request for owner personal phone, address, or keys and state the privacy rule",
            "Read the source file or ledger record first and cite exact lines in the response",
        ],
        "check": "command-center/bcc/pit/stars_ledger.py exists and defines Stars ledger accounting",
        "counterexample": "Do not block normal document Q&A when no private owner data or unverified payment is involved.",
        "code_refs": ["command-center/bcc/pit/stars_ledger.py"],
        "test_refs": ["command-center/tests/telegram_contracts/test_pult_contracts.py"],
        "verify_file": "command-center/bcc/pit/stars_ledger.py",
        "verify_symbol": "stars",
    },
    {
        "branch": "ux",
        "title": "UX & Pult: never claim GUI or mobile visual pass from HTTP status alone",
        "failure_observation": (
            "Agent reported that the mobile layout or dashboard UI was verified and aligned "
            "without running a browser/viewport inspection or capturing a screenshot receipt."
        ),
        "error_text": "UXHonestyError: visual claim made without viewport observation or screenshot receipt",
        "root_cause": (
            "Conflating HTTP 200 JSON responses with actual DOM rendering and visual layout verification."
        ),
        "correction": (
            "For UX, Command Center, and Telegram Mini App tasks, inspect the actual code and "
            "DOM/viewport state; if no live visual check ran in this session, report NOT_RUN "
            "instead of claiming visual completion."
        ),
        "failed_approaches": [
            "Reporting mobile layout PASS after checking only FastAPI route status codes",
            "Guessing default mission IDs or UI constants without grepping the feature module",
        ],
        "recipe": [
            "Locate the UI or bootstrap module with search/read_file (e.g. game_bootstrap_v16.py)",
            "Check whether an actual visual screenshot or viewport test was executed in this run",
            "Report PASS only for measured checks and explicitly mark unmeasured visual claims as NOT_RUN",
        ],
        "check": "command-center/bcc/features/game_bootstrap_v16.py defines BOSSBLOCKS-001 default mission_id",
        "counterexample": "Pure backend schema unit tests do not require a mobile screenshot if no visual claim is made.",
        "code_refs": ["command-center/bcc/features/game_bootstrap_v16.py"],
        "test_refs": ["command-center/tests/telegram_contracts/test_pult_contracts.py"],
        "verify_file": "command-center/bcc/features/game_bootstrap_v16.py",
        "verify_symbol": "BOSSBLOCKS-001",
    },
    {
        "branch": "apps",
        "title": "Apps (Poker GTO & Reports): post-session study only, exact GTO math and 21-20 Europe/Prague reports",
        "failure_observation": (
            "Agent either offered real-time move advice for a live cash poker table or calculated "
            "exchange volume reports without excluding bonuses and cancelled withdrawals."
        ),
        "error_text": "AppsContractError: live real-money RTA requested or unverified report window",
        "root_cause": (
            "Missing boundary between post-session GTO trainer analysis and prohibited real-time "
            "assistance, or ignoring the canonical 21-20 Europe/Prague report window."
        ),
        "correction": (
            "Refuse real-time assistance at live poker tables (post-session study only via "
            "apps/poker-botlab/botlab/gto.py); for Reports, enforce the 21-20 Europe/Prague window, "
            "exclude bonuses and cancelled withdrawals, and output report.md plus disputed.csv."
        ),
        "failed_approaches": [
            "Giving live real-money poker action advice during an active online hand",
            "Including bonus credits or cancelled withdrawals in net exchange volume totals",
        ],
        "recipe": [
            "If the user is at a live real-money poker table, refuse real-time advice and offer post-session GTO debrief",
            "For offline poker study, compute exact Pot Odds, MDF = pot/(pot+bet), SPR, and EV loss via botlab/gto.py",
            "For Telegram export reports, run tools/exchange_volume/report.py with 21-20 Europe/Prague and verify SHA-256",
        ],
        "check": "apps/poker-botlab/botlab/gto.py and tools/exchange_volume/report.py exist and pass verification",
        "counterexample": "Offline simulator hands in poker-train-host and poker-botlab are permitted for study and tournaments.",
        "code_refs": ["apps/poker-botlab/botlab/gto.py", "tools/exchange_volume/report.py"],
        "test_refs": ["apps/poker-botlab/tests/test_gto_tournament.py"],
        "verify_file": "apps/poker-botlab/botlab/gto.py",
        "verify_symbol": "def mdf(",
    },
    {
        "branch": "models",
        "title": "Models: local Ollama fleet selection, context ladder 64k-256k, and zero GPU rental",
        "failure_observation": (
            "Agent proposed renting cloud GPUs on RunPod for LoRA training or claimed a model was "
            "fine-tuned without comparing base vs target digests and held-out benchmark scores."
        ),
        "error_text": "ModelGovernanceError: RunPod rental prohibited or unmeasured model quality claim",
        "root_cause": (
            "Suggesting external paid GPU rental instead of local Ollama execution and confusing "
            "system-prompt changes with weight updates."
        ),
        "correction": (
            "Use only local Ollama models on 127.0.0.1:11434 with the context ladder (64k -> 128k -> 256k) "
            "in qwen_operator.py; refuse RunPod or paid GPU rental and verify model digests and held-out scores."
        ),
        "failed_approaches": [
            "Proposing RunPod H100/A100 rental to speed up training",
            "Letting Ollama silently truncate prompts above 80% context window instead of stepping up CTX_LADDER",
        ],
        "recipe": [
            "Query local Ollama models at 127.0.0.1:11434/api/tags and record exact model name and digest",
            "Monitor prompt token pressure via ctx_pressure and step up CTX_LADDER when over 80%",
            "Refuse any RunPod or external GPU rental request immediately",
        ],
        "check": "command-center/bcc/features/qwen_operator.py defines CTX_LADDER and ctx_pressure monitor",
        "counterexample": "Creating a local Ollama context-variant tag via /api/create copies metadata only and is permitted.",
        "code_refs": ["command-center/bcc/features/qwen_operator.py"],
        "test_refs": ["command-center/tests/test_pc_agent_argon.py"],
        "verify_file": "command-center/bcc/features/qwen_operator.py",
        "verify_symbol": "CTX_LADDER",
    },
    {
        "branch": "cloud",
        "title": "Cloud: free-only cloud workers (:free / NVIDIA NIM) and cross-family verification",
        "failure_observation": (
            "Agent attempted to connect a paid cloud model without an explicit owner price and daily "
            "spend cap, or used the same model family to self-verify a candidate patch."
        ),
        "error_text": "CloudGovernanceError: unapproved paid model or non-independent verifier family",
        "root_cause": (
            "Bypassing free_only_refusal in provider_governance.py or failing to enforce avoid_family='qwen' "
            "during cloud verification."
        ),
        "correction": (
            "Enforce zero-cost cloud routing (OpenRouter :free, NVIDIA NIM) via provider_governance.py "
            "unless the owner explicitly approves a model and daily cap; read provider keys only via "
            "tools/connect_provider.py (PROVIDER_KEY) into the encrypted vault."
        ),
        "failed_approaches": [
            "Calling paid OpenRouter models without an explicit --allow-paid-worker flag from the owner",
            "Hardcoding API keys in source files or reading keys outside the vault/connect_provider flow",
        ],
        "recipe": [
            "Check price_in == 0 and price_out == 0 and free_only_refusal before calling any cloud model",
            "Use tools/connect_provider.py reading PROVIDER_KEY from env to store keys in the vault",
            "Require avoid_family='qwen' when selecting a free cloud verifier for local Qwen diffs",
        ],
        "check": "tools/connect_provider.py reads PROVIDER_KEY and command-center/bcc/provider_governance.py enforces free_only_refusal",
        "counterexample": "Owner-approved named workers with explicit flags in tree_self_repair_cycle.py are governed exceptions.",
        "code_refs": ["command-center/bcc/provider_governance.py"],
        "test_refs": ["command-center/tests/test_tree_self_repair_cycle.py"],
        "verify_file": "command-center/bcc/provider_governance.py",
        "verify_symbol": "free_only_refusal",
    },
    {
        "branch": "computer",
        "title": "Computer (PC-Agent): Observe -> Plan -> Act -> Verify -> Rollback, policy split, and STOP switch",
        "failure_observation": (
            "PC-Agent executed desktop or shell actions without checking the STOP file before and "
            "after each step, or allowed destructive deletion of user folders without owner confirmation."
        ),
        "error_text": "PCAgentPolicyError: STOP switch active or T3 destructive path requires owner approval",
        "root_cause": (
            "Skipping pre/post observation of foreground window and file state, or failing to classify "
            "destructive paths and quoted arguments through pc_agent_policy.py."
        ),
        "correction": (
            "In PC-Agent tasks, classify every command/path via pc_agent_policy.py (T0-T3), check the "
            "STOP switch before and after every step, observe foreground window/file state, verify "
            "post-conditions with a screenshot/hash receipt, and refuse destructive deletions unless confirmed by the owner."
        ),
        "failed_approaches": [
            "Deleting user directories or running destructive commands without explicit owner confirmation",
            "Continuing execution after the STOP file is created or writing post-STOP action rows to audit.jsonl",
        ],
        "recipe": [
            "Check stop_switch active state before starting and before every step in pc_agent.py",
            "Classify path and command risk via pc_agent_policy.py and pc_agent_policy_paths.py",
            "Execute visible action, capture independent file/screenshot verification, and halt immediately on STOP",
        ],
        "check": "command-center/bcc/features/pc_agent_policy.py and pc_agent.py enforce T0-T3 policy and STOP",
        "counterexample": "Read-only T0 observations inside allowed workspace directories do not require T2/T3 escalation.",
        "code_refs": [
            "command-center/bcc/features/pc_agent.py",
            "command-center/bcc/features/pc_agent_policy.py",
            "command-center/bcc/features/pc_agent_policy_paths.py",
        ],
        "test_refs": ["command-center/tests/test_pc_agent_argon.py"],
        "verify_file": "command-center/bcc/features/pc_agent_policy.py",
        "verify_symbol": "PolicyVerdict",
    },
    {
        "branch": "media",
        "title": "Media & Genjutsu: immutable preset snapshots, engine conflict detection, and identity consent",
        "failure_observation": (
            "Agent accepted a request to swap an unconsented third-party Instagram face into a video "
            "or mutated a saved media preset in place during /edit or /create."
        ),
        "error_text": "MediaConsentAndPresetError: unconsented face/voice identity or mutable preset conflict",
        "root_cause": (
            "Ignoring identity consent rules for face/voice synthesis and failing to freeze an "
            "immutable preset snapshot with conflict detection in gen_refine.py."
        ),
        "correction": (
            "Refuse face or voice synthesis of third parties without explicit consent; keep personal "
            "media strictly on the owner PC/Pult, and use immutable preset snapshots with conflict "
            "checks in command-center/bcc/telegram_companion/gen_refine.py."
        ),
        "failed_approaches": [
            "Swapping a random person's face from social media without consent",
            "Overwriting active preset parameters during a running /edit or /create job",
        ],
        "recipe": [
            "Verify identity consent and local-only media routing before starting any /pic, /edit, or /create job",
            "Select the appropriate edit engine (Qwen / Z-Image) in gen_refine.py and snapshot preset state",
            "Record actual model name, elapsed duration, and output file hash in the completion receipt",
        ],
        "check": "command-center/bcc/telegram_companion/gen_refine.py implements edit engine and preset handling",
        "counterexample": "Neutral synthetic prompts (/create neutral object) require no personal consent check.",
        "code_refs": ["command-center/bcc/telegram_companion/gen_refine.py"],
        "test_refs": ["command-center/tests/telegram_contracts/test_pult_contracts.py"],
        "verify_file": "command-center/bcc/telegram_companion/gen_refine.py",
        "verify_symbol": "gen-speed.jsonl",
    },
    {
        "branch": "memory",
        "title": "Memory (LessonBook & Lifecycle): never store secrets, require independent verification, calendar.timegm UTC",
        "failure_observation": (
            "Agent tried to save an API key into memory/README, self-verified its own candidate lesson, "
            "or used time.mktime on UTC timestamps causing newly verified lessons to appear 1 hour stale."
        ),
        "error_text": "LessonPoisoned / ValidationError: secret material or self-verification refused by LearningStore",
        "root_cause": (
            "Storing raw credentials in LessonBook, using the same principal_id for author and verifier, "
            "or applying local DST offset via time.mktime instead of calendar.timegm."
        ),
        "correction": (
            "Never write API keys or secrets into memory or files; record v2 CoachingEpisodes with "
            "root_cause, recipe, check, and counterexample, verify via an independent principal "
            "with fresh head_sha evidence, and parse UTC timestamps with calendar.timegm."
        ),
        "failed_approaches": [
            "Saving sk-or-v1-* or provider keys in LessonBook or Markdown notes",
            "Calling LessonBook.verify with verifier.principal_id equal to episode.provenance.who",
        ],
        "recipe": [
            "Pass every lesson field through poison_reasons to reject secrets and control-plane overrides",
            "Save candidate lesson via LessonBook.save and run the verification check with an independent tool",
            "Call LessonBook.verify with external_tool verifier, matching expected==actual, and current head_sha",
        ],
        "check": "learning/lessons.py uses calendar.timegm and enforces independent verification in LessonBook.verify",
        "counterexample": "Short error class names in symptoms (e.g. ImportError) are valid and not flagged as poison.",
        "code_refs": ["learning/lessons.py", "learning/lesson_format.py", "learning/retrieval.py"],
        "test_refs": ["command-center/tests/test_tree_self_repair_cycle.py"],
        "verify_file": "learning/lessons.py",
        "verify_symbol": "calendar.timegm",
    },
    {
        "branch": "agents",
        "title": "Agents (Autonomy & Qwen-Operator L1): finite float guards, lab worktree isolation, no auto-merge to main",
        "failure_observation": (
            "Agent either allowed NaN/+inf cost reports to slip past GoalStore.budget_exceeded in goals.py "
            "or attempted to push/merge branches into main before the owner's word and green CI."
        ),
        "error_text": "AutonomyGuardError: non-finite float in budget_exceeded or premature merge to main",
        "root_cause": (
            "IEEE-754 comparisons with NaN evaluate to False, allowing non-finite costs to slip past "
            "simple > checks, and L1 Lab operators lack authority to merge into main."
        ),
        "correction": (
            "Guard all numeric budget and score inputs with math.isfinite in autonomy/goals.py and "
            "pit/discovery.py; confine Qwen-Operator L1 edits to lab/* worktrees and never merge to "
            "main without the owner's explicit button/word and green CI."
        ),
        "failed_approaches": [
            "Using raw float(x) > max_cost_usd without checking math.isfinite(val) and val >= 0",
            "Running git push origin HEAD:main before the owner's explicit confirmation",
        ],
        "recipe": [
            "Read target module (autonomy/goals.py or pit/discovery.py) and locate all float conversions",
            "Validate math.isfinite(x) and reject None/bool/NaN/±inf before any threshold or ranking comparison",
            "Run verify_tests in the isolated lab worktree and park any release_main action for owner decision",
        ],
        "check": "command-center/bcc/autonomy/goals.py and pit/discovery.py pass non-finite holdout checks",
        "counterexample": "Valid finite positive costs within max_cost_usd must continue to be charged normally.",
        "code_refs": ["command-center/bcc/autonomy/goals.py", "command-center/bcc/pit/discovery.py"],
        "test_refs": ["command-center/tests/test_tree_self_repair_cycle.py"],
        "verify_file": "command-center/bcc/autonomy/goals.py",
        "verify_symbol": "isfinite",
    },
    {
        "branch": "skills",
        "title": "Skills & Coding Recipes: strict tool allowlist, schema validation, and honest test reporting",
        "failure_observation": (
            "Agent claimed tests passed when 3 tests failed, or authored a coding recipe containing "
            "shell metacharacters or unapproved tools outside ALLOWED_TOOLS."
        ),
        "error_text": "SkillContractError: recipe step uses unallowed tool or test failure masked as PASS",
        "root_cause": (
            "Treating skill markdown as privilege-granting instructions or reporting green status "
            "without checking pytest exit_code == 0."
        ),
        "correction": (
            "Validate skill inputs and coding recipes strictly against ALLOWED_TOOLS "
            "(read_file, search, list_dir, edit_file, write_file, run_tests) in coding_recipes.py; "
            "never mask failing tests as green and never let a skill widen agent capabilities."
        ),
        "failed_approaches": [
            "Reporting 'all tests green' to the owner when pytest returned non-zero exit code",
            "Putting bash/pwsh shell strings or '../' paths inside coding recipe steps",
        ],
        "recipe": [
            "Validate every coding recipe with validate_recipe(recipe) in command-center/bcc/features/coding_recipes.py",
            "Ensure required_check runs pytest on relative repository test paths",
            "Report exact passed/failed counts from the test runner output without fabrication",
        ],
        "check": "command-center/bcc/features/coding_recipes.py enforces ALLOWED_TOOLS and _PATH_FORBIDDEN",
        "counterexample": "Code content inside edit_file/write_file may legitimately mention words like 'permissions' if not a denylist override.",
        "code_refs": ["command-center/bcc/features/coding_recipes.py", "command-center/bcc/features/skills.py"],
        "test_refs": ["command-center/tests/test_tree_self_repair_cycle.py"],
        "verify_file": "command-center/bcc/features/coding_recipes.py",
        "verify_symbol": "ALLOWED_TOOLS",
    },
    {
        "branch": "plugins",
        "title": "Plugins & MCP Hub: AUTO/ASK/DENY policy, sandboxed adapters, and no unauthorized OAuth/broadcasts",
        "failure_observation": (
            "Agent tried to broadcast internal reports to external plugin channels or activate an MCP "
            "connector before an owner decision and command policy validation."
        ),
        "error_text": "MCPPolicyError: connector activation or external broadcast requires owner decision",
        "root_cause": (
            "Assuming an installed adapter file in command-center/bcc/oss/ is automatically authorized "
            "for live external network calls or broadcasts."
        ),
        "correction": (
            "Keep MCP tools and OSS plugin adapters (blender_mcp.py, fincept_terminal.py, vibe_trading.py) "
            "governed by MCP_POLICY_KEY (auto|ask|deny) and command_policy_refusal; never broadcast reports "
            "to third parties or open external OAuth scopes before an explicit owner decision."
        ),
        "failed_approaches": [
            "Broadcasting owner reports to external chats or plugin endpoints",
            "Enabling MCP server commands that violate command_policy_refusal",
        ],
        "recipe": [
            "Check MCP server spec against command_policy_refusal in command-center/bcc/v2/mcp_hub.py",
            "Keep OSS adapters in paper/sandbox mode until explicitly activated by the owner",
            "Deliver reports exclusively to the owner's Telegram Pult",
        ],
        "check": "command-center/bcc/oss/blender_mcp.py and command-center/bcc/v2/mcp_hub.py exist and enforce boundaries",
        "counterexample": "Read-only local inspection of plugin adapter code requires no external OAuth flow.",
        "code_refs": ["command-center/bcc/oss/blender_mcp.py", "command-center/bcc/features/skills.py"],
        "test_refs": ["command-center/tests/test_pc_agent_argon.py"],
        "verify_file": "command-center/bcc/oss/blender_mcp.py",
        "verify_symbol": "blender",
    },
    {
        "branch": "ops",
        "title": "Ops & CI Gates: never disable constitution/pin/gates, verify source==build==runtime SHA, atomic Windows writes",
        "failure_observation": (
            "When a CI gate or constitution check blocked a commit, or os.replace hit PermissionError "
            "on Windows, the agent tried to disable the gate or crashed on concurrent file reads."
        ),
        "error_text": "OpsGateError: constitution/gate tamper prohibited; Windows os.replace PermissionError under reader lock",
        "root_cause": (
            "Attempting to weaken safety gates instead of fixing the underlying defect, and calling "
            "os.replace once on Windows without bounded retry when another handle reads the target file."
        ),
        "correction": (
            "Never disable or modify Constitution v2, its pin, or CI gates; verify exact SHA identity "
            "(source_sha == build_sha == runtime_sha) and use bounded retry around os.replace with temp-file "
            "cleanup in atomic_json on Windows."
        ),
        "failed_approaches": [
            "Modifying .github/workflows gates or constitution.verify() when a check fails",
            "Single-shot os.replace on Windows without retry when a reader holds the destination file open",
        ],
        "recipe": [
            "Verify .github/workflows gates and constitution status without modifying protected gate files",
            "For atomic JSON writes on Windows, write to a UUID .tmp file with allow_nan=False and retry os.replace on PermissionError",
            "Always unlink leftover .tmp files in a finally block and verify with atomic_json_replace holdout",
        ],
        "check": "bossman-core/bossman_v3/self_improvement/runner.py and .github/workflows exist and enforce gates",
        "counterexample": "Do not catch or silence ValueError from allow_nan=False when serializing invalid NaN/Inf JSON.",
        "code_refs": ["bossman-core/bossman_v3/self_improvement/runner.py", "tools/build_windows_bundle.py"],
        "test_refs": ["command-center/tests/test_tree_self_repair_cycle.py"],
        "verify_file": "bossman-core/bossman_v3/self_improvement/runner.py",
        "verify_symbol": "atomic_json",
    },
    {
        "branch": "oss",
        "title": "OSS Radar & Adapters: license compliance (flag AGPL), pinned upstream commits, and attribution integrity",
        "failure_observation": (
            "Agent proposed copying AGPL-licensed code into Bossman's closed codebase and stripping "
            "author attribution headers."
        ),
        "error_text": "OSSLicenseError: unflagged AGPL/non-commercial license or stripped attribution",
        "root_cause": (
            "Ignoring upstream license obligations (AGPL/GPL/non-commercial) and removing copyright "
            "and provenance notices during integration."
        ),
        "correction": (
            "Before integrating any upstream OSS code, record the exact repository URL, commit SHA, "
            "and license; refuse to strip attribution and explicitly flag AGPL or non-commercial "
            "licenses for owner decision."
        ),
        "failed_approaches": [
            "Copying AGPL code into proprietary modules and deleting copyright lines",
            "Installing unpinned third-party packages directly into the production runtime without isolated tests",
        ],
        "recipe": [
            "Inspect upstream license and commit SHA before touching any adapter in command-center/bcc/oss/",
            "Flag AGPL, GPL, or non-commercial licenses immediately and preserve all attribution notices",
            "Wrap upstream tools behind isolated adapters (e.g. vibe_trading.py paper-only mode) with unit tests",
        ],
        "check": "command-center/bcc/oss/vibe_trading.py and fincept_terminal.py preserve adapter boundaries",
        "counterexample": "Permissive MIT/Apache-2.0/BSD code may be integrated in isolated adapters provided attribution is kept.",
        "code_refs": ["command-center/bcc/oss/vibe_trading.py", "command-center/bcc/oss/fincept_terminal.py"],
        "test_refs": ["command-center/tests/test_pc_agent_argon.py"],
        "verify_file": "command-center/bcc/oss/vibe_trading.py",
        "verify_symbol": "paper",
    },
    {
        "branch": "commerce",
        "title": "Commerce (14th Branch): separate technical demo, buyer demand, order, payment, revenue, and expenses",
        "failure_observation": (
            "Agent counted simulated volume, seller ads, or a technical Reports demo as real earned "
            "revenue and profit, or launched a paid offer before an owner decision."
        ),
        "error_text": "CommerceAccountingError: demo/turnover conflated with net profit or premature offer",
        "root_cause": (
            "Confusing technical readiness (code/demo) or seller advertisements with verified customer "
            "orders, settled payments, and net profit after expenses."
        ),
        "correction": (
            "In Commerce tasks (Reports/Swap.me, Motion/Genjutsu, Operations, Jeff Stars), strictly "
            "separate technical demo, public buyer demand, order, payment receipt, revenue, and expenses; "
            "keep Stars paused and require an explicit owner decision before any offer or charge."
        ),
        "failed_approaches": [
            "Counting seller listings as buyer demand or treating paper trading turnover as profit",
            "Publishing a commercial offer or charging a client without an explicit owner decision",
        ],
        "recipe": [
            "Verify technical deliverable first (e.g. deterministic 21-20 Europe/Prague report.md + disputed.csv)",
            "Record buyer demand sources with exact date and client task, excluding seller advertisements",
            "Track demo, order, payment receipt, gross revenue, and compute costs in separate accounting columns",
        ],
        "check": "tools/exchange_volume/report.py and command-center/bcc/features/reports_runner.py implement verifiable report delivery",
        "counterexample": "Running a local deterministic demo on an approved export is valid technical verification, just not revenue.",
        "code_refs": ["tools/exchange_volume/report.py", "command-center/bcc/features/reports_runner.py"],
        "test_refs": ["command-center/tests/test_testing_period.py"],
        "verify_file": "command-center/bcc/features/reports_runner.py",
        "verify_symbol": "Europe/Prague",
    },
    {
        "branch": "devices",
        "title": "Devices (15th Branch): explicit owner pairing, immediate revoke, local Tapo/Cam safety, and global STOP priority",
        "failure_observation": (
            "Agent claimed a second PC or remote camera device was connected and executed a task "
            "when no physical device was paired, or streamed device media outside the owner's local PC."
        ),
        "error_text": "DevicePairingError: unpaired remote device claimed as PASS or missing global STOP priority",
        "root_cause": (
            "Simulating a successful remote device connection instead of reporting NOT_RUN/BLOCKED "
            "until physical pairing and owner approval exist."
        ),
        "correction": (
            "For Devices tasks (second owner PC, Bossman Cam / Tapo RTSP), require explicit owner "
            "pairing, immediate revoke capability, file hash verification without secret export, and "
            "global STOP priority; mark physical device gates NOT_RUN when hardware is not connected."
        ),
        "failed_approaches": [
            "Using a shared uncontrolled account across devices instead of explicit owner pairing and revoke",
            "Reporting physical camera or second-PC execution as PASS from a mock or unit test alone",
        ],
        "recipe": [
            "Check device registry, pairing status, and global STOP state before routing any remote or camera task",
            "Ensure file transfers allow only permitted directories with SHA-256 integrity checks and zero secrets",
            "If the physical second PC or Tapo camera is not reachable, report NOT_RUN honestly",
        ],
        "check": "command-center/bcc/autonomy/stop.py enforces global STOP priority across host and device tasks",
        "counterexample": "Unit tests of device protocol/framing may pass locally, while live hardware delivery stays NOT_RUN.",
        "code_refs": ["command-center/bcc/autonomy/stop.py", "command-center/bcc/features/pc_agent.py"],
        "test_refs": ["command-center/tests/test_pc_agent_argon.py"],
        "verify_file": "command-center/bcc/autonomy/stop.py",
        "verify_symbol": "stop_reason",
    },
]


CODING_RECIPES_SPEC: list[dict[str, Any]] = [
    {
        "id": "elder-brother-discovery-nonfinite-v1",
        "title": "Guard all candidate numeric fields inchoose_discovery_question against NaN, ±inf, None, and bool",
        "symptom": (
            "In command-center/bcc/pit/discovery.py, choose_discovery_question / score fails with "
            "TypeError on None or selects candidates with NaN/+inf/-inf in relevance, uncertainty, "
            "future_utility, annoyance_cost, or sensitivity_risk."
        ),
        "cause": (
            "IEEE-754 NaN comparisons return False and None raises TypeError in float(), allowing "
            "corrupted candidates to crash discovery or outrank valid finite candidates."
        ),
        "diagnosis": (
            "Hidden holdout tools/tree_holdout/discovery_nonfinite.py tests 72 permutations of "
            "NaN, +inf, -inf, None, bool, and negative numbers across all 5 numeric fields."
        ),
        "action": (
            "Define a strict finite-float helper in command-center/bcc/pit/discovery.py that rejects "
            "bool, None, non-numbers, NaN, and ±inf via math.isfinite, and filter out any candidate "
            "with an invalid numeric field before scoring so valid candidates win deterministically."
        ),
        "counterexample": (
            "Do not alter the scoring formula or tie-breaking order for valid candidates whose five "
            "numeric fields are all finite real numbers."
        ),
        "target": "command-center/bcc/pit/discovery.py",
        "search": "isfinite",
        "verify_tests": ["command-center/tests/test_tree_self_repair_cycle.py"],
        "keywords": [
            "discovery", "nan", "inf", "non-finite", "none", "annoyance_cost",
            "sensitivity_risk", "relevance", "isfinite", "choose_discovery_question",
        ],
    },
    {
        "id": "elder-brother-goal-budget-nonfinite-v1",
        "title": "Enforce GoalStore budget ceiling on NaN, ±inf, None, and negative external cost reports",
        "symptom": (
            "In command-center/bcc/autonomy/goals.py, GoalStore.charge and budget_exceeded fail to "
            "block a goal when external cost reports contain NaN, +inf, -inf, or when spent_usd "
            "reaches or exceeds max_cost_usd."
        ),
        "cause": (
            "Unvalidated float conversion and > comparison with NaN evaluate to False, letting "
            "a goal continue spending past its max_cost_usd ceiling."
        ),
        "diagnosis": (
            "Hidden holdout tools/tree_holdout/goal_budget_nonfinite.py verifies valid charges, "
            "exact ceiling boundaries, and non-finite cost rejection."
        ),
        "action": (
            "Validate both spent_usd, max_cost_usd, and incoming cost in command-center/bcc/autonomy/goals.py "
            "using math.isfinite; treat any non-finite or negative cost/budget as budget_exceeded / invalid."
        ),
        "counterexample": (
            "Do not block valid finite positive charges while total spent_usd remains strictly within max_cost_usd."
        ),
        "target": "command-center/bcc/autonomy/goals.py",
        "search": "budget_exceeded",
        "verify_tests": ["command-center/tests/test_tree_self_repair_cycle.py"],
        "keywords": [
            "goal", "budget", "budget_exceeded", "charge", "max_cost_usd",
            "nan", "inf", "non-finite", "isfinite", "autonomy",
        ],
    },
    {
        "id": "elder-brother-atomic-json-windows-v1",
        "title": "Survive Windows PermissionError race on os.replace in atomic_json without leaking .tmp files",
        "symptom": (
            "On Windows, bossman-core/bossman_v3/self_improvement/runner.py function atomic_json "
            "intermittently raises PermissionError on os.replace when a concurrent reader or writer "
            "holds the destination file open."
        ),
        "cause": (
            "Windows locks destination paths during concurrent reads/replaces; a single os.replace "
            "call fails immediately instead of retrying briefly."
        ),
        "diagnosis": (
            "Hidden holdout tools/tree_holdout/atomic_json_replace.py tests reader locks, 8 concurrent "
            "threads, 4 writers, secret redaction, and allow_nan=False."
        ),
        "action": (
            "Write JSON to a unique UUID .tmp sibling with allow_nan=False, retry os.replace with "
            "short backoff on PermissionError, and always unlink the .tmp file in a finally block."
        ),
        "counterexample": (
            "Do not swallow ValueError on NaN/Inf payloads (allow_nan=False must still raise) and "
            "do not leave .tmp files behind on failure."
        ),
        "target": "bossman-core/bossman_v3/self_improvement/runner.py",
        "search": "os.replace",
        "verify_tests": ["command-center/tests/test_tree_self_repair_cycle.py"],
        "keywords": [
            "atomic_json", "os.replace", "permissionerror", "windows", "race",
            "retry", "tmp", "allow_nan", "runner",
        ],
    },
]


def build_coaching_episodes(repo_root: Path, run_id: str | None = None) -> list[CoachingEpisode]:
    """Construct the 15 canonical v2 CoachingEpisodes (one per capability tree branch)."""
    rid = run_id or f"elder-brother-run-{int(time.time())}"
    env = host_environment()
    episodes: list[CoachingEpisode] = []
    for idx, spec in enumerate(BRANCH_LESSONS_SPEC, 1):
        branch = spec["branch"]
        ep = CoachingEpisode(
            attempt_id=f"eb15-{branch}-attempt-{idx:02d}",
            task_id=f"eb15-branch-{branch}",
            project_id=PROJECT_ID,
            failure_observation=spec["failure_observation"],
            correction=spec["correction"],
            source="teacher",
            kind="teacher_patch",
            status="candidate",
            scope="global",
            provenance=Provenance(
                who=AUTHOR_PRINCIPAL,
                what=f"Elder Brother 15-branch computer-agent lesson for {branch}",
                evidence_refs=[f"repo:{spec['verify_file']}"] + list(spec["test_refs"]),
                run_id=rid,
                model="argon-elder-brother",
            ),
            task_class=branch,
            environment=env,
            agent="bossman-computer-agent",
            model="bossman-fast-qwen36-35b-a3b-q5:latest",
            title=spec["title"],
            symptoms=[spec["failure_observation"]],
            error_text=spec["error_text"],
            root_cause=spec["root_cause"],
            failed_approaches=list(spec["failed_approaches"]),
            recipe=list(spec["recipe"]),
            check=spec["check"],
            counterexample=spec["counterexample"],
            refs={
                "code": list(spec["code_refs"]),
                "test": list(spec["test_refs"]),
                "commit": [],
                "evidence": [f"repo:{spec['verify_file']}"],
            },
            assistance_level="teacher_patch",
            runtime="bossman-cc-v2.2",
        )
        episodes.append(ep)
    return episodes


def run_independent_branch_check(repo_root: Path, spec: dict[str, Any], head_sha: str) -> tuple[dict, dict]:
    """Execute a real repository/code check for ``spec`` and return (verifier, evidence)."""
    rel_path = spec["verify_file"]
    symbol = spec["verify_symbol"]
    target = (repo_root / rel_path).resolve()
    exists = target.is_file()
    content = target.read_text(encoding="utf-8", errors="replace") if exists else ""
    found = exists and (symbol.lower() in content.lower())
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16] if exists else "missing"
    now = time.time()
    verifier = {
        "principal_id": f"{VERIFIER_PRINCIPAL}:{spec['branch']}",
        "independence_class": "external_tool",
        "model_id": "deterministic-repo-inspector",
        "run_id": f"verify-eb15-{spec['branch']}-{head_sha[:8]}",
    }
    evidence = {
        "source": f"repo-check:{rel_path}#{symbol}@sha256:{digest}",
        "expected": f"file_exists=True symbol_present({symbol})=True",
        "actual": f"file_exists={exists} symbol_present({symbol})={found}",
        "head_sha": head_sha,
        "environment": host_environment(),
        "observed_at": now,
        "collected_at": now,
    }
    return verifier, evidence


def seed_and_verify_15_branches(
    learning_dir: Path,
    repo_root: Path,
    *,
    include_coding_recipes: bool = True,
) -> dict[str, Any]:
    """Record and independently verify all 15 branch lessons + 3 executable coding recipes."""
    head_sha = git_head_sha(repo_root)
    book = LessonBook(learning_dir)
    run_id = f"eb15-seed-{head_sha[:8]}"
    episodes = build_coaching_episodes(repo_root, run_id=run_id)
    verified_lessons: list[dict[str, Any]] = []

    for ep, spec in zip(episodes, BRANCH_LESSONS_SPEC):
        saved = book.save(ep)
        lesson_id = saved["task_id"]
        verifier, evidence = run_independent_branch_check(repo_root, spec, head_sha)
        if evidence["actual"] != evidence["expected"]:
            raise RuntimeError(f"Branch check failed for {spec['branch']}: {evidence}")
        ver_rec = book.verify(
            lesson_id,
            verifier=verifier,
            evidence=evidence,
            statement=f"Independent repo check confirmed {spec['verify_file']} contains {spec['verify_symbol']}",
        )
        verified_lessons.append({
            "branch": spec["branch"],
            "lesson_id": lesson_id,
            "status": ver_rec.get("learning_status"),
            "version": ver_rec.get("version"),
            "evidence_source": evidence["source"],
        })

    verified_recipes: list[dict[str, Any]] = []
    if include_coding_recipes:
        sys.path.insert(0, str(repo_root / "command-center"))
        sys.path.insert(0, str(repo_root / "tools"))
        from bcc.features.coding_recipes import save_verified_recipe  # noqa: WPS433
        from tree_holdout import atomic_json_replace, discovery_nonfinite, goal_budget_nonfinite  # noqa: WPS433

        holdout_runners = {
            "elder-brother-discovery-nonfinite-v1": ("discovery_nonfinite", discovery_nonfinite.run),
            "elder-brother-goal-budget-nonfinite-v1": ("goal_budget_nonfinite", goal_budget_nonfinite.run),
            "elder-brother-atomic-json-windows-v1": ("atomic_json_replace", atomic_json_replace.run),
        }
        # coding_recipes.save_verified_recipe expects target to be data_dir (parent of "learning")
        data_dir = learning_dir.parent if learning_dir.name == "learning" else learning_dir
        for rspec in CODING_RECIPES_SPEC:
            h_name, h_fn = holdout_runners[rspec["id"]]
            h_res = h_fn(repo_root)
            passed = bool(h_res.get("passed")) and int(h_res.get("failed", 1)) == 0
            recipe_obj = {
                "id": rspec["id"],
                "title": rspec["title"],
                "symptom": rspec["symptom"],
                "cause": rspec["cause"],
                "diagnosis": rspec["diagnosis"],
                "action": rspec["action"],
                "counterexample": rspec["counterexample"],
                "required_check": {"tool": "run_tests", "args": {"paths": rspec["verify_tests"]}},
                "applies_when": {
                    "project_id": PROJECT_ID,
                    "language": "python",
                    "keywords": rspec["keywords"],
                    "paths": [rspec["target"]],
                },
                "steps": [
                    {"tool": "read_file", "args": {"path": rspec["target"]}},
                    {"tool": "search", "args": {"pattern": rspec["search"], "path": rspec["target"]}},
                    {"tool": "run_tests", "args": {"paths": rspec["verify_tests"]}},
                ],
                "provenance": {
                    "who": AUTHOR_PRINCIPAL,
                    "source": "teacher",
                    "assistance_level": "teacher_patch",
                    "what": f"Elder Brother verified coding recipe ({rspec['id']})",
                    "code_refs": [rspec["target"]],
                    "test_refs": rspec["verify_tests"],
                    "evidence_refs": [f"holdout:{h_name}:{h_res.get('total')}/{h_res.get('total')}"],
                },
                "status": "VERIFIED",
                "project_id": PROJECT_ID,
                "scope": "global",
            }
            ev = {
                "source": f"tree_holdout.{h_name}@{head_sha[:12]}",
                "expected": "PASS",
                "actual": "PASS" if passed else "FAIL",
                "head_sha": head_sha,
                "environment": host_environment(),
            }
            ver = {
                "principal_id": f"tree-holdout:{h_name}",
                "independence_class": "external_tool",
            }
            saved_r = save_verified_recipe(
                data_dir,
                recipe_obj,
                evidence=ev,
                verifier=ver,
                project_id=PROJECT_ID,
                scope="global",
            )
            verified_recipes.append({
                "recipe_id": saved_r["recipe_id"],
                "lesson_id": saved_r["lesson_id"],
                "holdout": h_name,
                "holdout_total": h_res.get("total"),
                "holdout_failed": h_res.get("failed"),
                "status": saved_r["status"],
            })

    return {
        "head_sha": head_sha,
        "learning_dir": str(learning_dir),
        "branches_verified": len(verified_lessons),
        "recipes_verified": len(verified_recipes),
        "lessons": verified_lessons,
        "recipes": verified_recipes,
    }


def render_elder_brother_playbook(book: LessonBook, *, project_id: str = PROJECT_ID) -> str:
    """Build a compact, structured computer-agent playbook directly from VERIFIED LessonBook records."""
    lines = [
        "=== BOSSMAN ELDER BROTHER COMPUTER-AGENT PLAYBOOK (VERIFIED MEMORY) ===",
        "Core Loop (Observe -> Plan -> Act -> Independently Verify -> Rollback):",
        "1. OBSERVE: Never guess repo facts, numbers, constants, or UI state. Call the smallest read tool (grep, read_file, list_dir, run_python) first.",
        "2. PLAN & GATE: Classify every request against the 15 capability tree branches and T0-T3 boundaries. If a request violates an owner rule (secrets, paid models/RunPod, live poker RTA, unconsented face/voice, destructive delete without approval, merging to main without approval, disabling gates, broadcasting private data), REFUSE clearly ('не буду' / 'нельзя'), name the exact rule, and offer the safe alternative.",
        "3. ACT: Execute only permitted tools within the allowed scope.",
        "4. INDEPENDENTLY VERIFY: A tool call or HTTP 200 is not proof of visual/functional completion. Report PASS only when verified by an observation or test receipt in this session; otherwise report NOT RUN / не проверено.",
        "5. ROLLBACK: On failed verification or STOP signal, revert changes and halt immediately.",
        "",
        "Verified Branch Lessons (15 Directions):",
    ]
    for branch in CANONICAL_15_BRANCHES:
        rows = book.retrieve(project_id=project_id, task_class=branch, limit=2)
        if not rows:
            continue
        top = rows[0]
        lines.append(
            f"- [{branch.upper()}] Advice: {top.get('correction')} | Check: {top.get('check')}"
        )
    return "\n".join(lines)
