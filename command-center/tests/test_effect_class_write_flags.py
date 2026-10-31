"""Security audit 31.10 (swarm Kimi + GLM, integrated by Claude): write flags of read-looking tools are writes."""
import pytest

from bcc.approval_scope import effect_class


@pytest.mark.parametrize("cmd", ["ruff check --add-noqa .", "mypy --install-types", "mypy --install-types --non-interactive",
                                 "tree -o out.txt", "tree -L 2 -o tree.txt", "tree --output tree.txt",
                                 'git grep -O"touch pwned" x', "pytest --basetemp=out"])
def test_write_capable_flags_are_not_read(cmd):
    assert effect_class("terminal.run", {"command": cmd}) != "read"


@pytest.mark.parametrize("cmd", ["tree", "tree -L 2", "mypy src", "flake8 .", "ls -la", "git status", "rg foo", "git grep foo"])
def test_plain_readers_stay_read(cmd):
    assert effect_class("terminal.run", {"command": cmd}) == "read"
