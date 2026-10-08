---
name: bossman-shared-helper-change-safety
description: Checklist before changing a shared helper or utility used by many modules. Prevents a local fix from silently disabling a downstream guard, as happened with safe_get and web_research net.py.
compatibility: BOSSMAN, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: safe-change
  learned_from: our own regression, 2026-10-06
---

# Shared-helper change safety

## The regression
A fix in the shared helper `safe_get` dropped the `Content-Encoding` response header. The downstream guard in web_research `net.py` reads that header to decide whether the body is compressed. With the header gone the guard never fired and nothing failed loudly. A fix in one place turned off a safety check in another.

## Before changing a shared helper
1. List every consumer: grep the symbol name across the whole repo (`command-center`, `bossman-core`, `tools`, tests), including re-exports and aliases.
2. Read what each consumer does with the output: which fields, headers, keys or exceptions it depends on.
3. Run the tests of every consumer, not only the helper's own tests. If a consumer has no test for the dependency, write one first.
4. Treat output fields as a contract: removing, renaming or retyping anything the helper returns is a breaking change.

## How to change safely
- Preserve information under a new name instead of dropping it. If a header or field must be stripped or normalized for one caller, keep the original as `raw_<name>` or on a separate attribute so other consumers still see it.
- Prefer a new parameter with the old behavior as default over changing behavior for all callers.
- Make guards fail closed: a guard that cannot find its input should block or warn, not pass silently. Add a test that the guard fires when its input is present.

## Check
- Diff the helper's returned keys and headers before and after on a sample input; the set may only grow.
- Name the consumers you checked in the commit message.
