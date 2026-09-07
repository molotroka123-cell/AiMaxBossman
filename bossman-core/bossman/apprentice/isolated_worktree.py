"""
Isolated Git Worktree Manager for OpenHands execution.

This module:
- Creates disposable Git worktrees for OpenHands tasks
- Ensures clean isolated state
- Records pre-run baseline
- Derives post-run evidence
- Cleans up worktree after completion

Security properties:
- OpenHands never edits owner's primary checkout
- Pre-existing changes don't contaminate evidence
- Worktree is destroyed after task (unless retention policy requires otherwise)
"""

import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class IsolatedWorktree:
    """
    Manages an isolated Git worktree for OpenHands execution.
    
    Usage:
        with IsolatedWorktree('/path/to/repo') as worktree:
            # worktree.root is the isolated checkout
            # Run OpenHands here
            # Derive evidence
        # worktree is automatically cleaned up
    """
    
    def __init__(
        self,
        source_repo: str,
        base_branch: Optional[str] = None,
        keep_after: bool = False
    ):
        self.source_repo = Path(source_repo).resolve()
        self.base_branch = base_branch or self._get_default_branch()
        self.keep_after = keep_after
        self.root: Optional[Path] = None
        self.pre_run_state: Dict[str, Any] = {}
        self.post_run_state: Dict[str, Any] = {}
        
    def _get_default_branch(self) -> str:
        try:
            result = subprocess.run(
                ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD'],
                cwd=str(self.source_repo),
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip().replace('refs/remotes/origin/', '')
        except Exception as e:
            logger.warning(f"Failed to get default branch: {e}")
        
        return 'main'
    
    def create(self) -> Path:
        logger.info(f"Creating isolated worktree from {self.source_repo}@{self.base_branch}")
        
        temp_dir = tempfile.mkdtemp(prefix='openhands_worktree_')
        worktree_path = Path(temp_dir) / 'worktree'
        
        try:
            cmd = [
                'git', 'worktree', 'add',
                str(worktree_path),
                '-b', f'openhands_task_{datetime.now().strftime("%Y%m%d_%H%M%S")}',
                f'origin/{self.base_branch}'
            ]
            
            result = subprocess.run(
                cmd,
                cwd=str(self.source_repo),
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0:
                cmd = ['git', 'worktree', 'add', str(worktree_path), self.base_branch]
                result = subprocess.run(
                    cmd,
                    cwd=str(self.source_repo),
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if result.returncode != 0:
                    raise RuntimeError(f"Failed to create worktree: {result.stderr}")
            
            self.root = worktree_path
            logger.info(f"Worktree created at {worktree_path}")
            
            self.pre_run_state = self._record_state()
            
            return worktree_path
            
        except Exception as e:
            logger.error(f"Failed to create worktree: {e}")
            if worktree_path.exists():
                self._cleanup(worktree_path)
            raise
    
    def _record_state(self) -> Dict[str, Any]:
        if not self.root:
            return {}
        
        state = {
            'timestamp': datetime.now().isoformat(),
            'head': None,
            'status': [],
            'files': {}
        }
        
        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                state['head'] = result.stdout.strip()
            
            result = subprocess.run(
                ['git', 'status', '--porcelain'],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                state['status'] = result.stdout.strip().split('\n')
            
        except Exception as e:
            logger.warning(f"Failed to record state: {e}")
        
        return state
    
    def derive_evidence(self) -> Dict[str, Any]:
        if not self.root:
            return {'error': 'No worktree'}
        
        evidence = {
            'modified_files': [],
            'added_files': [],
            'deleted_files': [],
            'patches': {},
            'head_before': self.pre_run_state.get('head'),
            'head_after': None,
            'untracked_files': []
        }
        
        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                evidence['head_after'] = result.stdout.strip()
            
            result = subprocess.run(
                ['git', 'diff', '--name-status', 'HEAD'],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    if not line:
                        continue
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        status = parts[0]
                        file_path = parts[1]
                        
                        if status == 'M' or status.startswith('M'):
                            evidence['modified_files'].append(file_path)
                        elif status == 'A' or status.startswith('A'):
                            evidence['added_files'].append(file_path)
                        elif status == 'D' or status.startswith('D'):
                            evidence['deleted_files'].append(file_path)
            
            result = subprocess.run(
                ['git', 'ls-files', '--others', '--exclude-standard'],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                evidence['untracked_files'] = result.stdout.strip().split('\n')
            
            for file_path in evidence['modified_files']:
                result = subprocess.run(
                    ['git', 'diff', 'HEAD', '--', file_path],
                    cwd=str(self.root),
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0:
                    evidence['patches'][file_path] = result.stdout
            
        except Exception as e:
            logger.error(f"Failed to derive evidence: {e}")
            evidence['error'] = str(e)
        
        return evidence
    
    def _cleanup(self, path: Path):
        try:
            if path.exists():
                shutil.rmtree(str(path), ignore_errors=True)
                logger.info(f"Cleaned up worktree at {path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup {path}: {e}")
    
    def cleanup(self):
        if self.root and not self.keep_after:
            try:
                subprocess.run(
                    ['git', 'worktree', 'remove', '-f', str(self.root)],
                    cwd=str(self.source_repo),
                    capture_output=True,
                    timeout=30
                )
            except Exception:
                pass
            
            self._cleanup(self.root)
            self.root = None
    
    def __enter__(self) -> 'IsolatedWorktree':
        self.create()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()


__all__ = ['IsolatedWorktree']
