"""
Teacher Sandbox with OpenHandsClient integration.

Provides isolated execution environment for TeacherFallback with:
- Workspace isolation
- Path guards (allowed_paths, protected_paths)
- Fail-closed security model
- OpenHands sidecar integration
"""

import logging
import os
from pathlib import Path
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


class TeacherSandbox:
    """
    Sandbox for TeacherFallback execution with OpenHands integration.
    
    Security model:
    - Isolated workspace
    - Path-based access control
    - Fail-closed on violations
    - No automatic deployment
    """
    
    def __init__(
        self,
        workspace_root: str,
        allowed_paths: List[str],
        protected_paths: List[str],
        openhands_enabled: bool = True
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.allowed_paths = [Path(p) for p in allowed_paths]
        self.protected_paths = [Path(p) for p in protected_paths]
        self.openhands_enabled = openhands_enabled
        self.openhands_client = None
        
        if openhands_enabled:
            self._init_openhands()
    
    def _init_openhands(self):
        """Initialize OpenHandsClient if enabled."""
        try:
            from bossman.apprentice.openhands_client import OpenHandsClient
            
            self.openhands_client = OpenHandsClient(
                allowed_paths=[str(p) for p in self.allowed_paths],
                protected_paths=[str(p) for p in self.protected_paths],
                workspace_root=str(self.workspace_root)
            )
            logger.info(f"OpenHandsClient initialized in {self.workspace_root}")
        except ImportError as e:
            logger.warning(f"OpenHandsClient not available: {e}")
            self.openhands_client = None
    
    def execute(self, task: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute a task in the sandbox."""
        logger.info(f"Sandbox executing: {task[:100]}...")
        
        if self.openhands_client and self.openhands_enabled:
            return self._execute_with_openhands(task, context)
        else:
            return self._execute_fallback(task, context)
    
    def _execute_with_openhands(self, task: str, context: Optional[Dict]) -> Dict[str, Any]:
        """Execute task using OpenHandsClient."""
        try:
            result = self.openhands_client.execute_task(
                task_type="code_generation",
                spec=task,
                context=context or {}
            )
            
            if 'files' in result:
                for file_path in result['files'].keys():
                    if not self._validate_path(file_path):
                        raise ValueError(f"Security violation: {file_path}")
            
            return result
        except Exception as e:
            logger.error(f"OpenHands execution failed: {e}")
            return self._execute_fallback(task, context)
    
    def _execute_fallback(self, task: str, context: Optional[Dict]) -> Dict[str, Any]:
        """Fallback execution without OpenHands."""
        logger.info("Using fallback execution")
        return {
            "status": "fallback",
            "task": task,
            "context": context
        }
    
    def _validate_path(self, path: str) -> bool:
        """Validate path is allowed and not protected."""
        path_obj = Path(path)
        
        for protected in self.protected_paths:
            try:
                path_obj.relative_to(protected)
                return False
            except ValueError:
                pass
        
        for allowed in self.allowed_paths:
            try:
                path_obj.relative_to(allowed)
                return True
            except ValueError:
                pass
        
        return False
    
    def is_path_allowed(self, path: str) -> bool:
        """Public API to check if path is allowed."""
        return self._validate_path(path)
    
    def get_status(self) -> Dict[str, Any]:
        """Get sandbox status."""
        return {
            "workspace_root": str(self.workspace_root),
            "allowed_paths": len(self.allowed_paths),
            "protected_paths": len(self.protected_paths),
            "openhands_enabled": self.openhands_enabled,
            "openhands_client": self.openhands_client is not None
        }
