# Fable media / web / Fleet closure delta — 2026-09-06

Requested target: `claude/bossman-closure-engineering-b8h722`.
Prepared on integrated source `beea20fbf5b3b66fa08a8ba936703cd0bc434cf3`, which
already contains Fable `dc0d149b91bee6617f5d2d207e99cd744eb24905` as an ancestor.
This retains the integrated native Video Studio absent from the older Fable tip.
Do not reset newer Fable commits when publishing; compare and reconcile first.

## Historical findings vs this patch

The seven Web Designer P1 findings in the old audit are NOT seven new fixes here.
Fable's existing repairs and regressions are retained. Fresh reproduction found
additional loss of SVG attribute case when a tag name is followed by a newline,
rewritten closing-tag spelling/whitespace, discarded unmatched closing tokens and
invented closing tags in intentionally incomplete documents. Parsed nodes now
retain literal or omitted endings; constructed nodes still serialize valid closes.
The original web regressions plus new source-fidelity cases passed locally.

A full real-FFmpeg run exposed a further range-export failure: off-grid intervals
such as 1.1–1.8s produced 17 frames instead of the declared 18 at 25fps. Inspection
of actual filter frames showed that the trim/overlay link could lack a frame-rate
for tpad, and output -t could independently quantize shorter intervals downward.
The link is normalized with eof_action=pass before lookahead; the video encoder
uses the declared integer frame limit. Audio remains bounded by atrim. Full decode,
actual endpoint pixels, frame count, timestamps and source integrity remain the
oracles. Verification was not relaxed and expected frame counts were not reduced.

## Native Video Studio export

The original completion hook decoded and hashed the full export again inside the
engine's shared short critical-hook budget. The renderer still performs its full
independent oracle in the native executor. Publication is hashed and bound to that
oracle before a durable receipt is signed by the existing evidence infrastructure.
It binds job/task/run/project, snapshot/options/result digests, owned path, content
hash and file identity. A model answer, queue ID or unsigned result cannot replace it.

The completion hook validates the receipt and current binding without a second
full decode. POSIX change-time identity avoids duplicate hashing for ordinary
host-owned artifacts. **Windows rehashes** because its stat ctime may be creation
time; restored timestamps must not silently pass integrity. Large Windows hash
latency remains a required platform measurement, not an achieved performance claim.
Downloads still perform their own full content verification; completed read hashes
are not made into a persistent general-purpose trust cache. Privileged filesystem
races after verification and whole-snapshot rollback remain outside this receipt's
guarantee and are NOT declared fixed.

Unsigned legacy results, changed artifacts and wrong-run proof fail explicitly;
there is no automatic blind re-render or approval-as-verification fallback. Owner
recovery for legacy/ambiguous artifacts requires fresh verification or an explicit
new export. This patch does not certify all crash points or all codecs/hardware.

Failures now preserve bounded safe code/message/stage/context metadata in the job,
including timeout, verification, invalid project and resource failures. Error
writes require the live execution fence. The UI displays those safe details, not
raw subprocess output, secret values or host paths.

## CI and merged approval contract

Existing Core/Command Center workflows now provision system FFmpeg and run a real
encode -> ffprobe -> full decode preflight. Missing binaries are hard failures;
optional ASR/model/hardware tests remain honestly optional. A focused exact-SHA
workflow exercises actual media, the new source cases and Fleet auth/RPC alongside
the existing lease/evidence/approval regressions. Required coverage is unchanged.
No self-hosted sandbox test is relabelled as a simulated PASS.

Two merged approval-matrix assertions expected completed after git push was denied
or its approval consumed, even while the model said "готово". The integrated
finalizer correctly marks an unclassified failed mutation failed. Only those
contradictory expectations were corrected, with added checks that there is no
post-state contract, no effect, a retained answer, a failed run and no impossible
review loop. Positive approved-effect tests and real executor denial tests remain.
No production finalizer rule was weakened to make the merge green.

## Fleet

Opt-in authenticated transport, not production Fleet: see
[remote RPC boundary and tests](../fleet/REMOTE_RPC_EXPERIMENTAL.md).
Default remote activation stays off. Full multi-host node authority, deployment,
rotation operations, partition behavior and long soak acceptance are still open.

## Evidence limits

Local reproduction uses Linux/Python 3.13 with actual FFmpeg 7.1.5, not the supported
3.11/3.12/Windows CI matrix. Synthetic input media and deterministic memory-admission
telemetry do not attest hardware or AI model quality. No paid provider is invoked.
The root run had 412 passing tests and three environment failures: offline isolated
wheel build (no setuptools download), and two missing solders imports. They were
not skipped, xfailed, weakened or misreported as a full PASS.

Final local media run: **242 passed, 11 optional skips**, with real FFmpeg.
Fleet auth/RPC plus existing Fleet regressions: **87 passed**.
The web-only original/source-fidelity suite: **55 passed**. These sets overlap
other focused runs and must not be summed into a release score.

Logs for attempts and final exact-SHA CI belong to the delivery PR/workflow artifacts.
A passing narrow subset, older SHA or successful merge is not a release certificate.
