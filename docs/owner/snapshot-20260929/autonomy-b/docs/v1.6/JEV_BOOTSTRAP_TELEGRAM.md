# Bossman 1.6 — Jev Bootstrap + Telegram Control

Status: foundation contract + deterministic bootstrap planner.

Code:
- `command-center/bcc/features/game_bootstrap_v16.py`
- `command-center/tests/test_game_bootstrap_v16.py`

## Answer to owner

Yes: for BossBlocks, Bossman is expected to discover/download/unpack/verify the
required game tooling itself **through the existing Bossman CMD/UX tool paths**.

The owner should not need to manually install Godot.

Exceptions that can still require Telegram owner action:
- Windows UAC/admin operation that cannot be avoided;
- a new license/EULA requiring owner acceptance;
- login/account/CAPTCHA;
- paid purchase;
- action outside the already delegated game-project scope.

For the current first-game path we deliberately avoid most of these by using
portable ZIP artifacts.

## Pinned fast path for BossBlocks

Current verified source snapshot:
- Godot official stable: 4.7.2.
- Voxel Tools release: v1.7.
- Voxel Tools v1.7 release name states it is a custom Godot 4.7.2 stable build.
- Windows editor asset:
  `godot.windows.editor.x86_64.exe.zip`
  SHA-256 `e2292adb0b671508ab220fce5bcd052b1c20674a3385d89c6b38601e6e1f956b`.
- Windows export template:
  `godot.windows.template_release.x86_64.exe.zip`
  SHA-256 `86fe371ed80948ccb4b4ac78a0e62e2917bc257890bdaa62b4f6e7aa402f0775`.

Source:
`https://github.com/Zylann/godot_voxel/releases/tag/v1.7`.

Fallback source:
`https://github.com/godotengine/godot/releases/tag/4.7.2-stable`.

Never trust a filename alone. Compare actual downloaded bytes with the pinned
release digest.

## Jev bootstrap state machine

```
DISCOVER
 -> if verified compatible tool already exists: REUSE
 -> else DOWNLOAD pinned official/release artifact
 -> VERIFY_HASH
 -> safe UNPACK into mission-scoped tools/cache
 -> VERSION_SMOKE
 -> tiny-project launch
 -> EXPORT_SMOKE
 -> READY
```

If primary Voxel Tools route is not READY inside 15 minutes:

```
FALLBACK
 -> official Godot 4.7.2 stable
 -> pure-Godot bounded voxel/chunk implementation
```

Do not spend the 4-hour benchmark compiling C++ infrastructure indefinitely.

## Existing Bossman components to reuse

Do not create another downloader/approval system.

Reuse:
- 1.5 Capability Factory;
- `browser.download` and its false-success protection;
- governed terminal/file paths;
- artifact hash verification;
- Telegram Companion;
- Jev bridge;
- existing approvals / STOP / PAUSE / RESUME;
- task/evidence identities.

The new bootstrap feature is only a deterministic **plan provider**. It never
claims that a download happened.

## Telegram owner-control loop

### Start

Recommended owner flow:

```
/task BOSSBLOCKS-001: execute docs/v1.6/FIRST_GAME_4H_MASTER_PROMPT.md for 4 hours through Bossman only
/confirm <code>
```

Once that single delegated mission is confirmed, ordinary local project
downloads/edits/tests inside its granted scope should proceed without repeatedly
asking the owner.

### During run

Owner controls everything from Telegram:

- `/menu` — control panel;
- `/status` — backend/PC status;
- `/queue` — tasks;
- `/screen` — spot-check screen;
- `/approvals` — only exceptional actions waiting for owner;
- `/pause` — stop taking new work;
- `/resume` — owner resumes;
- `/stop` — emergency stop;
- `/jev <request>` — ask Jev to choose one existing safe Telegram action.

Jev itself can never press approve/confirm/resume.

## Notifications: signal, not spam

Bossman should proactively notify the owner only for:

1. BOOTSTRAP_STARTED;
2. DOWNLOAD_VERIFIED;
3. FALLBACK_SELECTED;
4. 30-minute milestone summary;
5. ASTER_AT_RISK / BLOCKED / FALSE_PASS_RISK;
6. APPROVAL_REQUIRED;
7. MILESTONE_GREEN;
8. FINAL_ACCEPT / FINAL_REJECT.

Do not send a Telegram message for every model call, file edit or passing unit
test.

A 30-minute update should fit roughly:

```
BOSSBLOCKS 01:30/04:00
GREEN: movement, collision, terrain, break/place
RED: persistence restart
JEV: free/local coder -> GLM only if second repair fails
ASTER: save-state verification missing one negative control
COST: $0 incremental
OWNER ACTION: none
```

## Download truth

A successful HTTP response is not success.

Download PASS requires:
- expected URL/source;
- actual bytes saved under approved root;
- content is not HTML/error body;
- expected archive/file type;
- digest;
- safe extraction;
- expected executable/files exist;
- version probe;
- launch smoke.

For executable tooling, activation requires the smoke check.

## Avoid installer dead ends

Preferred:
1. reuse already verified tool;
2. portable official/pinned release;
3. user-scope package manager if deterministic;
4. admin installer only if necessary and owner approves;
5. source build only if no binary path exists and time budget justifies it.

Do not disable Smart App Control/security controls.

If Windows blocks an unsigned binary, report exact blocker and use an approved
alternative/fallback. Never turn off host protection merely to win the benchmark.

## Learning

Bootstrap failures are valuable training data.

Examples:
- wrong version selection;
- downloaded HTML instead of ZIP;
- digest mismatch;
- archive traversal;
- executable blocked;
- export template mismatch;
- plugin consumes too much benchmark time.

Aster proposes a generalized lesson, verifier confirms it, then Skill Compiler
can produce `game.bootstrap.godot_voxel` for the next game.

Next similar run should reuse verified pinned artifacts/cache/skill and avoid the
research/download work when hashes and policy are still current.
