# Integration snippets against current Command Center

These snippets are intentionally not full-file replacements.

## Services additions

```python
from .v2.browser_control import BrowserManager
from .v2.skill_library import SkillLibrary, default_skill_roots
from .v2.terminal_control import TerminalManager

# Services.__init__
self.browser = BrowserManager(settings.data_dir / "browser")
self.terminal = TerminalManager()
repo_root = settings.ui_dir.parent.parent  # adjust to actual repo-root resolver
skill_roots = default_skill_roots(repo_root)
self.skills = SkillLibrary(skill_roots, repo_root / ".agents" / "skills")

# Services.stop
await self.browser.close()
```

Browser must be optional: missing Playwright must not break Command Center startup.

## Dependencies to add after validation

```toml
"playwright>=1.48",
```

For MCP, prefer an optional extra until the exact official SDK version is verified:

```toml
mcp = ["mcp>=1.0"]
```

Do not make MCP or Playwright import failures fatal at startup.

## New DB entities recommended

Centralize them in one migration owner:

- provider_catalog_models
- model_capability_checks
- missions
- mission_kpis
- mission_kpi_events
- resource_reservations
- governor_interventions
- evaluations
- session_forks
- browser_profiles
- browser_sessions
- terminal_sessions
- skills
- skill_versions
- mcp_servers
- mcp_tools
- opencode_sessions

## Browser API

```text
GET/POST/PATCH/DELETE /api/browser/profiles
GET/POST              /api/browser/sessions
GET                    /api/browser/sessions/{id}
POST                   /api/browser/sessions/{id}/start
POST                   /api/browser/sessions/{id}/navigate
POST                   /api/browser/sessions/{id}/action
GET                    /api/browser/sessions/{id}/snapshot
GET                    /api/browser/sessions/{id}/screenshot
POST                   /api/browser/sessions/{id}/takeover
POST                   /api/browser/sessions/{id}/pause
POST                   /api/browser/sessions/{id}/resume
POST                   /api/browser/sessions/{id}/stop
```

## Terminal API

```text
GET  /api/terminal/sessions
POST /api/terminal/sessions
GET  /api/terminal/sessions/{id}
POST /api/terminal/sessions/{id}/stdin
POST /api/terminal/sessions/{id}/kill
```

Command requests must carry enough information for the trusted layer to resolve permissions.
Never accept `approved=true` from an untrusted model request.

## Skills API

```text
GET  /api/skills
POST /api/skills/import
POST /api/skills
GET  /api/skills/{id}
POST /api/skills/{id}/propose-update
POST /api/skills/{id}/apply-update
```

## OpenRouter API

```text
POST /api/providers/{id}/sync-catalog
GET  /api/providers/{id}/catalog
POST /api/providers/{id}/catalog/{remote_id}/pin
POST /api/models/{id}/verify-capabilities
```

## MCP API

```text
GET/POST/PATCH/DELETE /api/mcp/servers
POST /api/mcp/servers/{id}/check
GET  /api/mcp/servers/{id}/tools
POST /api/mcp/servers/{id}/refresh
```

## OpenCode API

```text
POST /api/opencode/check
POST /api/opencode/sessions
POST /api/opencode/sessions/{id}/abort
POST /api/opencode/sessions/{id}/fork
GET  /api/opencode/sessions/{id}/diff
```

The exact session creation/send-message endpoint must be read from the currently installed OpenCode OpenAPI spec,
not guessed from stale documentation.
