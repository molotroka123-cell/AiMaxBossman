# File Commander Mini

Foundation for BOSSMAN standalone App.

## Run

```bash
python -m venv .venv
# activate environment
pip install -e ".[dev]"
file-commander-mini
```

Default: `http://127.0.0.1:8911`

## Current foundation status

Implemented:
- manifest
- standalone HTTP service
- health
- capabilities
- metrics
- job creation/list/status/cancel
- artifact endpoint
- future local-task emission adapter

Not implemented yet:
- real domain engine
- durable SQLite
- UI
- background workers
- real external integrations
- production auth
- Bossman-side Local Task Exchange consumer

See `TECHNICAL_SPEC.md`.
