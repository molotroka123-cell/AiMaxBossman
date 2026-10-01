# Telegram settings store: companion home vs BCC_DATA_DIR

Worktree: `C:\Users\asd\Bossman\wt-bugtest-0930` (package `command-center/bcc`). Read-only analysis, 2026-10-01.
All paths below are relative to `command-center/` unless absolute.

## 1. Verdict

The owner's audit is correct. The Telegram **settings API** (`bcc/features/telegram_settings.py`) is part of one Bossman
instance, but it reads and writes the *companion's own home*, which is a machine-global folder that does not depend on
`settings.data_dir` / `BCC_DATA_DIR`:

```
bcc/telegram_companion/__main__.py:19-21   default_config() = %LOCALAPPDATA%\Bossman\telegram-companion\config.json
bcc/features/telegram_settings.py:67-72    config_path() = $BOSSMAN_TELEGRAM_CONFIG or default_config()
```

`config_path().parent` ("home") then holds ALL of: `config.json` (people, models, core_url, core_data_dir),
`credentials.enc` + `secret.key` (bot token, core token; its OWN Fernet key, not data_dir/secret.key),
`companion.sqlite3` (state, inbox, history, learning_log, **profiles**, proposals, gates), `companion.env`, `poller.lock`,
`poller.json`, `exports/`, `cloud-budget.sqlite3`, logs and hand-made `config.json.bak-*` files.

So two Bossman instances with different `BCC_DATA_DIR` (owner :8800/8801, a test/bugtest copy, a release candidate) share
ONE Telegram config and ONE profile store. A settings save from the wrong instance rewrites the real owner's file.
Jeff (PIT) is the contrast: it is correctly rooted in `<data_dir>/pit-v1.7` (`bcc/pit/config.py:56-65`,
`bcc/features/jeff_settings.py:59-91`) and its owner-profile JSON files live in
`<data_dir>/pit-v1.7/owner-profiles/<person_key>.json` (`bcc/pit/participant_profile.py:34,56-61`).

Live machine evidence (non-secret shape only): `%LOCALAPPDATA%\Bossman\telegram-companion\config.json` has
`people[0] = {role: owner, agent_id: 1}`, `core_url = http://127.0.0.1:8801`, **no** `core_data_dir`, plus
`claude_bridge/codex_bridge/pc_control/jev_*` keys and a retired `bossman_launch` key; `%LOCALAPPDATA%\Bossman\CommandCenter`
(the owner data_dir) contains no `telegram-companion` folder at all.

## 2. Every place Telegram settings / profile / people / owner data is read or written

### 2a. Companion-home (the defective, machine-global store)

| Location | What | R/W |
|---|---|---|
| `bcc/telegram_companion/__main__.py:19-21` | `default_config()` -> LOCALAPPDATA path, no data_dir | path |
| `bcc/telegram_companion/__main__.py:133` | CLI `--config` default = `default_config()` | path |
| `bcc/telegram_companion/__main__.py:24-55` | `setup()` console: creates config.json + credentials.enc (O_EXCL, refuses if exists) | W (create-only, safe) |
| `bcc/telegram_companion/setup_ui.py:54,65,142` | browser setup: refuses if config or credentials exist, O_EXCL | W (create-only, safe) |
| `bcc/telegram_companion/__main__.py:91-105` | `serve()`: `Store(path.parent)`, `single_instance(path.parent)` | R/W sqlite |
| `bcc/telegram_companion/__main__.py:150-157` | `--unlock-delegation`: `Store(args.config.parent).put("delegation_locked", False)` | W |
| `bcc/telegram_companion/store.py:16-43` | `Store(home)`: creates `companion.sqlite3`, `Vault(home)` (own `secret.key`) | R/W |
| `bcc/telegram_companion/store.py:210-226` | `profile / put_profile / delete_profile` (table `profiles`, key `"<uid>:<uid>"`) | R/W/**DELETE** |
| `bcc/telegram_companion/store.py:228-236` | `forget(who)`: deletes learning_log, **profiles**, history, pending proposals/gates | **DELETE** |
| `bcc/telegram_companion/service.py:895-902` | `/forget` -> `/forget_confirm` (callback button only) -> `store.forget(person.key)` | **DELETE** |
| `bcc/telegram_companion/service.py:1187-1215` | `refresh_profiles()` -> `store.put_profile(person.key, ...)` for every `settings.people` | W |
| `bcc/telegram_companion/service.py:903-912` | `/privacy` reads profile | R |
| `bcc/telegram_companion/config.py:216-260` | `load(path, env_file)`: config.json + `credentials.enc` + env override | R |
| `bcc/telegram_companion/config.py:134-135` | `Settings.__post_init__`: exactly one owner, <=8 people | validation |
| `bcc/telegram_companion/owner_report.py:69-75` | `default_config()` then `load()`; owner = first role==owner person | R |
| `bcc/market/notify.py:152-159` | `default_config()` + `companion.env`, sends to the owner | R |
| `bcc/pit/bot_guard.py:79-84,87-104` | `companion_config_path()` duplicate of the same LOCALAPPDATA path (also `BOSSMAN_COMPANION_CONFIG`), fingerprints bot token to keep Jeff off the companion bot | R |
| `bcc/pit/bot_guard.py:25-30` | machine-wide per-token poller lock dir `%LOCALAPPDATA%\Bossman\telegram-pollers` (intentionally global, NOT part of this bug) | lock |
| `scripts/Start-TelegramCompanion.ps1:29-30` | defaults `-ConfigPath`/`-EnvFile` to LOCALAPPDATA | path |
| `bcc/telegram_companion/backend_target.py:24-30,63` | `default_core_data_dir()` = `bcc.config._data_dir()` (this one IS BCC_DATA_DIR aware) | R |

### 2b. Settings API (the code the owner's audit points at), all through `config_path()`

| Line | Function / route | Behaviour |
|---|---|---|
| `telegram_settings.py:67-72` | `config_path()` | the root cause |
| `:77-83` | `_read_config` | reads config.json |
| `:86-95` | `_read_secrets(home)` | reads `credentials.enc` with `Vault(home)` (companion's key) |
| `:98-107` | `_atomic_write` | tmp + replace, no backup |
| `:256-263` | `GET /telegram/settings` | reads |
| `:266-374` | `PUT /telegram/settings` `put_settings` | rewrites config.json AND credentials.enc (see 3) |
| `:342-344` | inside put_settings | `core_url` from `request.url.port`, `core_data_dir = settings.data_dir` of the *serving* instance (the only data_dir-aware line, it re-points the shared companion at whichever instance saved last) |
| `:379-396` | `POST /telegram/test` | `load(config_path())`, getMe only |
| `:405-468` | `POST/DELETE /telegram/token` | rewrites `credentials.enc`; DELETE also sets `enabled=false` in config.json |
| `:471-488` | `POST /telegram/commands` | `load(config_path())` |
| `:493-499` | `_store()` | `Store(config_path().parent)` if `companion.sqlite3` exists, else None |
| `:502-513` | `_people()` / `_key(uid)` | people from config.json; key `"<uid>:<uid>"`, 404 if uid not in people |
| `:516-535` | `GET /telegram/people` | `store.profile(key)`, entries, learn flags |
| `:542-560` | `PUT /telegram/profile/{uid}` | `store.put_profile(...)` (owner included) |
| `:563-572` | `DELETE /telegram/profile/{uid}` | `store.delete_profile(key)`; **works on the owner key, no confirmation, no snapshot** |
| `:575-611` | `POST /telegram/export` | writes `<home>/exports/*.jsonl` |
| `:616-625,720-745` | `_command`, `start()` | spawns the companion with `--config <config_path()>`, log in home |
| `:639-645` | `_holder()` | `instance_holder(config_path().parent)` |

`bcc/features/telegram_calls.py` and `bcc/telegram_calls/settings.py` are NOT affected: `calls_home()` is
`<Settings().data_dir>/telegram-calls` (`telegram_calls/settings.py:22-36`) and `features/telegram_calls.py:139,224,527-556`
use `svc.settings.data_dir`. (The only mention of `telegram-companion\calls` is a fixture string in
`tests/telegram_calls/test_hardening.py:69,77`.) `bcc/features/jeff_settings.py` and `bcc/pit/**` are data_dir based
(`PITStore(home)` at `pit/runtime.py:696`, `pit/web.py:273`, `pit/participant_admin.py:185`; `PITStore` merely subclasses
the companion `Store` class, its files sit in `<data_dir>/pit-v1.7`). `grep people[0]` finds nothing in code; the
"owner = people[0]" convention is only in operator notes, code picks `role == "owner"`.

## 3. What can delete / overwrite the owner profile or owner identity today

Ranked by how likely a "repeated settings save" or an "other instance" destroys owner data.

1. **PUT /telegram/settings strips the owner executor** - `telegram_settings.py:319-320`:
   `people = [Person(body.owner_id, body.owner_id, "owner", None)]` always passes `agent_id=None`. The live owner entry has
   `agent_id: 1`, so the very first UI save (or any later save) silently sets it to `null` and `cfg.update({"people": ...})`
   (`:323-324`) writes it. Every guest `agent_id` is dropped the same way. `tests/test_telegram_settings.py:119`
   even pins "all agent_id is None". Needs an explicit decision: the 2026-09-22 docstring (`:18-23`) says the section is
   chat-only, but the owner's live setup (operator notes: owner executor = `agent_id` in people[0]) says otherwise; either way a
   save must never *silently erase* an existing binding. Fix: carry `agent_id` over from `existing["people"]` for the same
   `user_id` + role.
2. **Corrupt/unreadable config is replaced without a backup** - `:271-274` `except (OSError, ValueError): existing = {}`;
   then `:322-347` builds a fresh cfg and `:367` overwrites the file. All keys that the UI does not own
   (`claude_bridge`, `codex_*`, `pc_control`, `jev_enabled`, `fast_timeout`, `monitor_seconds`, `profile_every`, ...) are lost,
   and there is no `.bak`. (When the file is readable they ARE preserved by `:322`.)
3. **Secrets partly wiped on every save** - `:350-351` forces `cloud_token: ""` and `core_token: ""` unless `image_enabled`
   (`:352-354` then re-sets core_token from the serving instance's auth token). A save from instance B writes B's auth token
   into the shared `credentials.enc`, which instance A's companion then sends to B (cross-instance token leak/misdirection).
4. **Wrong-instance save re-points the companion** - `:342-344` (`core_url`, `core_data_dir`) from whichever instance saved.
   The live file has `core_url 8801` and no `core_data_dir`; one save from a bugtest/CI instance changes the owner's
   companion target (the RC19 incident class). There is no per-data_dir isolation and no `autouse` conftest guard that
   forces `BOSSMAN_TELEGRAM_CONFIG` for tests (`tests/conftest.py:90-135` only guards the Fable ledger and the Jeff blocklist;
   every Telegram settings test sets the variable itself via fixtures at `tests/test_telegram_settings.py:53-62`,
   `test_backend_target_rc19.py:137`, `test_telegram_orphan_rc19.py:40`, `test_telegram_settings_ui.py:30`). Any new test that
   PUTs or DELETEs without the fixture would hit `%LOCALAPPDATA%\Bossman\telegram-companion` on the owner's machine.
5. **Changing `owner_id` orphans the owner profile** - profile key is `f"{uid}:{uid}"` (`:513,524`); after a save with a new
   `owner_id` the old row stays in sqlite but `_key()` returns 404 for it, so the UI can neither show nor delete it, and
   `Store.prune*` never reaps it (`store.py:225-226,307-314`). Not a delete, but "profile lost" from the owner's point of view.
6. **DELETE /telegram/profile/{uid} on the owner uid** (`:563-572` -> `store.py:222-223`) - single click in
   `ui/pages/telegram_settings.js:146`, no confirm, no snapshot, unrecoverable. Owner-initiated by design, but it is the one
   explicit way to lose the owner profile through the settings API.
7. **/forget_confirm in Telegram** (`service.py:898-902` -> `store.py:228-236`) deletes the profile of the person who pressed
   it (including the owner). Intentional privacy command; guarded by callback-only confirmation. Not a settings path.
8. **`refresh_profiles` overwrite** (`service.py:1215`) - `put_profile` replaces a profile the owner hand-edited
   (`edited_by_owner=True`, `telegram_settings.py:558`); the flag is stored but never consulted before overwrite
   (`store.py:214-220`). Silent loss of owner edits (version bump only).
9. Other writers that do NOT delete: `rotate_token` (`:415-440`), `revoke_token` (`:443-468`, sets `enabled=false`),
   `setup()`/`setup_ui` (create-only). `Vault` key mismatch (`:93`) tells the owner to *delete credentials.enc*, which is a
   manual destructive instruction on the shared home.
10. Migration hazard to design around: the companion's `Vault(home)` uses `<home>/secret.key` (`store.py:21`,
    `secrets.py:36-37`), NOT `data_dir/secret.key`. `credentials.enc` and every sealed sqlite value (`Store.seal/open`) are
    only readable with the SAME `secret.key`. A migration that copies sqlite/credentials without `secret.key` destroys access
    to the owner profile permanently.

## 4. Minimal safe fix

### (a) New canonical location

`<settings.data_dir>/telegram-companion/` (i.e. `<BCC_DATA_DIR>/telegram-companion/`), same file names as today:
`config.json`, `credentials.enc`, `secret.key`, `companion.sqlite3`, `companion.env`, `exports/`, `cloud-budget.sqlite3`.
Keeping the folder name `telegram-companion` keeps `tests/test_pit_telegram_fake.py:193-199` valid, and keeps it a sibling
of `pit-v1.7` and `telegram-calls`. Note: when running from a source checkout without `BCC_DATA_DIR`,
`bcc/config.py:24-25` makes data_dir `command-center/data` (git-ignored, `command-center/.gitignore:1`); the repo-leak test
`test_telegram_settings.py:405-412` must therefore be updated to resolve through an isolated data_dir.

One resolver, used everywhere (new `bcc/telegram_companion/paths.py`):

```
companion_config_path(data_dir=None) ->
  1. $BOSSMAN_TELEGRAM_CONFIG or $BOSSMAN_COMPANION_CONFIG   (explicit override, unchanged semantics; tests)
  2. <data_dir or bcc.config._data_dir()>/telegram-companion/config.json
legacy_config_path() = %LOCALAPPDATA%\Bossman\telegram-companion\config.json   (migration source only)
```

Callers to switch (no behaviour change beyond the path):
- `telegram_settings.py:67-72 config_path()` - needs `request`/`svc.settings.data_dir`; simplest is a module-level
  `_DATA_DIR` set from `app.state.svc.settings.data_dir` at feature mount, or add a `Request` param to the 12 routes that call
  `config_path()` (`:258,269,384,425,448,478,496,504,519,584,643,723`). Prefer a small `_cfg_path(request)` helper plus
  `functools.lru_cache`-free lookup, so tests keep working with the env override.
- `telegram_companion/__main__.py:19-21 default_config()` and `:133` (CLI default) - delegate to the resolver; `owner_report.py:69-71`,
  `market/notify.py:152-155`, `pit/bot_guard.py:79-84` call the same function (delete the duplicated path logic).
- `scripts/Start-TelegramCompanion.ps1:29-30` - default to `$env:BCC_DATA_DIR\telegram-companion` when set, else legacy.
- `_command()` (`:616-625`) already passes `--config <path>`; it then starts the companion in the canonical home automatically.
- Split-brain guard: after migration write `<legacy home>/MIGRATED_TO.json` (a NEW marker file, nothing removed);
  `serve()` (`__main__.py:91`) follows the marker and logs `TELEGRAM_COMPANION=HOME_MIGRATED_FOLLOWING_MARKER`, so a
  launcher still pointing at the legacy path cannot run a second diverging copy. The per-token poller lock
  (`pit/bot_guard.py:34-60`) already prevents two live pollers.

### (b) Migration (backup first, copy-only, idempotent, never deletes the source)

New function `migrate_companion_home(legacy_home, new_home, *, now=None) -> dict` in `paths.py`, called lazily by the
resolver (first settings API call / companion start) and by an explicit CLI `python -m bcc.telegram_companion --migrate`.

1. If `new_home/config.json` exists -> return `{"status": "ALREADY_MIGRATED"}`; do nothing (idempotent, never overwrites
   a canonical file). If `legacy_home/config.json` does not exist -> `{"status": "NO_LEGACY"}`; canonical stays empty.
2. Refuse (`HOME_IN_USE`) while `store.instance_holder(legacy_home)` is not None (live poller) unless called from the
   explicit CLI with `--force-copy-live` (sqlite is copied via the backup API anyway, see 4).
3. **Backup first**: copy the entire legacy home to `<legacy_home>.migration-backup-<UTC yyyymmddTHHMMSSZ>/`
   (`shutil.copytree`, ignoring `poller.lock`, `poller.json`; for `*.sqlite3` use `sqlite3.Connection.backup`). Verify
   sha256 of config.json/credentials.enc/secret.key against the source and the integrity (`PRAGMA integrity_check`) of the
   sqlite copy; abort with `MIGRATION_BACKUP_FAILED` before touching the destination if anything mismatches. Every legacy
   `config.json.bak-*` travels with it for free.
4. Copy into a temp sibling `new_home.tmp-<ts>` in this order: `secret.key` FIRST (see hazard 10), `credentials.enc`,
   `config.json`, `companion.sqlite3` (backup API, produces a consistent snapshot even if the legacy store is open),
   `companion.env`, `exports/`, `cloud-budget.sqlite3`. Skip lock/pid files. Re-open the copy with
   `Store(tmp)` and `load(tmp/config.json)` to prove the secret key still decrypts the credentials and a known profile row;
   then `os.replace(tmp, new_home)`. On any failure remove ONLY the temp folder; the legacy home is never modified.
5. Write `<legacy_home>/MIGRATED_TO.json` `{"to": ..., "at": ..., "backup": ..., "sha256": {...}}` (add-only).
6. If the migrated config.json has `core_data_dir == ""`, set it to the owning `data_dir` ONLY in the migrated copy
   (so a post-migration companion cannot be re-pointed by another instance). Everything else is byte-identical.
7. Return a structured result (counts of profile rows per person key before/after) so the API can show it.

The source is never deleted, moved, truncated or rewritten by the migration.

### (c) Harden the save/delete paths (same change set, small)

- `put_settings` (`telegram_settings.py:266`): (i) before the first overwrite in a process, and whenever the content changes,
  write `config.json.bak-<ts>` and `credentials.enc.bak-<ts>` beside the target (keep last N=10); (ii) on a corrupt config
  (`:271-274`) refuse with 409 instead of continuing with `{}` (the GET at `:261` already treats it as 409); (iii) carry over
  `agent_id` per `(user_id, role)` from `existing["people"]` (`:319-320`); (iv) do not blank `cloud_token`/`core_token`
  that the owner set (`:350-351`) unless the corresponding feature is being turned off in this request; (v) if
  `existing["core_data_dir"]` is set and differs from this instance's data_dir, return 409 `TELEGRAM_OWNED_BY_OTHER_INSTANCE`
  instead of re-pointing (`:342-344`).
- `delete_profile` (`:563-572`, `store.py:222`): snapshot the sealed row into a new table `profile_backups(who, body, deleted)`
  (or `<home>/backups/profile-<key>-<ts>.json.enc`) before `DELETE`, and require `?confirm=owner` when `uid` is the owner.
- Orphan guard: a save that changes `owner_id` returns a warning listing the old profile key instead of silently orphaning it.
- `refresh_profiles` (`service.py:1196-1215`): skip overwriting when `old.get("edited_by_owner")` unless `force=True`.
- `tests/conftest.py`: add an `autouse` fixture that sets `BOSSMAN_TELEGRAM_CONFIG` (and `BOSSMAN_COMPANION_CONFIG`) to
  `tmp_path/"tg-guard"/"config.json"` so no test can touch `%LOCALAPPDATA%` (existing tests override it again, fine).

### Files / functions to change

1. NEW `bcc/telegram_companion/paths.py`: `companion_config_path`, `legacy_config_path`, `migrate_companion_home`, `follow_marker`.
2. `bcc/telegram_companion/__main__.py`: `default_config()`, `main()` (`--migrate`), `serve()` (follow marker).
3. `bcc/features/telegram_settings.py`: `config_path()`, `put_settings`, `delete_profile`, `_store`, `people`, plus a data_dir-aware accessor.
4. `bcc/telegram_companion/store.py`: `delete_profile` (snapshot), optional `profile_backups` table.
5. `bcc/telegram_companion/service.py`: `refresh_profiles` honours `edited_by_owner`.
6. `bcc/telegram_companion/owner_report.py`, `bcc/market/notify.py`, `bcc/pit/bot_guard.py::companion_config_path`: use the shared resolver.
7. `scripts/Start-TelegramCompanion.ps1`: default paths.
8. `tests/conftest.py`: autouse guard; `tests/test_telegram_settings.py:119,405-412` adjust.

## 5. Tests to add (exact)

New file `tests/test_telegram_store_location.py` (uses `tmp_path`, `monkeypatch`, never the real LOCALAPPDATA):

1. `test_config_path_follows_bcc_data_dir` - set `BCC_DATA_DIR=tmp/a`, unset overrides: `companion_config_path() ==
   tmp/a/telegram-companion/config.json`; with `BOSSMAN_TELEGRAM_CONFIG` set the override wins.
2. `test_two_data_dirs_are_isolated` - two `env`-style apps with data dirs A and B; PUT settings on A; GET on B returns
   `configured: false`; A's `config.json` untouched by B's save (byte compare).
3. `test_settings_api_never_touches_localappdata` - set `LOCALAPPDATA=tmp/fake_local` with an existing sentinel
   `Bossman/telegram-companion/config.json`; run PUT/GET/DELETE profile/token routes without `BOSSMAN_TELEGRAM_CONFIG`;
   assert the sentinel is byte-identical and nothing new appeared under `fake_local`.
4. `test_migration_backs_up_then_copies_and_keeps_source` - build a legacy home (config.json with owner
   `agent_id: 1`, `Store.put_profile(owner_key,...)`, a guest profile, credentials.enc, secret.key); run
   `migrate_companion_home`; assert a timestamped `*.migration-backup-*` exists BEFORE the destination (record order via
   mtime or a monkeypatched copy hook), every legacy file still exists with the same sha256, destination
   `Store(new_home).profile(owner_key)` equals the legacy value, and `load(new/config.json)` decrypts credentials.
5. `test_migration_is_idempotent` - run twice; second returns `ALREADY_MIGRATED`, destination hashes unchanged, no second
   backup directory is created, destination file edited between runs (e.g. new setting) is NOT overwritten.
6. `test_migration_failure_leaves_source_and_destination_clean` - monkeypatch the sqlite copy to raise; assert legacy home
   hash-identical, no `new_home`, no `*.tmp-*` leftovers, error code `MIGRATION_BACKUP_FAILED` or `MIGRATION_COPY_FAILED`.
7. `test_migration_refuses_live_poller` - hold `single_instance(legacy_home)`; migration returns `HOME_IN_USE`, nothing written.
8. `test_migration_requires_secret_key` - legacy home without `secret.key` -> refuse (`SECRET_KEY_MISSING`) rather than
   producing an undecryptable copy.
9. **`test_owner_profile_survives_migration_and_repeated_saves`** (the required proof):
   arrange legacy home with an owner profile (`edited_by_owner=True`, version 3) and owner `agent_id=1`;
   (i) migrate; (ii) call `PUT /api/telegram/settings` 5 times with the same body, alternating `bot_token` empty/non-empty,
   `enabled` true/false, `image_enabled` on/off; (iii) migrate again; (iv) `GET /api/telegram/people`.
   Assert after every step: owner profile row present, `text`/`version`/`edited_by_owner` unchanged, owner is still
   `people[0]`-role owner with `agent_id == 1`, sqlite `SELECT count(*) FROM profiles` never decreases, and the legacy
   source hashes never change.
10. `test_put_settings_preserves_agent_id_and_unknown_keys` - existing config has `agent_id: 1`, `claude_bridge: true`,
    `pc_control: true`; after PUT they are unchanged (replaces the assertion at `test_telegram_settings.py:119`).
11. `test_put_settings_refuses_corrupt_config_and_backs_up` - write `{not json`; PUT returns 409, file bytes unchanged;
    with a valid config, a `config.json.bak-*` appears before the first rewrite.
12. `test_put_settings_does_not_repoint_foreign_instance` - config has `core_data_dir=<other>`; PUT from this instance ->
    409 `TELEGRAM_OWNED_BY_OTHER_INSTANCE`, file unchanged.
13. `test_owner_id_change_warns_and_keeps_old_profile` - after owner_id change the old profile row still exists and the
    response carries the orphan warning.
14. `test_delete_owner_profile_requires_confirm_and_snapshots` - DELETE without `confirm=owner` -> 4xx, row present;
    with it -> 200, snapshot row/file exists and is restorable.
15. `test_refresh_profiles_does_not_clobber_owner_edit` - `edited_by_owner=True` profile, `refresh_profiles()` leaves it
    unchanged; `force=True` replaces it.
16. `test_serve_follows_migration_marker` - legacy `--config` with `MIGRATED_TO.json` resolves to the canonical home.
17. Update `tests/test_pit_telegram_fake.py:193-199` and `tests/test_telegram_settings.py:405-412` to resolve through an
    isolated `BCC_DATA_DIR`; add the autouse guard to `tests/conftest.py` and a meta-test that fails if
    `os.environ['BOSSMAN_TELEGRAM_CONFIG']` points outside `tmp_path` during a test.

## 6. Risks / open questions for the owner

- `agent_id` policy: settings docstring says chat-only (`telegram_settings.py:18-23`) but the live owner has `agent_id: 1`.
  Recommended: preserve, never silently clear; clearing requires an explicit field in `SettingsIn`.
- The owner's live companion is started by a launcher that uses the legacy path; the marker redirect plus the per-token
  lock make a mixed start safe, but the first migration must happen with the companion stopped (Stop in UI), then Start.
- Nothing in this analysis was modified or deleted; the live files under `%LOCALAPPDATA%\Bossman\telegram-companion` were only
  listed, and only non-secret key names / role / agent_id were read from config.json.
