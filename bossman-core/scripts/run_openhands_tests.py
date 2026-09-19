#!/usr/bin/env python3
"""
Run 4 hermetic security tests for OpenHandsClient.

Tests:
1. allowed_paths enforcement
2. protected_paths enforcement
3. fail-closed on out-of-scope writes
4. no auto-mission-completion
"""

import subprocess
import sys
import os

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    bossman_core = os.path.join(root_dir, 'bossman-core')
    
    os.chdir(bossman_core)
    
    print(f"Running OpenHandsClient hermetic tests from {bossman_core}")
    print("=" * 60)
    
    test_file = "tests/apprentice/test_openhands_client.py"
    
    cmd = [
        sys.executable, "-m", "pytest",
        test_file,
        "-v",
        "--tb=short"
    ]
    
    print(f"Command: {' '.join(cmd)}")
    print("=" * 60)
    
    result = subprocess.run(cmd, capture_output=False)
    
    print("=" * 60)
    if result.returncode == 0:
        print("✅ All 4 hermetic security tests PASSED")
        return 0
    else:
        print("❌ Some tests FAILED")
        return result.returncode

if __name__ == "__main__":
    sys.exit(main())
