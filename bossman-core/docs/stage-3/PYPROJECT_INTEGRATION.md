# pyproject integration

Current Bossman Core already depends on FastAPI, uvicorn, httpx, PyYAML and Pydantic, so Stage 3 does not require another web framework.

Recommended additions:

```toml
[project.optional-dependencies]
resource = ["psutil>=5.9"]

[project.scripts]
bossman-gateway = "bossman.gateway.main:main"
```

Do not blindly replace the existing `[project.scripts]`; merge the entry.

`psutil` is optional in the implementation. Without it the gateway remains functional and simply exposes fewer process/system memory metrics.
