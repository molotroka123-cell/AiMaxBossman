# Authenticated recovery snapshots

TaskJournal now writes schema 3 snapshots signed by the journal signer. The
signature covers all durable fields, including pending/started state, in-flight
intent, plan digest, steps, task identity and execution binding. Loading validates
this envelope before constructing steps or making a replay decision. Completion
receipts retain their independent signatures.

## Migration and rollback

Schema 2 and unsigned snapshots are deliberately rejected, including apparently
pending snapshots: they cannot establish that an irreversible effect never began.
Do not auto-sign old files, clear in-flight flags, or delete a journal to retry.
Preserve the original journal and signing key. Reconcile actual effects against an
independent external record with the owner, then create a new mission only for
proven outstanding work under fresh authorization. No automatic migration or
reconciliation tool is provided by this patch.

Quiesce runners before upgrading. Newly created missions use schema 3 immediately;
existing schema 3 missions resume normally with the original signing key. Missing
or changed keys fail closed. A runtime rollback must retain this reader/writer
hardening; older readers do not enforce the snapshot signature and must not resume
these missions. This patch does not turn off any existing release gates.

## Evidence and limits

The regression fixture kills a separate process after fsync of a non-idempotent
append and before a receipt. Seven mutations of the stored journal must produce
no additional external effect. Untouched ambiguous attempts remain blocked across
repeated fresh processes, while a completed checkpoint resumes the remaining work
exactly once in the fixture. Legacy input, signer substitution and task transplant
are rejected without rewriting the stored file.

Authentication detects changed bytes, not restoration of a previously valid
whole snapshot. Protection against complete storage rollback needs an independent
monotonic anchor or external effect reconciliation; it is not delivered here.
Possession of the signing key or trusted-process code execution is outside this
integrity boundary. This patch does not establish general exactly-once execution.
