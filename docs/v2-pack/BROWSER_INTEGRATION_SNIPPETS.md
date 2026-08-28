# INTEGRATION SNIPPETS

These are intentionally small so Claude can merge them into the current files instead of re-reading the whole repository.

## pyproject.toml

Add to dependencies:

```toml
"playwright>=1.48",
```

## db.py

Add the `browser_profiles` and `browser_sessions` tables from
`docs/CLAUDE_BROWSER_CONTROL_HANDOFF.md`.

## api.py imports

```python
from fastapi.responses import JSONResponse, Response
from .browser_control import (
    BrowserManager, BrowserPolicy, BrowserUnavailable,
    BrowserPolicyDenied, BrowserApprovalRequired, BrowserTakeoverActive,
)
```

## Services.__init__

```python
self.browser = BrowserManager(settings.data_dir / "browser")
```

## Services.stop

Before DB close:

```python
await self.browser.close()
```

## Pydantic request shapes

```python
class BrowserProfileIn(BaseModel):
    name: str
    agent_id: int | None = None
    policy: dict = Field(default_factory=dict)

class BrowserSessionIn(BaseModel):
    profile_id: int | None = None
    agent_id: int | None = None
    profile_name: str = "default"
    headless: bool = True

class BrowserNavigateIn(BaseModel):
    url: str
    actor: str = "agent"

class BrowserActionIn(BaseModel):
    action: str
    selector: str = ""
    value: str = ""
    actor: str = "agent"
```

## Screenshot response

Do not serialize PNG into JSON.

```python
@router.get("/browser/sessions/{session_id}/screenshot")
async def browser_screenshot(session_id: int, svc: Services = Depends(services)):
    png = await svc.browser.screenshot(session_id, actor="human")
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})
```

## Action dispatch

```python
if body.action == "click":
    return await svc.browser.click(session_id, body.selector, actor=body.actor)
if body.action == "type":
    return await svc.browser.type_text(session_id, body.selector, body.value, actor=body.actor)
if body.action == "select":
    return await svc.browser.select(session_id, body.selector, body.value, actor=body.actor)
```

Do not expose an untrusted `approved=True` body parameter to agents.
ASK must be bound to canonical Approval Queue.
