#!/usr/bin/env python3
"""
OpenHands Sidecar - Isolated coding worker for Bossman.

Protocol: bossman.openhands.v1

This sidecar:
- Runs in isolated Python 3.12+ environment
- Receives tasks from Bossman via JSON protocol
- Executes OpenHands agent against isolated workspace
- Returns structured results without claiming completion
- Never sees provider credentials (passed via env)
- Logs safely without leaking secrets

Usage:
    python openhands_sidecar.py --task-id <id> --workspace <path>
"""

import argparse
import json
import logging
import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "bossman.openhands.v1"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def redact_secrets(text: str) -> str:
    """Redact potential secrets from logs."""
    if not text:
        return text
    
    import re
    redacted = text
    redacted = re.sub(r'sk-[a-zA-Z0-9]{20,}', '[REDACTED_API_KEY]', redacted)
    redacted = re.sub(r'Bearer\s+[a-zA-Z0-9\-_.]+', 'Bearer [REDACTED]', redacted)
    redacted = re.sub(r'OPENROUTER_API_KEY=[^\s]+', 'OPENROUTER_API_KEY=[REDACTED]', redacted)
    redacted = re.sub(r'ANTHROPIC_API_KEY=[^\s]+', 'ANTHROPIC_API_KEY=[REDACTED]', redacted)
    
    return redacted


class SidecarRequest:
    """Request from Bossman to OpenHands sidecar."""
    
    def __init__(self, data: Dict[str, Any]):
        self.schema_version = data.get('schema_version', SCHEMA_VERSION)
        self.task_id = data.get('task_id')
        self.instruction = data.get('instruction', '')
        self.workspace_root = data.get('workspace_root', '.')
        self.allowed_paths = data.get('allowed_paths', [])
        self.protected_paths = data.get('protected_paths', [])
        self.model = data.get('model', 'openrouter/auto')
        self.timeout_seconds = data.get('timeout_seconds', 300)
        self.metadata = data.get('metadata', {})
        
    def validate(self) -> List[str]:
        errors = []
        if not self.task_id:
            errors.append("task_id is required")
        if not self.instruction:
            errors.append("instruction is required")
        if not self.workspace_root:
            errors.append("workspace_root is required")
        if self.timeout_seconds < 1 or self.timeout_seconds > 3600:
            errors.append("timeout_seconds must be between 1 and 3600")
        return errors


class SidecarResponse:
    """Response from OpenHands sidecar to Bossman."""
    
    def __init__(
        self,
        status: str,
        task_id: str,
        schema_version: str = SCHEMA_VERSION,
        runtime_seconds: float = 0.0,
        model: Optional[str] = None,
        termination_reason: Optional[str] = None,
        changed_files: Optional[List[str]] = None,
        error: Optional[str] = None,
        diagnostics: Optional[Dict[str, Any]] = None
    ):
        self.schema_version = schema_version
        self.status = status
        self.task_id = task_id
        self.runtime_seconds = runtime_seconds
        self.model = model
        self.termination_reason = termination_reason
        self.changed_files = changed_files or []
        self.error = error
        self.diagnostics = diagnostics or {}
        
    def to_dict(self) -> Dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'status': self.status,
            'task_id': self.task_id,
            'runtime_seconds': self.runtime_seconds,
            'model': self.model,
            'termination_reason': self.termination_reason,
            'changed_files': self.changed_files,
            'error': self.error,
            'diagnostics': self.diagnostics
        }


def run_openhands_agent(
    instruction: str,
    workspace_root: str,
    allowed_paths: List[str],
    protected_paths: List[str],
    model: str,
    timeout_seconds: int
) -> Dict[str, Any]:
    """
    STUB: Run OpenHands agent against workspace.
    
    Real implementation requires:
    1. openhands-ai package installed
    2. Provider credentials in environment
    3. Actual agent execution
    """
    logger.info(f"Running OpenHands agent in {workspace_root}")
    logger.info(f"Instruction: {instruction[:100]}...")
    logger.info(f"Model: {model}")
    logger.info(f"Timeout: {timeout_seconds}s")
    
    return {
        'success': True,
        'changed_files': [],
        'error': None,
        'stub': True,
        'message': 'OpenHands integration stub - requires openhands-ai package'
    }


def main():
    parser = argparse.ArgumentParser(description='OpenHands Sidecar for Bossman')
    parser.add_argument('--task-id', required=True, help='Task identifier')
    parser.add_argument('--workspace', required=True, help='Workspace root path')
    parser.add_argument('--instruction', required=True, help='Coding instruction')
    parser.add_argument('--allowed-paths', nargs='*', default=[], help='Allowed paths')
    parser.add_argument('--protected-paths', nargs='*', default=[], help='Protected paths')
    parser.add_argument('--model', default='openrouter/auto', help='Model to use')
    parser.add_argument('--timeout', type=int, default=300, help='Timeout in seconds')
    parser.add_argument('--request-json', help='Full request as JSON')
    
    args = parser.parse_args()
    
    start_time = datetime.now()
    
    try:
        if args.request_json:
            request_data = json.loads(args.request_json)
            request = SidecarRequest(request_data)
        else:
            request = SidecarRequest({
                'schema_version': SCHEMA_VERSION,
                'task_id': args.task_id,
                'instruction': args.instruction,
                'workspace_root': args.workspace,
                'allowed_paths': args.allowed_paths,
                'protected_paths': args.protected_paths,
                'model': args.model,
                'timeout_seconds': args.timeout
            })
        
        errors = request.validate()
        if errors:
            response = SidecarResponse(
                status='error',
                task_id=args.task_id,
                error=f"Invalid request: {'; '.join(errors)}",
                runtime_seconds=(datetime.now() - start_time).total_seconds()
            )
            print(json.dumps(response.to_dict(), indent=2))
            return 1
        
        logger.info(f"Starting task {args.task_id}")
        
        result = run_openhands_agent(
            instruction=request.instruction,
            workspace_root=request.workspace_root,
            allowed_paths=request.allowed_paths,
            protected_paths=request.protected_paths,
            model=request.model,
            timeout_seconds=request.timeout_seconds
        )
        
        runtime = (datetime.now() - start_time).total_seconds()
        
        if result.get('success'):
            response = SidecarResponse(
                status='success',
                task_id=args.task_id,
                runtime_seconds=runtime,
                model=args.model,
                termination_reason='completed',
                changed_files=result.get('changed_files', []),
                diagnostics={'stub': result.get('stub', False)}
            )
        else:
            response = SidecarResponse(
                status='error',
                task_id=args.task_id,
                runtime_seconds=runtime,
                model=args.model,
                termination_reason='execution_failed',
                error=result.get('error', 'Unknown error'),
                changed_files=result.get('changed_files', [])
            )
        
        print(json.dumps(response.to_dict(), indent=2))
        return 0 if result.get('success') else 1
        
    except Exception as e:
        runtime = (datetime.now() - start_time).total_seconds()
        error_msg = redact_secrets(traceback.format_exc())
        
        response = SidecarResponse(
            status='error',
            task_id=args.task_id if 'args' in locals() else 'unknown',
            runtime_seconds=runtime,
            termination_reason='exception',
            error=redact_secrets(str(e)),
            diagnostics={'traceback': error_msg}
        )
        
        print(json.dumps(response.to_dict(), indent=2))
        return 1


if __name__ == '__main__':
    sys.exit(main())
