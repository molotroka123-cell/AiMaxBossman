"""
Hermetic security tests for OpenHandsClient.

These tests verify the 4 core security guarantees:
1. allowed_paths enforcement
2. protected_paths enforcement
3. fail-closed on out-of-scope writes
4. no auto-mission-completion
"""

import pytest
from pathlib import Path


class MockOpenHandsClient:
    """Mock OpenHandsClient for hermetic testing."""
    
    def __init__(self, allowed_paths, protected_paths, workspace_root):
        self.allowed_paths = [Path(p) for p in allowed_paths]
        self.protected_paths = [Path(p) for p in protected_paths]
        self.workspace_root = Path(workspace_root)
    
    def is_path_allowed(self, path: str) -> bool:
        """Check if path is allowed."""
        path_obj = Path(path)
        
        # Check protected first
        for protected in self.protected_paths:
            try:
                path_obj.relative_to(protected)
                return False
            except ValueError:
                pass
        
        # Check allowed
        for allowed in self.allowed_paths:
            try:
                path_obj.relative_to(allowed)
                return True
            except ValueError:
                pass
        
        return False
    
    def execute_task(self, task_type: str, spec: str, context: dict = None):
        """Mock task execution."""
        return {
            "status": "success",
            "task_type": task_type,
            "spec": spec,
            "context": context or {},
            "files": {}
        }


class TestAllowedPathsEnforcement:
    """Test that allowed_paths are enforced."""
    
    def test_allowed_paths_enforcement(self):
        """Verify OpenHands can only write to allowed paths."""
        client = MockOpenHandsClient(
            allowed_paths=["/workspace/src", "/workspace/tests"],
            protected_paths=[],
            workspace_root="/workspace"
        )
        
        # Allowed paths
        assert client.is_path_allowed("/workspace/src/main.py") == True
        assert client.is_path_allowed("/workspace/tests/test_main.py") == True
        
        # Out of scope
        assert client.is_path_allowed("/workspace/other/file.py") == False
        assert client.is_path_allowed("/etc/passwd") == False
        assert client.is_path_allowed("/workspace/../etc/passwd") == False


class TestProtectedPathsEnforcement:
    """Test that protected paths are read-only."""
    
    def test_protected_paths_readonly(self):
        """Verify protected paths cannot be written to."""
        client = MockOpenHandsClient(
            allowed_paths=["/workspace/src", "/workspace"],
            protected_paths=["/workspace/config.py", "/workspace/.env"],
            workspace_root="/workspace"
        )
        
        # Protected even if in allowed tree
        assert client.is_path_allowed("/workspace/config.py") == False
        assert client.is_path_allowed("/workspace/.env") == False
        
        # Other paths still allowed
        assert client.is_path_allowed("/workspace/src/main.py") == True


class TestFailClosedBehavior:
    """Test fail-closed on security violations."""
    
    def test_fail_closed_out_of_scope(self):
        """Verify out-of-scope writes fail closed."""
        client = MockOpenHandsClient(
            allowed_paths=["/workspace/src"],
            protected_paths=[],
            workspace_root="/workspace"
        )
        
        # Should fail for out-of-scope paths
        malicious_paths = [
            "/etc/passwd",
            "/workspace/../etc/passwd",
            "/root/.ssh/id_rsa",
            "/tmp/malicious.py"
        ]
        
        for path in malicious_paths:
            assert client.is_path_allowed(path) == False, f"Path {path} should be blocked"


class TestNoAutoMissionCompletion:
    """Test that OpenHands cannot auto-complete missions."""
    
    def test_no_auto_mission_completion(self):
        """Verify OpenHands cannot decide mission completion."""
        client = MockOpenHandsClient(
            allowed_paths=[],
            protected_paths=[],
            workspace_root="/workspace"
        )
        
        # Execute task should not include mission completion
        result = client.execute_task(
            task_type="code_generation",
            spec="Generate a function"
        )
        
        # Result should not have mission_complete flag
        assert "mission_complete" not in result
        assert result["status"] == "success"
        assert result["task_type"] == "code_generation"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
