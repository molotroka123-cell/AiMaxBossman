"""
Example usage of OpenHandsClient integration.

This example shows how to use TeacherSandbox and OpenHandsClient
for safe code generation with security guarantees.
"""

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def example_basic_usage():
    """Basic usage example with TeacherSandbox."""
    logger.info("=== Basic Usage Example ===")
    
    from bossman.apprentice.teacher_sandbox import TeacherSandbox
    
    sandbox = TeacherSandbox(
        workspace_root="/path/to/workspace",
        allowed_paths=[
            "bossman-core/bossman/apprentice/",
            "bossman-core/tests/apprentice/",
        ],
        protected_paths=[
            "bossman-core/bossman/config.py",
            "bossman-core/.env.example",
        ],
        openhands_enabled=False
    )
    
    result = sandbox.execute(
        task="Generate a function that adds two numbers",
        context={"language": "python"}
    )
    
    logger.info(f"Result: {result}")
    return result


def example_advanced_usage():
    """Advanced usage with direct OpenHandsClient."""
    logger.info("=== Advanced Usage Example ===")
    
    from bossman.apprentice.openhands_client import OpenHandsClient
    
    client = OpenHandsClient(
        allowed_paths=[
            "bossman-core/bossman/apprentice/",
            "bossman-core/tests/apprentice/",
        ],
        protected_paths=[
            "bossman-core/bossman/config.py",
        ],
        workspace_root="/path/to/workspace"
    )
    
    result = client.execute_task(
        task_type="code_generation",
        spec="Create a REST API endpoint with FastAPI",
        context={
            "language": "python",
            "framework": "fastapi",
            "endpoint": "/api/v1/items"
        }
    )
    
    if 'files' in result:
        for file_path in result['files'].keys():
            if not client.is_path_allowed(file_path):
                logger.error(f"Security violation: {file_path}")
                raise ValueError(f"Fail-closed: {file_path}")
    
    logger.info(f"Generated files: {list(result.get('files', {}).keys())}")
    return result


def example_wiring_integration():
    """Example using TeacherOpenHandsIntegration."""
    logger.info("=== Wiring Integration Example ===")
    
    from bossman.apprentice.teacher_wiring_patch import TeacherOpenHandsIntegration
    from bossman.apprentice.openhands_client import OpenHandsClient
    
    client = OpenHandsClient(
        allowed_paths=["bossman-core/bossman/apprentice/"],
        protected_paths=["bossman-core/bossman/config.py"],
        workspace_root="/path/to/workspace"
    )
    
    integration = TeacherOpenHandsIntegration(
        openhands_client=client,
        allowed_paths=["bossman-core/bossman/apprentice/"],
        protected_paths=["bossman-core/bossman/config.py"]
    )
    
    result = integration.execute_code_task(
        spec="Generate a utility function",
        context={"type": "utility"}
    )
    
    logger.info(f"Integration result: {result}")
    return result


def example_security_validation():
    """Example demonstrating security validation."""
    logger.info("=== Security Validation Example ===")
    
    from bossman.apprentice.openhands_client import OpenHandsClient
    
    client = OpenHandsClient(
        allowed_paths=["src/", "tests/"],
        protected_paths=["config.py", ".env"],
        workspace_root="/workspace"
    )
    
    test_paths = [
        ("src/main.py", True),
        ("tests/test_main.py", True),
        ("config.py", False),
        (".env", False),
        ("../etc/passwd", False),
        ("/etc/passwd", False),
    ]
    
    for path, expected in test_paths:
        result = client.is_path_allowed(path)
        status = "✓" if result == expected else "✗"
        logger.info(f"{status} {path}: {result} (expected {expected})")
        assert result == expected, f"Path validation failed for {path}"
    
    logger.info("All security validations passed!")


def main():
    """Run all examples."""
    logger.info("OpenHands Integration Examples")
    logger.info("=" * 50)
    
    try:
        example_basic_usage()
    except Exception as e:
        logger.error(f"Basic example failed: {e}")
    
    try:
        example_advanced_usage()
    except Exception as e:
        logger.error(f"Advanced example failed: {e}")
    
    try:
        example_wiring_integration()
    except Exception as e:
        logger.error(f"Wiring example failed: {e}")
    
    try:
        example_security_validation()
    except Exception as e:
        logger.error(f"Security example failed: {e}")
    
    logger.info("=" * 50)
    logger.info("Examples complete!")


if __name__ == "__main__":
    main()
