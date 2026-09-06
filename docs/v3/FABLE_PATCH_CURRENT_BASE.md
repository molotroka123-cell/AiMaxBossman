# Media/Web/Fleet patch on the active Fable branch

The active branch discovered during publication is
`claude/fable-system-hardening-lpqq9r`, base
`cb75656cdd1d84553158d27ec965363a3ea14282`.
It already contains fourteen new Fable commits above b85ab17, including FFmpeg
installation and stronger context, approvals, dispatch and certification checks.

This patch is rebuilt on that exact tree. It preserves Fable's engine, approvals,
finalizer, context compiler, certification tools and existing Command Center CI
byte-for-byte. There is no merge of the old closure branch into its hot files.
Source-preserving DOM hardening developed on the earlier Fable lane is retained
in the Web module together with the newly reproduced token-fidelity regressions.

`FABLE_MEDIA_FLEET_PATCH_20260906.md` records original development on the unified
candidate. Its counts are local historical measurements, not certification of
this newly based tree. The two old approval-matrix test adjustments and the
unified-candidate workflow are intentionally omitted because this active branch
has its own stronger approval/crash/certification tests. FFmpeg installation
already supplied by Fable is not redundantly changed in Command Center CI.

New `Fable media and Fleet acceptance` runs real FFmpeg, the full media/Web suite,
actual loopback mutual TLS Fleet tests and Fable interaction regressions on exact
SHA with Python 3.11 and 3.12. It is still a subsystem gate, not full release or
Windows/sandbox/multi-host production certification. Remote RPC is experimental,
explicitly enabled, and requires the canonical Fleet authority; it is not made
production-ready by network authentication alone.

No paid model calls, background autonomy activation, disabled security gates,
coverage reductions or product data changes are authorized by this handoff.
