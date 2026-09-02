# Learning Case: F-006-F-007-untrusted-marker

## Metadata
MODEL: claude-fable-5-1
AGENT: agent-B+lead
START_SHA: 3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
END_SHA: 760ac6d
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:bossman-core/tests/test_secrem_f006_f007_untrusted.py
CONFIDENCE: 0.85
TAGS: {"domain": "security", "bug_class": "prompt_injection", "component": "bossman.context", "severity": "MEDIUM", "security_boundary": "context"}
FINDINGS: F-006, F-007

## Task
untrusted-data marker applied inconsistently (retrieved as system; exec/write output unmarked)

## Symptom
Retrieved memory/RAG went in as role=system without a marker; run/git/ffmpeg output was returned to the model as plain text.

## Reproduction
- bossman-core/tests/test_secrem_f006_f007_untrusted.py

## Evidence
- context.py appended {'role':'system', ...pulled} (pre-fix)
- runner filtered by `tool.rights in ('read','send')` (pre-fix)
- post-fix: retrieved block role=user + RETRIEVED_DATA_HEADER; every tool except log/search_journal gets EXTERNAL_DATA_HEADER

## Hypotheses considered
- marker keyed on tool rights instead of on 'may carry foreign text' (root cause)
- system role chosen for KV-cache stability

## Rejected hypotheses + why
- keep role=system but add header (system role still carries policy weight in most providers)

## Root cause
The data/instruction boundary was applied by category guess rather than by provenance.

## Relevant code paths
- bossman-core/bossman/context.py:ContextBuilder.build
- bossman-core/bossman/runner.py:_call_tool
- bossman-core/bossman/runner.py:INTERNAL_SAFE_TOOLS

## Fix strategy
Provenance-based: everything not authored by the agent's own journal is data; retrieved block becomes user-role with header.

## Alternatives considered
- per-tool flag (drifts as tools are added)

## Why this fix was chosen
Default-deny on trust: new tools are data unless explicitly internal.

## Files changed
- bossman-core/bossman/context.py
- bossman-core/bossman/runner.py

## Tests added
- bossman-core/tests/test_secrem_f006_f007_untrusted.py

## Original reproduction after fix
marked

## Adversarial variants
- injected 'SYSTEM: ignore all rules' in retrieved text never appears in a system message
- exec tool output prefixed; journal tool not

## Regression
bossman-core focused 247 passed (context block order test still green)

## Fresh external verification
pytest over real ContextBuilder output and runner._call_tool with stubbed DB.

## Generalizable lessons
- Mark by provenance, not by permission class.

## Teach local model
- Recognize: role=system built from retrieved content
- Prefer: allowlist of internal tools; everything else is data
