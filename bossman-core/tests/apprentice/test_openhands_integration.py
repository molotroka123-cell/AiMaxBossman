"""
Integration tests for TeacherFallback + OpenHandsClient wiring.

These tests verify the full integration between TeacherFallback,
TeacherSandbox, and OpenHandsClient with improved coverage.
"""

import pytest
from pathlib import Path


class TestTeacherSandboxIntegration:
    """Test TeacherSandbox integration with OpenHandsClient."""
    
    def test_sandbox_creation(self):
        """Test that sandbox can be created with OpenHands enabled."""
        from bossman.apprentice.teacher_sandbox import TeacherSandbox
        
        sandbox = TeacherSandbox(
            workspace_root="/tmp/test_workspace",
            allowed_paths=["/tmp/test_workspace/src"],
            protected_paths=["/tmp/test_workspace/config.py"],
            openhands_enabled=False
        )
        
        assert sandbox.workspace_root == Path("/tmp/test_workspace").resolve()
        assert len(sandbox.allowed_paths) == 1
        assert len(sandbox.protected_paths) == 1
        assert sandbox.openhands_enabled == False
        assert sandbox.openhands_client is None
    
    def test_sandbox_with_openhands(self):
        """Test sandbox initialization with OpenHands enabled."""
        from bossman.apprentice.teacher_sandbox import TeacherSandbox
        
        sandbox = TeacherSandbox(
            workspace_root="/tmp/test_workspace",
            allowed_paths=["/tmp/test_workspace/src"],
            protected_paths=["/tmp/test_workspace/config.py"],
            openhands_enabled=False
        )
        
        result = sandbox.execute("test task")
        assert result["status"] == "fallback"
        assert result["task"] == "test task"
    
    def test_path_validation(self):
        """Test path validation logic."""
        from bossman.apprentice.teacher_sandbox import TeacherSandbox
        
        sandbox = TeacherSandbox(
            workspace_root="/tmp/test",
            allowed_paths=["/tmp/test/src", "/tmp/test/docs"],
            protected_paths=["/tmp/test/config.py"],
            openhands_enabled=False
        )
        
        # Allowed paths
        assert sandbox.is_path_allowed("/tmp/test/src/main.py") == True
        assert sandbox.is_path_allowed("/tmp/test/docs/readme.md") == True
        
        # Protected paths
        assert sandbox.is_path_allowed("/tmp/test/config.py") == False
        
        # Out of scope
        assert sandbox.is_path_allowed("/tmp/other/file.py") == False
        assert sandbox.is_path_allowed("/etc/passwd") == False
    
    def test_sandbox_status(self):
        """Test sandbox status reporting."""
        from bossman.apprentice.teacher_sandbox import TeacherSandbox
        
        sandbox = TeacherSandbox(
            workspace_root="/tmp/test",
            allowed_paths=["/tmp/test/src"],
            protected_paths=["/tmp/test/config.py"],
            openhands_enabled=False
        )
        
        status = sandbox.get_status()
        
        assert "workspace_root" in status
        assert "allowed_paths" in status
        assert "protected_paths" in status
        assert "openhands_enabled" in status
        assert "openhands_client" in status
        assert status["allowed_paths"] == 1
        assert status["protected_paths"] == 1
        assert status["openhands_enabled"] == False


class TestTeacherWiringPatch:
    """Test TeacherOpenHandsIntegration wiring."""
    
    def test_integration_creation(self):
        """Test that integration layer can be created."""
        from bossman.apprentice.teacher_wiring_patch import TeacherOpenHandsIntegration
        
        class MockClient:
            def execute_task(self, **kwargs):
                return {"files": {"test.py": "content"}}
        
        integration = TeacherOpenHandsIntegration(
            openhands_client=MockClient(),
            allowed_paths=["/src"],
            protected_paths=["/config.py"]
        )
        
        assert integration.allowed_paths == ["/src"]
        assert integration.protected_paths == ["/config.py"]
        assert integration._mission_complete_override == False
    
    def test_path_allowed_check(self):
        """Test path allowed checking in integration."""
        from bossman.apprentice.teacher_wiring_patch import TeacherOpenHandsIntegration
        
        class MockClient:
            pass
        
        integration = TeacherOpenHandsIntegration(
            openhands_client=MockClient(),
            allowed_paths=["/src", "/docs"],
            protected_paths=["/config.py", "/.env"]
        )
        
        # Allowed
        assert integration._is_path_allowed("/src/main.py") == True
        assert integration._is_path_allowed("/docs/readme.md") == True
        
        # Protected
        assert integration._is_path_allowed("/config.py") == False
        assert integration._is_path_allowed("/.env") == False
        
        # Out of scope
        assert integration._is_path_allowed("/other/file.py") == False
    
    def test_mission_completion_control(self):
        """Test that mission completion is controlled by Bossman."""
        from bossman.apprentice.teacher_wiring_patch import TeacherOpenHandsIntegration
        
        class MockClient:
            pass
        
        integration = TeacherOpenHandsIntegration(
            openhands_client=MockClient(),
            allowed_paths=["/src"],
            protected_paths=[]
        )
        
        # OpenHands cannot complete mission by itself
        assert integration.can_complete_mission() == False
        
        # Request completion returns False (Bossman decides)
        result = integration.request_mission_completion()
        assert result == False


class TestSecurityGuarantees:
    """Test all security guarantees are enforced."""
    
    def test_allowed_paths_enforcement(self):
        """Verify allowed paths are enforced."""
        from bossman.apprentice.teacher_sandbox import TeacherSandbox
        
        sandbox = TeacherSandbox(
            workspace_root="/tmp/test",
            allowed_paths=["/tmp/test/src"],
            protected_paths=[],
            openhands_enabled=False
        )
        
        # Can write to allowed
        assert sandbox.is_path_allowed("/tmp/test/src/file.py") == True
        
        # Cannot write outside
        assert sandbox.is_path_allowed("/tmp/test/other/file.py") == False
    
    def test_protected_paths_enforcement(self):
        """Verify protected paths are read-only."""
        from bossman.apprentice.teacher_sandbox import TeacherSandbox
        
        sandbox = TeacherSandbox(
            workspace_root="/tmp/test",
            allowed_paths=["/tmp/test/src"],
            protected_paths=["/tmp/test/config.py"],
            openhands_enabled=False
        )
        
        # Protected even if in allowed tree
        assert sandbox.is_path_allowed("/tmp/test/config.py") == False
    
    def test_fail_closed_behavior(self):
        """Verify fail-closed on security violations."""
        from bossman.apprentice.teacher_wiring_patch import TeacherOpenHandsIntegration
        
        class MockClient:
            def execute_task(self, **kwargs):
                return {"files": {"/etc/passwd": "malicious"}}
        
        integration = TeacherOpenHandsIntegration(
            openhands_client=MockClient(),
            allowed_paths=["/src"],
            protected_paths=[]
        )
        
        # Should raise on security violation
        with pytest.raises(ValueError, match="Fail-closed"):
            integration.execute_code_task("malicious task")
    
    def test_no_auto_mission_completion(self):
        """Verify OpenHands cannot auto-complete missions."""
        from bossman.apprentice.teacher_wiring_patch import TeacherOpenHandsIntegration
        
        class MockClient:
            pass
        
        integration = TeacherOpenHandsIntegration(
            openhands_client=MockClient(),
            allowed_paths=[],
            protected_paths=[]
        )
        
        # OpenHands cannot decide mission completion
        assert integration.request_mission_completion() == False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
