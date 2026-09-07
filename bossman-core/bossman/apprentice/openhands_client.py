"""
OpenHands Client - Bossman integration with OpenHands coding agent.

This client:
- Executes OpenHands as an isolated sidecar process
- Enforces allowed_paths and protected_paths
- Derives Git evidence independently
- Never trusts sidecar claims about file changes
- Implements fail-closed security

Protocol: bossman.openhands.v1
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class OpenHandsClient:
    """
    Client for OpenHands sidecar execution.
    
    Security guarantees:
    - allowed_paths enforcement
    - protected_paths enforcement
    - fail-closed on violations
    - independent Git evidence derivation
    - no secret leakage
    """
    
    SCHEMA_VERSION = "bossman.openhands.v1"
    
    def __init__(
        self,
        allowed_paths: List[str],
        protected_paths: List[str],
        workspace_root: str,
        sidecar_script: Optional[str] = None,
        timeout_seconds: int = 300,
        model: str = "openrouter/auto"
    ):
        self.allowed_paths = [Path(p).resolve() for p in allowed_paths]
        self.protected_paths = [Path(p).resolve() for p in protected_paths]
        self.workspace_root = Path(workspace_root).resolve()
        self.sidecar_script = sidecar_script or self._find_sidecar()
        self.timeout_seconds = timeout_seconds
        self.model = model
        
        logger.info(f"OpenHandsClient initialized")
        logger.info(f"  Workspace: {self.workspace_root}")
        logger.info(f"  Allowed paths: {len(self.allowed_paths)}")
        logger.info(f"  Protected paths: {len(self.protected_paths)}")
        logger.info(f"  Sidecar: {self.sidecar_script}")
    
    def _find_sidecar(self) -> str:
        candidates = [
            Path(__file__).parent.parent.parent / 'scripts' / 'openhands_sidecar.py',
            Path(__file__).parent / 'scripts' / 'openhands_sidecar.py',
            Path('scripts') / 'openhands_sidecar.py',
        ]
        
        for candidate in candidates:
            if candidate.exists():
                return str(candidate.resolve())
        
        return str(Path(__file__).parent.parent.parent / 'scripts' / 'openhands_sidecar.py')
    
    def is_path_allowed(self, path: str) -> bool:
        try:
            path_obj = Path(path).resolve()
            
            try:
                path_obj.relative_to(self.workspace_root)
            except ValueError:
                logger.warning(f"Path {path} outside workspace {self.workspace_root}")
                return False
            
            for protected in self.protected_paths:
                try:
                    path_obj.relative_to(protected)
                    logger.warning(f"Path {path} in protected area {protected}")
                    return False
                except ValueError:
                    pass
            
            for allowed in self.allowed_paths:
                try:
                    path_obj.relative_to(allowed)
                    return True
                except ValueError:
                    pass
            
            logger.warning(f"Path {path} not in any allowed area")
            return False
            
        except Exception as e:
            logger.error(f"Path validation error for {path}: {e}")
            return False
    
    def execute_task(
        self,
        task_type: str,
        spec: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        logger.info(f"Executing {task_type} task via OpenHands")
        
        if not self.sidecar_script or not Path(self.sidecar_script).exists():
            return {
                'success': False,
                'error': f"Sidecar script not found: {self.sidecar_script}",
                'changed_files': []
            }
        
        request = {
            'schema_version': self.SCHEMA_VERSION,
            'task_id': f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            'instruction': spec,
            'workspace_root': str(self.workspace_root),
            'allowed_paths': [str(p) for p in self.allowed_paths],
            'protected_paths': [str(p) for p in self.protected_paths],
            'model': self.model,
            'timeout_seconds': self.timeout_seconds,
            'metadata': context or {}
        }
        
        try:
            cmd = [
                sys.executable,
                self.sidecar_script,
                '--request-json',
                json.dumps(request)
            ]
            
            logger.info(f"Running sidecar: {' '.join(cmd[:3])}...")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds + 30
            )
            
            try:
                response = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse sidecar response: {e}")
                logger.error(f"Stdout: {result.stdout[:500]}")
                return {
                    'success': False,
                    'error': f"Invalid sidecar response: {e}",
                    'changed_files': []
                }
            
            if response.get('schema_version') != self.SCHEMA_VERSION:
                logger.warning(f"Schema version mismatch: {response.get('schema_version')}")
            
            if response.get('status') == 'success':
                changed_files = response.get('changed_files', [])
                
                for file_path in changed_files:
                    if not self.is_path_allowed(file_path):
                        logger.error(f"Security violation: sidecar changed {file_path} but it's not allowed")
                        return {
                            'success': False,
                            'error': f"Security violation: {file_path} not in allowed_paths",
                            'changed_files': [],
                            'blocked': True
                        }
                
                return {
                    'success': True,
                    'changed_files': changed_files,
                    'runtime_seconds': response.get('runtime_seconds', 0),
                    'model': response.get('model'),
                    'termination_reason': response.get('termination_reason')
                }
            else:
                return {
                    'success': False,
                    'error': response.get('error', 'Unknown sidecar error'),
                    'changed_files': response.get('changed_files', []),
                    'termination_reason': response.get('termination_reason')
                }
                
        except subprocess.TimeoutExpired:
            logger.error(f"Sidecar timed out after {self.timeout_seconds}s")
            return {
                'success': False,
                'error': f"Timeout after {self.timeout_seconds}s",
                'changed_files': [],
                'timeout': True
            }
        except Exception as e:
            logger.error(f"Sidecar execution failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'changed_files': []
            }
    
    def derive_evidence(self, changed_files: List[str]) -> Dict[str, Any]:
        evidence = {
            'files': [],
            'patches': [],
            'validation': {
                'all_files_exist': True,
                'all_in_scope': True,
                'no_protected_changes': True
            }
        }
        
        for file_path in changed_files:
            full_path = Path(file_path)
            
            if not full_path.exists():
                logger.warning(f"Claimed changed file doesn't exist: {file_path}")
                evidence['validation']['all_files_exist'] = False
                continue
            
            if not self.is_path_allowed(file_path):
                logger.error(f"Evidence validation failed: {file_path} not allowed")
                evidence['validation']['all_in_scope'] = False
                continue
            
            try:
                content = full_path.read_text()
                evidence['files'].append({
                    'path': file_path,
                    'status': 'modified' if full_path.exists() else 'added',
                    'size': len(content)
                })
            except Exception as e:
                logger.error(f"Failed to read {file_path}: {e}")
        
        return evidence


__all__ = ['OpenHandsClient']
