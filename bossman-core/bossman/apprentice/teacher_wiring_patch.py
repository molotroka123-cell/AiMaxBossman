"""
TeacherFallback + OpenHandsClient wiring integration.

This module integrates OpenHandsClient with TeacherFallback while maintaining
security guards (allowed_paths, protected_paths, fail-closed).
"""

import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class TeacherOpenHandsIntegration:
    """
    Integration layer between TeacherFallback and OpenHandsClient.
    
    Security guarantees:
    - allowed_paths enforcement
    - protected_paths enforcement
    - fail-closed on violations
    - no auto-mission-completion from OpenHands
    - no automatic push/deploy
    """
    
    def __init__(
        self,
        openhands_client,
        allowed_paths: List[str],
        protected_paths: List[str]
    ):
        self.openhands_client = openhands_client
        self.allowed_paths = allowed_paths
        self.protected_paths = protected_paths
        self._mission_complete_override = False
    
    def execute_code_task(self, spec: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Execute a code generation task through OpenHandsClient.
        
        Args:
            spec: Task specification
            context: Optional context dictionary
        
        Returns:
            Result dictionary with generated files
        """
        logger.info(f"Executing code task via OpenHands: {spec[:100]}...")
        
        result = self.openhands_client.execute_task(
            task_type="code_generation",
            spec=spec,
            context=context or {}
        )
        
        # Security validation
        if 'files' in result:
            for file_path in result['files'].keys():
                if not self._is_path_allowed(file_path):
                    logger.error(f"Security violation: {file_path} not in allowed_paths")
                    raise ValueError(f"Fail-closed: {file_path}")
        
        return result
    
    def _is_path_allowed(self, path: str) -> bool:
        """Check if path is in allowed_paths and not in protected_paths."""
        for protected in self.protected_paths:
            if path.startswith(protected):
                return False
        
        for allowed in self.allowed_paths:
            if path.startswith(allowed):
                return True
        
        return False
    
    def can_complete_mission(self) -> bool:
        """OpenHands cannot decide mission completion - Bossman controls this."""
        return self._mission_complete_override
    
    def request_mission_completion(self) -> bool:
        """Request mission completion (Bossman decides)."""
        logger.info("Mission completion requested - Bossman will verify")
        return False
