# CURRENT STATE

Canonical branch: `release/bossman-owner`. Canonical convergence PR: #67.

This file is a pointer, not a certificate. The authoritative release state is the latest remote SHA plus its exact-SHA workflow runs.

Current release rules:
- one canonical branch and one candidate SHA;
- no transfer of evidence from an older SHA;
- deterministic tests are not called real-AI evidence;
- source imports are not installed-product evidence;
- missing code is not OWNER_HARDWARE_REQUIRED;
- mocks and demo fallbacks never count as owner success.

Owner-hardware-only areas include real local-model execution, real owner desktop interaction, real Telegram account delivery and human-only login/CAPTCHA steps. Software-fixable gaps remain release blockers until fixed or explicitly deferred as noncritical.
