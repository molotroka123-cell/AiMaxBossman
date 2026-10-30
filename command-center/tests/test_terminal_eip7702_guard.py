"""Binance hot-wallet drain 29.10.2026 (EIP-7702 delegation signed with a stolen key): terminal.run refuses such work as T3."""
import pytest

from bcc.features.tools_terminal import hard_deny_reason


@pytest.mark.parametrize("cmd", [
    "node sign.js --eip7702 --delegate 0xabc",
    "python make_tx.py --authorization_list auth.json",
    "cast send --auth 0x1 # EIP-7702 setCode",
    "node build_setCodeTx.js",
    "python recover.py --mnemonic words.txt",
    "npm run drainer",
    "python sweeper.py --to 0xdead",
])
def test_wallet_delegation_and_drain_commands_are_hard_denied(cmd):
    assert hard_deny_reason(cmd)


@pytest.mark.parametrize("cmd", ["python -m pytest -q", "git status", "python analyze.py orders.csv",
                                 "python report.py --authorization-header-test"])
def test_ordinary_commands_are_not_affected(cmd):
    assert not hard_deny_reason(cmd)


@pytest.mark.parametrize("cmd", [
    "python dump_private_key.py", "node wallet_export.js", "python read_seed_phrase.py",
    "(rm -rf /)", "rm -rf /*", "rm -fr \\", "rm -rf / && echo ok",
    r"type %USERPROFILE%\.aws\credentials", r"type C:\Users\asd\.ssh\id_ed25519",
    "cmd /c python dump_private_key.py", 'powershell -c "python dump_private_key.py"',
])
def test_fuzz_found_bypasses_are_denied(cmd):
    """Mutation fuzz 30.10 (10.3M variants): these got past the hard-deny layer before the fix."""
    assert hard_deny_reason(cmd)


@pytest.mark.parametrize("cmd", ["rm -rf build/", "rm -rf ./dist", "python wallets_of_birds_report.py --dry-run",
                                 "python keyboard.py", "type README.md"])
def test_fuzz_fix_does_not_overblock(cmd):
    assert not hard_deny_reason(cmd)


@pytest.mark.parametrize("cmd", [
    "cat C:/Users/asd/AppData/Local/Bossman/CommandCenter/token", r"type %LOCALAPPDATA%\Bossman\CommandCenter\bcc.db",
    r"type C:\Users\asd\.bossman-mobile\tokens.json", "sqlite3 bcc.db .dump",
    "git reset --har HEAD~3", "git push origin +main", "git push origin :main", "git push origin --delete main",
    "git branch -D main", "git clean -ffdx",
    "rd /s /q %USERPROFILE%", "rmdir /s /q C:/Users/asd", r"del /f /s /q C:\Users\asd\*", "Remove-Item -Recurse -Force $HOME",
    "rm -rf C:/Users/asd", "rm -rf /c/Users/asd",
])
def test_security_audit_hard_deny_gaps_are_closed(cmd):
    """Security audit 30.10 (OSS-Scanner-style, reproducers): each of these returned no hard-deny before."""
    assert hard_deny_reason(cmd)


@pytest.mark.parametrize("cmd", ["git push origin main", "git reset --soft HEAD~1", "git branch -d feature",
                                 "git clean -n", r"del /q build\*.tmp", "rm -rf ./build", "Remove-Item -Recurse .\dist",
                                 "python report.py --db sales.db"])
def test_security_audit_fix_does_not_overblock(cmd):
    assert not hard_deny_reason(cmd)


@pytest.mark.parametrize("cmd", ["ls & rm -rf build", "ls <(rm -rf build)", "cat x >(tee y)"])
def test_single_ampersand_and_process_substitution_are_writes(cmd):
    from bcc.approval_scope import effect_class
    assert effect_class("terminal.run", {"command": cmd}) == "write"


@pytest.mark.parametrize("cmd", ["ls 2>&1", "git status &> log.txt", "dir"])
def test_redirection_ampersand_is_not_a_command_separator(cmd):
    from bcc.features.action_contract import _looks_like_mutation
    assert _looks_like_mutation(cmd) == ("log.txt" in cmd)
