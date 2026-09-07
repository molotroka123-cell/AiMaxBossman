# OpenHands Integration Quick Start

## Architecture

```
Bossman (3.11) -> TeacherFallback -> OpenHandsClient -> Sidecar (3.12+)
                     |
              PathGuards (fail-closed)
```

## Security Guarantees

1. **allowed_paths** - OpenHands может писать только в разрешённые пути
2. **protected_paths** - Защищённые пути только для чтения
3. **fail-closed** - Любая попытка записи вне allowed_paths блокируется
4. **no auto-mission** - OpenHands не может завершить миссию самостоятельно
5. **no auto-deploy** - Нет автоматического push/deploy

## Usage

### Basic

```python
from bossman.apprentice.teacher_sandbox import TeacherSandbox

sandbox = TeacherSandbox(
    workspace_root="/path/to/workspace",
    allowed_paths=["bossman-core/bossman/apprentice/"],
    protected_paths=["bossman-core/bossman/config.py"],
    openhands_enabled=True
)

result = sandbox.execute("Generate hello world function")
print(result)
```

### With OpenHandsClient directly

```python
from bossman.apprentice.openhands_client import OpenHandsClient

client = OpenHandsClient(
    allowed_paths=["bossman-core/bossman/apprentice/"],
    protected_paths=["bossman-core/bossman/config.py"],
    workspace_root="/path/to/workspace"
)

result = client.execute_task(
    task_type="code_generation",
    spec="Create a function that adds two numbers"
)
```

## Running Tests

```bash
cd bossman-core
python -m pytest tests/apprentice/test_openhands_client.py -v
```

Expected output:
```
tests/apprentice/test_openhands_client.py::test_allowed_paths_enforcement PASSED
tests/apprentice/test_openhands_client.py::test_protected_paths_readonly PASSED
tests/apprentice/test_openhands_client.py::test_fail_closed_out_of_scope PASSED
tests/apprentice/test_openhands_client.py::test_no_auto_mission_completion PASSED

4 passed
```

## Live Run (requires credentials)

```bash
export OPENROUTER_API_KEY="your-key-here"
python scripts/openhands_sidecar.py --task "Generate hello world"
```

## Files

| File | Purpose |
|------|--------|
| `openhands_client.py` | Main OpenHands client |
| `teacher_sandbox.py` | Sandbox with OpenHands integration |
| `teacher_wiring_patch.py` | Wiring utilities |
| `test_openhands_client.py` | 4 hermetic security tests |
| `scripts/openhands_sidecar.py` | Sidecar runner |

## Status

- Wiring complete
- Tests passing (4/4)
- Live run (requires credentials)
