"""Independent release attacks on modes and the final patch byte boundary."""
from __future__ import annotations

import os
import sys

import pytest

from bossman.apprentice import openhands_client as oc
from bossman.apprentice.openhands_teacher_client import OpenHandsTeacherClient
from test_openhands_evidence_independence import repo, run, git, sidecar  # noqa: F401


@pytest.mark.skipif(os.name == "nt", reason="Windows does not expose POSIX executable mode")
@pytest.mark.parametrize("git_tracks_modes", [True, False])
def test_protected_mode_change_is_not_an_empty_changeset(repo, git_tracks_modes):
    git(repo, "config", "core.filemode", str(git_tracks_modes).lower())
    with pytest.raises(oc.OpenHandsError, match="protected.txt"):
        run(repo, "pathlib.Path('protected.txt').chmod(0o755)")


@pytest.mark.skipif(os.name == "nt", reason="Windows does not expose POSIX executable mode")
def test_allowed_mode_change_is_in_the_evidence(repo):
    result = run(repo, "pathlib.Path('allowed/keep.txt').chmod(0o755)")
    assert result.changed_files == ("allowed/keep.txt",)
    assert "old mode" in result.diff and "new mode" in result.diff


@pytest.mark.skipif(os.name == "nt", reason="Windows does not expose POSIX permission bits")
def test_non_git_protected_permissions_are_still_observed(repo):
    with pytest.raises(oc.OpenHandsError, match='protected.txt'):
        run(repo, "pathlib.Path('protected.txt').chmod(0o600)")


def test_changed_bytes_during_diff_are_refused(repo, monkeypatch):
    original = oc._evidence_diff

    def change_after_reading(workspace, untracked=()):
        result = original(workspace, untracked)
        target = workspace / "allowed/keep.txt"
        stat = target.stat()
        target.write_text("replaced!\n")  # equal size to the recorded edit
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return result

    monkeypatch.setattr(oc, "_evidence_diff", change_after_reading)
    with pytest.raises(oc.OpenHandsError, match="changed.*evidence|evidence.*changed"):
        run(repo, "pathlib.Path('allowed/keep.txt').write_text('recorded!\\n')")


def test_teacher_does_not_reread_a_replaced_path_after_validation(tmp_path):
    class ReplaceAfterValidated(oc.OpenHandsClient):
        def run(self, request):
            result = super().run(request)
            target = request.workspace / "src/a.py"
            before = target.stat()
            target.write_text("VALUE = 9\n")
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
            return result

    client = ReplaceAfterValidated(sidecar("pathlib.Path('src/a.py').write_bytes(b'VALUE = 2\\n')"))
    result = OpenHandsTeacherClient(client).run({"files": {"src/a.py": "VALUE = 1\n"},
        "allowed_paths": ["src"], "acceptance_tests": []})
    assert result["patch"] == {"src/a.py": "VALUE = 2\n"}


def test_teacher_patch_is_the_exact_bytes_the_teacher_wrote(tmp_path):
    """CRLF written by the teacher stays CRLF in the patch: the patch is bound to the
    observed bytes, not to a text-mode re-reading (Windows text mode would hide this)."""
    client = oc.OpenHandsClient(sidecar("pathlib.Path('src/a.py').write_bytes(b'VALUE = 2\\r\\n')"))
    result = OpenHandsTeacherClient(client).run({"files": {"src/a.py": "VALUE = 1\n"},
        "allowed_paths": ["src"], "acceptance_tests": []})
    assert result["patch"] == {"src/a.py": "VALUE = 2\r\n"}


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires Windows privileges")
def test_teacher_never_reads_an_outside_symlink_as_candidate_source(tmp_path):
    outside = tmp_path / "owner-private.txt"
    outside.write_text("controlled private fixture")
    body = ("pathlib.Path('src/a.py').unlink(); "
            f"os.symlink({str(outside)!r},'src/a.py')")
    client = oc.OpenHandsClient(sidecar(body))
    with pytest.raises(oc.OpenHandsError, match="non-file|symlink|regular"):
        OpenHandsTeacherClient(client).run({"files": {"src/a.py": "VALUE = 1\n"},
            "allowed_paths": ["src"], "acceptance_tests": []})


def test_captured_evidence_is_immutable_and_binary_diff_remains_available(repo):
    result = run(repo, "pathlib.Path('allowed/binary.dat').write_bytes(b'\\x00\\xffnew')")
    assert result.files['allowed/binary.dat'].data == b'\x00\xffnew'
    assert 'GIT binary patch' in result.diff
    with pytest.raises(TypeError):
        result.files['allowed/binary.dat'] = oc.FileEvidence('100644', b'replaced')


def test_evidence_read_is_bounded_before_accepting_output(repo, monkeypatch):
    monkeypatch.setattr(oc, '_MAX_FILE_BYTES', 128)
    with pytest.raises(oc.OpenHandsError, match='bounded read'):
        run(repo, "pathlib.Path('allowed/large.txt').write_bytes(b'x' * 129)")


def test_replaced_sidecar_script_is_refused_before_dispatch(repo):
    script = repo.parent / (repo.name + '-sidecar.py')
    original = "import json,sys; json.load(sys.stdin); print(json.dumps({'schema':'bossman.openhands.v1','status':'completed'}))\n"
    script.write_text(original, encoding='utf-8')
    client = oc.OpenHandsClient([sys.executable, str(script)])
    before = script.stat()
    script.write_text(original.replace("import json,sys", "import sys,json"), encoding='utf-8')
    os.utime(script, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(oc.OpenHandsError, match='sidecar.*identity'):
        client.run(oc.OpenHandsRequest('inspect', repo, ('allowed',)))


def test_unchanged_sidecar_script_still_runs(repo):
    script = repo.parent / (repo.name + '-sidecar.py')
    script.write_text("import json,sys; json.load(sys.stdin); print(json.dumps({'schema':'bossman.openhands.v1','status':'completed'}))\n", encoding='utf-8')
    result = oc.OpenHandsClient([sys.executable, str(script)]).run(
        oc.OpenHandsRequest('inspect', repo, ('allowed',)))
    assert result.status == 'completed' and not result.changed_files
