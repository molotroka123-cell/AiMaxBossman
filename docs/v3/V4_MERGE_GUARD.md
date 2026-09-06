# Merge guard for the v4 editor integration

`origin/codex/astra-v4-integration` (commit `357a934`) integrates the video studio
and the web designer into Command Center. It is branched from `d6b43ce`, which is
**before** the Epoch 3 closure work. Both branches therefore carry their own copy
of the same two editors, and a careless merge silently reverts fixes that are
already on `claude/bossman-control-v03-43igbk`.

This file is the checklist for whoever performs that merge. It is not an
objection to the v4 work — that work is wanted. It is a list of things that must
survive it.

## Independent agreement, recorded

Both lines found the Windows evidence-key defect separately and fixed it the same
way, with `O_BINARY`. The v4 version additionally calls `os.fsync` before closing
the descriptor, which is strictly better: without it the key file can exist with
no bytes in it after an abrupt stop, and every previously signed piece of evidence
stops verifying. **That improvement has been adopted here**, so the two versions of
`bossman_shared/evidence.py` now agree and this hunk should merge cleanly.

## What must not be lost

### Web designer — take the Epoch 3 version, not the v4 copy

`command-center/bcc/web_designer_dom.py` is 532 lines on v4 and 756 lines on the
primary branch, because the primary branch carries sixteen reproduced fixes. The
v4 copy is the pristine source-branch version. **Taking v4's copy re-opens all of
them.** The two that matter most are live-script defects that reach the owner's
exported site:

- attribute **names** were never escaped, so an attribute named
  `x" onmouseover="alert(1)` serialised into a working event handler;
- the generator interpolated the project name into `title`, the logo and `h1`
  with no escaping, giving four live script occurrences in a saved site.

Also lost would be: processing instructions and CDATA preserved through a
round-trip, inline SVG surviving case folding (`viewBox`, `linearGradient`,
`clipPath`), unterminated markup kept instead of swallowed, iterative
serialisation and traversal that no longer raise `RecursionError` at roughly 400
levels of nesting, a text edit that no longer flattens a subtree, stale element
ids refused with a 409 instead of editing an innocent element, compare-and-set
saves that report a conflict instead of last-writer-wins, and atomic writes.

`command-center/tests/test_web_designer.py` on the primary branch holds the 22
regressions that pin all of this. If the merged tree keeps v4's module and the
primary branch's tests, the tests fail — which is the desired failure, because it
means the guard worked. Resolve it by keeping **both** the fixed module and the
tests.

### Video studio — the closure fixes are not in either branch

The video studio audited in Epoch 3 lives on `origin/codex/video-studio`, and its
closure fixes were delivered as a patch handed to the owner, not committed
anywhere. `docs/v3/VIDEO_STUDIO_CLOSURE_FINDINGS.md` records what they are and
what was measured. v4 integrates the studio **without** them. Before or shortly
after the merge, these need re-applying against the integrated copy:

- **Export dead end.** Verification re-decoded and re-hashed the artifact inside
  one fixed 60-second hook budget shared by every hook, so an export that was
  already verified and on disk parked in `waiting_approval` with a null download
  and a permanent 409, and approval could not rescue it. The fix moves
  verification into the executor, where it has the job's lifetime, and records it
  as a durable receipt. Verification is not skipped — it happens once, in the
  right place.
- **Typed failure reasons** instead of an exception class name reaching the owner.
- **A GET that amplifies into unbounded work**: any media request hashed whole
  files on the shared thread pool with no dedup and no cap.
- **Cached hashes bound to file identity**, with the honest limit stated in code.
- **One canonical duration contract**, so the timeline cannot render `NaN`.
- **A real colour defect**: output was tagged bt709 but never converted to it, so
  the same source came out in different colours at different sizes. Only
  installing real FFmpeg exposed this.

### FFmpeg in CI

v4 adds steps to both CI workflows. Whatever form they take, the requirement is
that missing binaries make the build **red**, not silently skip. On the video
studio branch 61 tests are gated on real FFmpeg and 12 of them fail outright
rather than skipping, because three files carry no guard. Do not add guards to
those twelve: that converts a measurable loss of coverage into an invisible one.

### Execution truth and authorization

The primary branch carries fixes v4 cannot know about, all in
`command-center/bcc/engine.py`, `bcc/tools.py` and `bcc/finalize.py`. v4 also
edits `engine.py` and `terminal_control.py`, so these will conflict and must be
resolved by keeping both sides' intent:

- an approval token is bound to the call it was issued for, and to that task and
  that run, so a token from another run, another task, or one already spent
  cannot authorise an effect;
- authorization is revalidated at effect time, so a revoked tool and a newly
  denied policy stop an approval that predates them;
- a crash between the effect and the journal cannot produce a duplicate effect on
  either the approval or the automatic path; the call ends `uncertain`, which the
  finalizer refuses to credit;
- tool generation is per name, so an unrelated tool registering first no longer
  throws away the owner's approval across a restart;
- the finalizer enforces declared obligations only and does not manufacture
  obligations it can never satisfy.

`command-center/tests/test_approval_resume_matrix.py` and
`tests/test_golden_missions.py` pin these. If they fail after the merge, the
merge lost something.

## Suggested order

1. Merge, resolving every editor file in favour of the **primary branch** copy
   where the primary branch has fixes, and taking v4's genuinely new files.
2. Run `tests/test_web_designer.py`, `tests/test_approval_resume_matrix.py`,
   `tests/test_golden_missions.py` and `tests/test_redteam_closure.py` first —
   they are the tripwires.
3. Then the full Command Center and Core gates.
4. Re-apply the video studio closure patch against the integrated copy.
