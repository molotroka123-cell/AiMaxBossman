from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "bossman-core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from bossman.openclaw_bridge import (  # noqa: E402
    OpenClawBridge,
    OpenClawBridgeError,
    fingerprint_skill_dir,
    sanitized_openclaw_env,
)

CATALOG = ROOT / "integrations" / "openclaw" / "curated-skills.json"
REVIEW = ROOT / "integrations" / "openclaw" / "security-review-20260907.json"


class FakeProc:
    def __init__(self, code: int = 0, out: str = "", err: str = "") -> None:
        self.returncode = code
        self.stdout = out
        self.stderr = err


class FakeRunner:
    def __init__(self, *, verify_payload=None, verify_code=0, install_code=0) -> None:
        self.calls = []
        self.verify_payload = {"ok": True, "decision": "pass"} if verify_payload is None else verify_payload
        self.verify_code = verify_code
        self.install_code = install_code

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if argv[-1:] == ["--version"]:
            return FakeProc(out="OpenClaw 2026.9.0\n")
        if argv[1:4] == ["skills", "list", "--json"]:
            return FakeProc(out=json.dumps({"skills": []}))
        if len(argv) > 2 and argv[1:3] == ["skills", "verify"]:
            return FakeProc(self.verify_code, json.dumps(self.verify_payload), "verification error")
        if len(argv) > 2 and argv[1:3] == ["skills", "install"]:
            if self.install_code == 0:
                ws = Path(kwargs["cwd"])
                skill = ws / "skills" / "playwright-mcp"
                skill.mkdir(parents=True, exist_ok=True)
                (skill / "SKILL.md").write_text("---\nname: playwright-mcp\n---\n", encoding="utf-8")
            return FakeProc(self.install_code, "installed\n", "install error")
        raise AssertionError(f"unexpected argv: {argv!r}")


def bridge(runner=None, env=None):
    return OpenClawBridge(
        catalog_path=CATALOG,
        security_review_path=REVIEW,
        executable="/fake/openclaw",
        runner=runner or FakeRunner(),
        env_source={} if env is None else env,
    )


def test_live_security_review_only_allows_playwright_candidate():
    b = bridge()
    plan = b.plan("playwright-mcp")
    assert plan.install_ref == "@spiceman161/playwright-mcp"
    assert plan.version == "1.0.0"
    assert "--force" not in plan.install_argv
    assert "--force-install" not in plan.install_argv
    assert "--global" not in plan.install_argv

    for blocked in ("azhua-skill-vetter", "arc-trust-verifier", "agent-team-orchestration", "agentgate", "arc-skill-gitops"):
        with pytest.raises(OpenClawBridgeError):
            b.plan(blocked)


def test_unknown_and_injection_slugs_are_refused():
    b = bridge()
    for slug in ("unknown", "playwright-mcp;rm", "../playwright-mcp", "Playwright-MCP"):
        with pytest.raises(OpenClawBridgeError):
            b.plan(slug)


def test_probe_is_honest_when_openclaw_is_missing():
    b = OpenClawBridge(
        catalog_path=CATALOG,
        security_review_path=REVIEW,
        which=lambda _: None,
        runner=FakeRunner(),
        env_source={},
    )
    result = b.probe()
    assert result.available is False
    assert "not installed" in (result.reason or "")


def test_provider_and_bossman_secrets_are_not_forwarded():
    runner = FakeRunner()
    b = bridge(runner, {
        "PATH": "/bin",
        "HOME": "/home/owner",
        "OPENCLAW_HOME": "/home/owner/.openclaw",
        "OPENAI_API_KEY": "secret-openai",
        "ANTHROPIC_API_KEY": "secret-anthropic",
        "BOSSMAN_GATEWAY_CORE_KEY": "secret-bossman",
        "OPENCLAW_GATEWAY_URL": "https://remote.example.invalid",
    })
    b.probe()
    env = runner.calls[-1][1]["env"]
    assert env["PATH"] == "/bin"
    assert env["OPENCLAW_HOME"] == "/home/owner/.openclaw"
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "BOSSMAN_GATEWAY_CORE_KEY" not in env
    assert "OPENCLAW_GATEWAY_URL" not in env


def test_verify_requires_explicit_pass_object():
    with pytest.raises(OpenClawBridgeError):
        bridge(FakeRunner(verify_payload={"ok": False, "decision": "fail"})).verify("playwright-mcp")
    with pytest.raises(OpenClawBridgeError):
        bridge(FakeRunner(verify_payload={"ok": True, "decision": "suspicious"})).verify("playwright-mcp")
    with pytest.raises(OpenClawBridgeError):
        bridge(FakeRunner(verify_payload={"decision": "pass"})).verify("playwright-mcp")


def test_install_requires_owner_approval(tmp_path):
    with pytest.raises(OpenClawBridgeError, match="owner approval"):
        bridge().install("playwright-mcp", workspace=tmp_path, owner_approved=False)


def test_successful_install_is_pinned_and_receipted(tmp_path):
    runner = FakeRunner()
    b = bridge(runner)
    receipt = b.install("playwright-mcp", workspace=tmp_path, owner_approved=True)
    assert receipt.slug == "playwright-mcp"
    assert receipt.version == "1.0.0"
    assert receipt.owner_approved is True
    assert len(receipt.bundle_sha256) == 64
    assert len(receipt.verification_sha256) == 64
    stored = json.loads((tmp_path / ".bossman" / "openclaw-receipts" / "playwright-mcp.json").read_text())
    assert stored["bundle_sha256"] == receipt.bundle_sha256

    install_call = [call for call in runner.calls if call[0][1:3] == ["skills", "install"]][0][0]
    assert install_call == ["/fake/openclaw", "skills", "install", "@spiceman161/playwright-mcp", "--version", "1.0.0"]


def test_install_refuses_symlink_workspace(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(OpenClawBridgeError, match="symlink"):
        bridge().install("playwright-mcp", workspace=link, owner_approved=True)


def test_skill_fingerprint_is_deterministic_and_refuses_symlinks(tmp_path):
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text("hello", encoding="utf-8")
    first = fingerprint_skill_dir(root)
    second = fingerprint_skill_dir(root)
    assert first == second
    assert len(first) == 64

    target = root / "target.txt"
    target.write_text("x", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return
    with pytest.raises(OpenClawBridgeError, match="symlink"):
        fingerprint_skill_dir(root)


def test_sanitized_env_is_allowlist_not_denylist():
    env = sanitized_openclaw_env({"PATH": "x", "HOME": "y", "RANDOM_NEW_SECRET": "z"})
    assert env == {"PATH": "x", "HOME": "y"}
