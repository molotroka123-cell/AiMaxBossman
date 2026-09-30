# CLAUDE HANDOFF — Browser & Computer Control
## READ THIS FIRST TO SAVE TOKENS

This handoff was prepared against `molotroka123-cell/AiMaxBossman`,
branch `claude/bossman-control-v03-43igbk`, before the 15-agent V2 pass.

**Do not re-design the browser architecture from zero.**
A tested policy/runtime skeleton is already supplied in:

- `command-center/bcc/browser_control.py`
- `command-center/tests/test_browser_control.py`
- `command-center/ui/browser-control.html`

The purpose of this note is to reduce Claude/Fable context burn.

---

## What is already implemented in the supplied code

### BrowserPolicy

Per-agent/session policy supports:

- enabled
- `dom_vision | dom_only | vision_only`
- allowed domains
- blocked domains
- per-action `auto | ask | deny`
- max tabs
- persistent profile
- screenshot policy
- max runtime

Default policy:

- navigate/read/screenshot/click/type/select = AUTO
- download/upload/submit/login = ASK
- purchase/payment/wallet/bank_transfer = DENY

Blocked domain has priority over allow list.

### BrowserManager

Playwright runtime implements:

- lazy Playwright startup
- isolated browser session
- optional persistent Chromium profile
- Navigate
- DOM snapshot
- screenshot
- click
- type
- select
- back
- reload
- Pause
- Resume
- Human Take Over
- Stop

When `takeover=true`, agent actions are rejected until Resume.

### DOM-first snapshot

The agent receives:

- URL
- title
- visible page text
- interactive elements
- labels/aria/name/type/placeholder/href

Do **not** force the vision model to inspect a screenshot for every step.
The intended routing is:

1. DOM snapshot first
2. execute deterministic Playwright action
3. screenshot/vision only for:
   - visual QA
   - canvas
   - ambiguous elements
   - layout/design
   - non-DOM interfaces

This is materially faster and uses fewer local inference tokens.

---

# Required integration work

## 1. Dependency

Add:

```toml
"playwright>=1.48",
```

to `command-center/pyproject.toml`.

Do not automatically download Chromium during ordinary app startup.

Installation command should be exposed in Setup/System UI:

```bash
playwright install chromium
```

The rest of Command Center must still boot if Playwright is not installed.

---

## 2. Database

Add two canonical tables to `command-center/bcc/db.py`.

```python
browser_profiles = sa.Table(
    "browser_profiles", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(120), nullable=False, unique=True),
    sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id", ondelete="SET NULL")),
    sa.Column("policy", sa.JSON, default=dict),
    sa.Column("created_at", sa.DateTime, default=utcnow),
    sa.Column("updated_at", sa.DateTime, default=utcnow),
)

browser_sessions = sa.Table(
    "browser_sessions", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("profile_id", sa.Integer, sa.ForeignKey("browser_profiles.id", ondelete="SET NULL")),
    sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id", ondelete="SET NULL")),
    sa.Column("status", sa.String(24), default="created"),
    sa.Column("current_url", sa.Text, default=""),
    sa.Column("takeover", sa.Boolean, default=False),
    sa.Column("paused", sa.Boolean, default=False),
    sa.Column("last_action", sa.Text, default=""),
    sa.Column("created_at", sa.DateTime, default=utcnow),
    sa.Column("updated_at", sa.DateTime, default=utcnow),
    sa.Column("finished_at", sa.DateTime),
)
```

For V2, move DB evolution to Alembic before 15 branches start editing schema.

---

## 3. Services

In Command Center `Services`:

```python
self.browser = BrowserManager(settings.data_dir / "browser")
```

During stop:

```python
await self.browser.close()
```

Browser unavailable must NOT make the rest of BOSSMAN unhealthy.

System health should show:

```json
{
  "browser": {
    "status": "ok|unavailable",
    "detail": "Playwright ready | install playwright/chromium"
  }
}
```

---

## 4. Required Browser API

Create:

```text
GET    /api/browser/profiles
POST   /api/browser/profiles
PATCH  /api/browser/profiles/{id}
DELETE /api/browser/profiles/{id}

GET    /api/browser/sessions
POST   /api/browser/sessions
GET    /api/browser/sessions/{id}
POST   /api/browser/sessions/{id}/start
POST   /api/browser/sessions/{id}/navigate
POST   /api/browser/sessions/{id}/action
GET    /api/browser/sessions/{id}/snapshot
GET    /api/browser/sessions/{id}/screenshot
POST   /api/browser/sessions/{id}/takeover
POST   /api/browser/sessions/{id}/pause
POST   /api/browser/sessions/{id}/resume
POST   /api/browser/sessions/{id}/stop
```

Every runtime change must emit an event:

```text
browser.session.started
browser.navigated
browser.action
browser.takeover
browser.paused
browser.resumed
browser.stopped
browser.error
```

---

## 5. Approval integration

Do NOT invent another approval system.

Reuse existing BOSSMAN approval architecture.

Agent-side policy:

```text
AUTO -> execute
ASK  -> existing Approval Queue -> execute only after approval
DENY -> reject
```

The agent must not be able to pass an `approved=true` flag to itself.

A trusted execution layer must bind the approved Approval ID to the exact:
- agent
- session
- action
- action arguments hash

High-risk defaults:

```text
browser.open/read_dom/screenshot/click/type -> AUTO
browser.download -> ASK
browser.upload -> ASK
browser.submit -> ASK
browser.login -> ASK
purchase/payment/wallet/bank transfer -> DENY
```

---

## 6. Unify with bossman-core instead of duplicating tools

Current repo already contains the stronger tool loop in:

`bossman-core/bossman/runner.py`

and permissioned tools in:

`bossman-core/bossman/toolkit/`

Command Center MVP currently has persistent queue/recovery but its new engine does not yet execute full tool calls.

Do not create a second permanent tool runtime.

Target V2 architecture:

```text
Command Center
      |
canonical Task/Run/Agent/Approval/Event
      |
Agent Runtime
      |
browser.* / fs.* / git.* / terminal.*
```

Port the proven `bossman-core` tool permission semantics into the canonical runtime.

---

## 7. Browser tool names

Add canonical permissions:

```text
browser.open
browser.read_dom
browser.screenshot
browser.click
browser.type
browser.select
browser.upload
browser.download
browser.submit
browser.login
browser.cookies

computer.screen
computer.mouse
computer.keyboard
```

Desktop `computer.*` is a separate phase.

Do not treat Playwright browser control and arbitrary OS desktop control as the same security boundary.

---

## 8. Dashboard UX

The supplied `browser-control.html` is a functional integration/debug screen,
not the final V2 design.

Integrate it into the existing SPA as a proper `Browser` page.

### Agent editor

Add Browser section:

```text
Browser access       ON/OFF
Mode                 DOM + Vision / DOM only / Vision only
Allowed sites        patterns
Blocked sites        patterns
Navigation           AUTO/ASK/DENY
Read DOM              AUTO/ASK/DENY
Click                 AUTO/ASK/DENY
Fill forms            AUTO/ASK/DENY
Upload                AUTO/ASK/DENY
Download              AUTO/ASK/DENY
Submit                AUTO/ASK/DENY
Login                 AUTO/ASK/DENY
Profile               temporary/persistent
Screenshots           never/errors/each step
Max tabs
Max runtime
Vision model
```

### Browser page

Show active sessions:

```text
Agent
Profile
Current URL
Current action
Duration
Status
```

Buttons:

```text
Live View
Take Over
Pause
Resume
Stop
```

Detail view:

```text
Screenshot
URL
DOM summary
Last action
Next intended action
Agent
Model
Event log
```

Mobile must expose Take Over / Pause / Resume / Stop in <= 2-3 taps.

---

## 9. Real human takeover semantics

When Take Over is pressed:

1. mark session takeover in canonical state
2. reject new agent browser actions
3. keep browser/session alive
4. user can inspect/interact
5. Resume clears takeover
6. agent re-snapshots DOM before continuing

Do not allow concurrent user + agent clicking.

---

## 10. Agent efficiency rule

To save local inference time:

```text
DOM -> deterministic action -> DOM
```

Use Vision only when the DOM cannot answer the question.

Never send full-page screenshots to the large coding model by default.

Prefer a smaller vision worker.

---

# Acceptance tests Claude must add

## Policy tests

- allow list
- block list precedence
- auto/ask/deny
- hard deny payment/wallet actions

Already supplied in `test_browser_control.py`.

## Runtime E2E

Use a local test webpage.

Scenario:

1. start browser
2. navigate local page
3. snapshot
4. find button/input
5. click
6. type
7. screenshot
8. Take Over
9. agent click must fail
10. Resume
11. agent click works
12. Stop

## Approval E2E

1. set submit=ASK
2. agent requests submit
3. Approval appears
4. no submit before approval
5. approve
6. exactly that pending action executes once
7. replaying approval cannot execute it twice

## Deny E2E

- payment action cannot execute even if agent requests it
- blocked domain navigation denied

## Browser crash

1. kill Chromium
2. session becomes degraded/interrupted
3. event emitted
4. self-healing policy may reopen from safe checkpoint
5. no duplicate form submission

---

# Do not spend tokens on these questions

Decisions already made:

- Playwright is the primary browser engine.
- DOM-first, Vision second.
- Existing Approval Queue is canonical.
- Browser and full desktop control are separate permission layers.
- Payment/wallet actions are DENY by default.
- Human Take Over blocks agent actions.
- Browser profiles are per-agent/session.
- The UI must configure everything without YAML.
- Browser runtime must not prevent BOSSMAN startup if unavailable.
- Use the supplied module rather than rewriting from scratch unless a failing test proves it inadequate.

---

# Claude TODO, in order

1. Copy supplied files into repo.
2. Add DB tables/migration.
3. Wire `BrowserManager` into Services.
4. Add Browser API.
5. Add approval binding for ASK.
6. Port browser permissions into canonical agent tool runtime.
7. Add SPA Browser page.
8. Add Agent Browser settings.
9. Add WS events.
10. Run policy tests.
11. Run Playwright local-page E2E.
12. Run Take Over E2E.
13. Run approval E2E.
14. Run mobile QA.
15. Update V2 acceptance matrix.
16. Commit only after all above passes.

Definition of Done:

> From BOSSMAN UI I can give an agent browser permission, define AUTO/ASK/DENY,
> launch a session, watch what it is doing, Take Over, Resume, and the agent can
> reliably navigate/read/click/type via Playwright while sensitive actions are
> approval-gated.
