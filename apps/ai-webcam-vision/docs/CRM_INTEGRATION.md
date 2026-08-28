# CRM correlation

Movement alone is not work. The camera supplies physical evidence; the CRM
supplies intent and assignment. Only their combination produces an operational
state.

## Contract

```
GET {AWV_CRM_BASE_URL}/api/bossman/room-context?room_id=dental-1&at=<ISO8601>
Authorization: Bearer {AWV_CRM_TOKEN}      # sent only if a token is configured
```

Response:

```json
{
  "source": "clinic-crm",
  "employee_id": "assistant-7",
  "clinician_id": "doctor-2",
  "shift_active": true,
  "appointment_id": "appt-123",
  "appointment_active": true,
  "planned_service": "endodontics-3-channel",
  "confirmed_service": ""
}
```

Unknown fields are ignored; missing fields fall back to their defaults. Strings
are length-capped on ingest.

## Modes

| `AWV_CRM_KIND` | meaning | `is_mock` | network |
|---|---|---|---|
| `disabled` (default) | no CRM at all | `true` | none |
| `mock` | scripted answers for tests/demos | `true` | none |
| `generic_http` | real clinic CRM | `false` | requires `AWV_CRM_EGRESS_ENABLED=true` |

`CrmContext.available` is the field that matters: `false` means *there is no
CRM answer*, which is not the same as *the CRM says there is no appointment*.
The classifier refuses to claim `CLINICAL_WORK`, `PREP`, `TURNOVER` or
`STAFF_NONCLINICAL` without an available CRM context, and every stored
observation records `crm_available` and `crm_is_mock`.

## State fusion

| Evidence + context | State |
|---|---|
| frame at baseline, no motion | `EMPTY` |
| appointment active + chair evidence + work-zone motion | `CLINICAL_WORK` |
| appointment active + chair evidence, no work motion | `IDLE_OCCUPIED` |
| appointment active + room activity, chair at baseline | `PREP` |
| no active appointment + chair not at baseline | `TURNOVER` |
| shift active + room activity + chair at baseline | `STAFF_NONCLINICAL` |
| movement without chair evidence | `TRANSIT` |
| chair evidence with no CRM corroboration | `IDLE_OCCUPIED` (low confidence) |

A new state must repeat `AWV_DEBOUNCE_SAMPLES` times before it takes effect, so
one noisy frame cannot flip the room into `CLINICAL_WORK`.

## Procedure label

Precedence, with provenance always stored:

1. CRM `confirmed_service` → confidence 1.0, provenance `crm_confirmed`;
2. active appointment `planned_service` → confidence 0.85, provenance `crm_planned`;
3. otherwise `unknown`, confidence 0.0, provenance `unknown`.

A future local VLM may add evidence, never the label on its own. The camera
does not know what procedure is being performed and this application never
claims that it does.

## Verification status

The CRM boundary is tested against the mock and against the egress guard.
**REAL CRM: NOT TESTED** — no clinic CRM endpoint exists yet. Do not mark the
real integration done from mock runs.
