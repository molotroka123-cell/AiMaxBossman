# OpenHands host evidence corrections

This is component evidence, not a live model-backed OpenHands acceptance or a
final release verdict. The real model/SDK boundary remains OWNER_LIVE_REQUIRED.

| ID | Severity | Reproduction and cause | Correction and controls |
|---|---|---|---|
| OH-NEW-01 | P1 | Changing a protected file's executable bit returned completed with an empty changed-file list. Filesystem comparison checked only blob bytes; the documented Git-view union was not actually returned. | Compare type/mode independently and keep the union. Protected chmod is refused with core.filemode both true and false; allowed executable changes remain in the actual diff. |
| OH-NEW-02 | P1 | Replacing equal-size bytes and restoring mtime during diff generation admitted evidence for inconsistent contents. | Host captures bounded immutable bytes around observation and refuses a changed snapshot. The reviewed diff is rendered from those captured bytes in a separate directory, with external diff/text conversion disabled. |
| OH-NEW-03 | P1 | Teacher reopened a pathname after the client validated it; replacement VALUE=9 was proposed instead of the observed VALUE=2. | Teacher consumes host-captured immutable bytes. The regression replaces the path after validation and still receives VALUE=2. |
| OH-NEW-04 | P1 | Teacher followed an allowed symlink and read an external controlled file as patch source. | Preserve link evidence in the client; refuse non-regular or mode-changing text proposals in Teacher. No pathname is reopened by Teacher. |
| OH-NEW-05 | P1 | A configured sidecar script could be replaced between client setup and dispatch, including same-size/restored-mtime replacement. | Hash the resolved executable and actual script arguments at setup and again before dispatch. Replaced script is refused; unchanged script executes. |
| OH-NEW-06 | P2 | Workspace evidence used unrestricted whole-file reads. | Bound each file to 8 MiB, snapshots to 32 MiB and 10,000 files. Descriptor reads check type/identity; POSIX parent handles do not follow links; Windows checks the actual opened handle. Oversized output is refused before admission. |
| OH-NEW-07 | P1 | Non-executable permission changes are not represented by Git's tree mode. | Compare observed permission bits against the pre-run snapshot; reject protected changes and unsupported permission-only patches. |

The additional tests are in
`bossman-core/tests/apprentice/test_openhands_release_evidence.py`. The existing
client, evidence-independence and path-boundary suites remain required. The
positive controls preserve new files, binary diffs, allowed symlinks, ordinary
text edits, and an unchanged configured sidecar.

One existing test enumerates every `OpenHandsResult` field to forbid an agent
granting itself mission authority. Its exact field set now additionally permits
`files`, an immutable host snapshot, and still forbids push/merge/deploy/
permissions/authority/approve fields. This is a necessary result-contract
correction, not permission for the sidecar to supply those bytes.

Initial controls on the previous implementation reproduced the mode, changed
bytes, teacher-reopen and teacher-link failures. The configured-script control
also failed before identity checking. Local source regressions pass; Windows
workspace/PID jobs now exercise supported Python versions. Only results on the
final published SHA may be counted as final acceptance.
