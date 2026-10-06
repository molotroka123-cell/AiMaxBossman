"""authored_by_lane (opsplug): bossman.sandbox.toolbox - what a sandbox may run inside itself."""
from pathlib import Path

import pytest

from bossman import errors
from bossman.sandbox import toolbox
from bossman.sandbox.models import SandboxSession, SandboxSpec


class _Runtime:
    def __init__(self, root: Path):
        self.root = root

    def workdir(self, session):
        return self.root


@pytest.fixture
def box(tmp_path):
    work = tmp_path / "ws" / "work"
    work.mkdir(parents=True)
    return SandboxSession(id="sbx_t", spec=SandboxSpec(task="t")), _Runtime(work), work


def test_shell_must_be_an_argv_array_of_known_executables():
    assert toolbox.shell_argv(["python", "-c", "print(1)"]) == ("python", "-c", "print(1)")
    assert toolbox.shell_argv(["/usr/bin/env", "ls"])[0] == "/usr/bin/env"
    for bad in ("rm -rf /", [], ["curl", "http://x"], ["powershell", "-c", "x"]):
        with pytest.raises(errors.PolicyDenied):
            toolbox.shell_argv(bad)


def test_sh_dash_c_is_refused_as_injection_vector():
    with pytest.raises(errors.PolicyDenied, match="injection"):
        toolbox.shell_argv(["bash", "-c", "echo hi; rm x"])
    assert toolbox.shell_argv(["bash", "script.sh"]) == ("bash", "script.sh")


def test_git_publish_and_remote_config_are_never_available():
    for sub in sorted(toolbox.GIT_FORBIDDEN):
        with pytest.raises(errors.PolicyDenied, match="owner"):
            toolbox.git_argv([sub])
    with pytest.raises(errors.PolicyDenied):
        toolbox.git_argv(["gc"])                       # unknown = not in the allowed subset
    with pytest.raises(errors.PolicyDenied):
        toolbox.git_argv("status")                     # a string is an injection vector
    assert toolbox.git_argv(["status", "-s"]) == ("git", "status", "-s")


def test_contained_paths_stay_inside_the_workspace(box):
    session, rt, work = box
    (work / "a.txt").write_text("x", encoding="utf-8")
    assert toolbox.contained(session, rt, "a.txt") == (work / "a.txt").resolve()
    for escape in ("../outside.txt", "sub/../../outside.txt", str((work.parent / "x").resolve())):
        with pytest.raises(errors.PolicyDenied):
            toolbox.contained(session, rt, escape)


def test_symlink_escape_is_blocked(box, tmp_path):
    session, rt, work = box
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (work / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privilege on this host")
    with pytest.raises(errors.PolicyDenied):
        toolbox.contained(session, rt, "link/secret.txt")


def test_unknown_workdir_is_an_error_and_browser_profile_is_separate(box):
    session, rt, work = box
    assert toolbox.browser_profile_dir(session, rt) == work.parent / "browser-profile"
    with pytest.raises(errors.BossmanError):
        toolbox.contained(session, object(), "a.txt")
