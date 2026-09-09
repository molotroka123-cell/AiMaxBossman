# AI File Sorter — external sidecar integration

Bossman's **File Intelligence** capability drives [hyperfield/ai-file-sorter][up]
as a separate program. The sidecar proposes; Bossman authorises; the filesystem
— not the process exit code — says what happened.

[up]: https://github.com/hyperfield/ai-file-sorter

| | |
| --- | --- |
| Upstream | <https://github.com/hyperfield/ai-file-sorter> |
| Pinned commit | `4dc374df69b5e63d5354e121097d92e25bbd32da` |
| Licence | AGPL-3.0-or-later — see `NOTICE.md` |
| Mode | `external_sidecar` — subprocess, typed argv, documented process boundary |
| Bossman code | `command-center/bcc/file_intelligence/` |
| Flag | `file_intelligence_v1` (`BCC_FILE_INTELLIGENCE`), **off by default** |

The pin, the licence and the protocol constants live in `integration.json`. Code
reads them from there rather than repeating the SHA, so there is one place to
change when the pin moves.

## The rule this integration exists to enforce

```
PROPOSAL       != AUTHORIZATION
APPROVAL       != POST_STATE
PROCESS_EXIT_0 != VERIFIED_EFFECT
MODEL_TEXT     != PROOF
```

AI File Sorter is a good file organiser. It is not, and does not become, an
authority in Bossman. Everything below follows from that.

## Flow

```
owner
  ↓
scope + privacy preflight          ← refuses here, before any process starts
  ↓
aifilesorter --headless --review-only
  ↓
review plan  →  Bossman envelope (digest, source identities, hashes)
  ↓
OWNER APPROVAL                     ← a subset, not a rubber stamp
  ↓
fresh source re-verification       ← STALE_REVIEW_PLAN refuses the whole unit
  ↓
aifilesorter --headless-apply <approved plan>
  ↓
independent filesystem observation ← Bossman looks itself
  ↓
verified receipt   |   VERIFICATION_FAILED
```

## Installing the sidecar

Bossman does **not** clone, build or update AI File Sorter. Doing that at startup
would pull AGPL source into the product's own lifecycle and silently follow
upstream `main`. Installation is an explicit owner action, into a directory
outside the Bossman tree:

```sh
git clone https://github.com/hyperfield/ai-file-sorter /opt/ai-file-sorter
cd /opt/ai-file-sorter
git checkout 4dc374df69b5e63d5354e121097d92e25bbd32da
git rev-parse HEAD          # must print the SHA above before you build
# then build per upstream's own instructions
```

Updating is likewise explicit: compare the installed SHA against the pin, decide,
and change `integration.json`. There is no auto-update, because supply-chain
drift into a component that moves the owner's files is not a convenience.

## Configuration

| Setting | Meaning |
| --- | --- |
| `BCC_FILE_INTELLIGENCE` | `1` enables the capability. Default off. |
| `AIFS_EXECUTABLE` | Full path to the executable. **Preferred** — a PATH match is accepted but recorded as unpinned. |
| `AIFS_INSTALL_DIR` | External integration directory, searched if `AIFS_EXECUTABLE` is unset. |
| `AIFS_CONFIG` | Path to the sidecar's INI, if not in a standard location. |
| `AIFS_ROOTS` | `os.pathsep`-separated roots the owner authorises. **Empty means nowhere.** |

## What the doctor reports, and what it refuses to claim

`GET /api/file-intelligence/status` works even while the feature is off, so the
owner can see what is missing before enabling anything.

* `AVAILABLE` / `NOT_INSTALLED` / `WRONG_VERSION` / `PROTOCOL_FAILED` / `BUSY`
* `resolved_executable`, `binary_sha256`, `pinned_upstream_sha_expected`
* `binary_version: VERSION_UNVERIFIED`

That last one is deliberate. The executable does not report the upstream commit
it was built from, so Bossman does not claim to know it. A pinned manifest is a
statement about *this repository's documentation*; what the binary on the owner's
machine was compiled from is a separate question. Calling the build pinned because
the document beside it is pinned would substitute a self-reference for a check.

## Boundaries, and why each one is where it is

**No vendored source.** Nothing under `bossman-core/`, `command-center/`,
`learning/` or `bossman_shared/` derives from the upstream C++ tree. The only
upstream artefacts here are this README, `NOTICE.md`, and the protocol constants
in `integration.json` — flag names and JSON keys, read from upstream source so
Bossman speaks the contract correctly.

**Typed argv, never a shell string.** Arguments are built as a list from enums
and already-scope-checked paths. A file named `; rm -rf ~` stays a filename
because no interpreter exists in that path to read it as anything else. This is
not better escaping; it is the absence of anything to escape.

**`--review-only` is not a parameter.** A parameter can be passed, and a passed
value can be computed from untrusted input. Analysis has no switch.

**Auto-apply is unreachable.** Upstream accepts four spellings —
`--auto-apply`, `--headless-auto-apply`, `--apply-without-review`,
`--headless-apply-without-review` — and all four, plus their `=value` forms, are
rejected in the *built* argv. Checking intent proves nothing; checking the result
does.

**Apply executes only the approved plan.** `--headless-apply` accepts no path and
no operation. Because upstream applies the *whole* plan file it is given, Bossman
derives a filtered plan containing only the approved entries — otherwise
approving five files out of twenty would apply twenty.

**Scope sits above upstream's own.** Upstream already avoids structured project
folders; that is its *sorting* policy. This is Bossman's *authority* policy, and
two independent protections are fine here because they answer different questions
and either one saying no means no.

Denied by default: any `.git` repository (found by marker, not by a list of known
paths — an unknown clone in Downloads is more dangerous than a known one),
Bossman state, credential and secrets directories, system and installation roots,
active sandboxes and workspaces, traversal, symlink and junction escape, and
anything outside `AIFS_ROOTS`. Repository *mutation* has no opt-in at all;
repository *analysis* requires an explicit one.

This capability is for Downloads, Documents, Photos, Media, archives and ordinary
owner folders. It is not for source-code refactoring.

**Privacy fails closed.** Upstream's `is_remote_choice()` counts
`LLMChoice::Custom` — an arbitrary OpenAI-compatible endpoint — as local, so
Bossman classifies the backend itself: `Custom` and `Unset` are UNKNOWN, and
UNKNOWN is refused even with owner approval, because there is nothing to approve
while the destination is unproven. Local is never inferred from "a local
executable started": a local process pointed at a remote endpoint is exactly the
case the rule exists for.

**One runtime owner.** Upstream has its own `AnalysisRuntimeLock`; Bossman's
scheduler does not create the situation of two sorters against one directory. A
concurrent request WAITs. A stale lock is recovered by upstream's rule — this
host, dead pid — never by age, since a slow analysis legitimately holds the
runtime for a long time.

**Undo is not offered.** `UNDO_HEADLESS=UNAVAILABLE`. Upstream at this SHA has no
headless undo flag; `undo_dir` is only passed through to apply options. Bossman
keeps enough provenance for a future verified inverse operation but does not ship
an unsafe one, and does not put a button in the UI for an operation that does not
exist. A visible undo control would be a defect.

## Testing levels, kept apart

| Level | What it proves | State |
| --- | --- | --- |
| A — contract | Bossman behaves correctly on every protocol answer, using a deterministic fake sidecar. Runs in CI with no models. | 113 tests passing |
| B — real binary smoke | The real executable analyses, reviews, applies and verifies on a temporary folder. | `REAL_BINARY_NOT_RUN` |
| C — owner machine | Real Windows, real folder, real local model, disposable corpus. | `OWNER_LOCAL_MODEL_NOT_RUN` |

Level A passing is not evidence that the real binary works. If the binary is
absent, level B reports `NOT_RUN` — never a fabricated pass.
