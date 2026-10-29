import pytest

from bcc.features.pc_agent_policy_guards import is_immutable_audit_log_target


@pytest.mark.parametrize(
    "command",
    ("Remove-Item audit.jsonl", "del audit.jsonl", "ren audit.jsonl x.bak"),
)
def test_audit_log_mutation_is_guarded(command: str) -> None:
    assert is_immutable_audit_log_target(command)


@pytest.mark.parametrize("text", ("Get-Content audit.jsonl", "notes.txt"))
def test_audit_log_reads_and_other_files_are_allowed(text: str) -> None:
    assert not is_immutable_audit_log_target(text)
