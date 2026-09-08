"""
Tests for IsolatedWorktree.

These tests verify:
- Worktree creation from source repo
- Clean isolated state
- Evidence derivation
- Proper cleanup
"""

import pytest
import subprocess
import tempfile
from pathlib import Path


class TestIsolatedWorktree:
    """Test isolated worktree functionality."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary Git repository for testing."""
        temp_dir = tempfile.mkdtemp(prefix='test_repo_')
        repo_path = Path(temp_dir) / 'repo'
        repo_path.mkdir()

        subprocess.run(['git', 'init'], cwd=str(repo_path), check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=str(repo_path), check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=str(repo_path), check=True, capture_output=True)

        test_file = repo_path / 'test.txt'
        test_file.write_text('initial content\n')
        subprocess.run(['git', 'add', '.'], cwd=str(repo_path), check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'Initial commit'], cwd=str(repo_path), check=True, capture_output=True)

        yield repo_path

        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_worktree_creation(self, temp_repo):
        """Test that worktree can be created."""
        from bossman.apprentice.isolated_worktree import IsolatedWorktree

        with IsolatedWorktree(str(temp_repo)) as worktree:
            assert worktree.root is not None
            assert worktree.root.exists()
            assert worktree.root != temp_repo

            test_file = worktree.root / 'test.txt'
            assert test_file.exists()
            assert test_file.read_text() == 'initial content\n'

    def test_worktree_isolation(self, temp_repo):
        """Test that worktree is isolated from source."""
        from bossman.apprentice.isolated_worktree import IsolatedWorktree

        with IsolatedWorktree(str(temp_repo)) as worktree:
            test_file = worktree.root / 'test.txt'
            test_file.write_text('modified in worktree\n')

            source_file = temp_repo / 'test.txt'
            assert source_file.read_text() == 'initial content\n'

    def test_evidence_derivation(self, temp_repo):
        """Test that evidence is correctly derived."""
        from bossman.apprentice.isolated_worktree import IsolatedWorktree

        with IsolatedWorktree(str(temp_repo)) as worktree:
            test_file = worktree.root / 'test.txt'
            test_file.write_text('modified content\n')

            new_file = worktree.root / 'new.txt'
            new_file.write_text('new file\n')

            evidence = worktree.derive_evidence()

            assert 'test.txt' in evidence['modified_files']
            assert 'new.txt' in evidence['untracked_files']
            assert evidence['head_before'] is not None

    def test_cleanup(self, temp_repo):
        """Test that worktree is cleaned up."""
        from bossman.apprentice.isolated_worktree import IsolatedWorktree

        worktree = IsolatedWorktree(str(temp_repo))
        worktree.create()
        root_path = worktree.root

        assert root_path.exists()

        worktree.cleanup()

        assert not root_path.exists()

    def test_keep_after_flag(self, temp_repo):
        """Test that keep_after prevents cleanup."""
        from bossman.apprentice.isolated_worktree import IsolatedWorktree

        worktree = IsolatedWorktree(str(temp_repo), keep_after=True)
        worktree.create()
        root_path = worktree.root

        worktree.cleanup()

        assert root_path.exists()

        import shutil
        shutil.rmtree(str(root_path.parent), ignore_errors=True)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
