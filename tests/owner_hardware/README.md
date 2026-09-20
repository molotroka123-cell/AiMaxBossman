# Owner Hardware Acceptance Pack

Run only against the exact installed release candidate. Do not run from an editable checkout.

The machine-readable checklist is `manifest.json`. The existing MVČR scenario remains authoritative at `tests/owner_scenarios/OWNER_HARDWARE_FIRST_RUN.md`.

## One owner command

From the installed package environment:

```powershell
python -m bcc.owner_acceptance --data-dir "$env:BCC_DATA_DIR" --output OWNER_HARDWARE_CERTIFICATION.json
```

Then follow the interactive HW-01…HW-12 checklist. Every case is one of PASS, FAIL, OWNER_ACTION_REQUIRED or NOT_TESTED. Missing credentials or a human-only login checkpoint is OWNER_ACTION_REQUIRED; missing product code is FAIL.

No real payment, government submission, destructive external action or account mutation is permitted by this pack without the product's normal explicit approval gate.
