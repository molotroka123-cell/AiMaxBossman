"""EVAL500 (TZ 2.5 M5 / T4): 500 synthetic owner scenarios, production-path runner, free judge, baseline receipt.

Layout:
  schema.py        categories table, scenario schema/validator, split + sha256 of the sealed holdout
  holdout_guard.py refuses prompts that would leak a holdout scenario into a coder/swarm context
  generate.py      drafts with free Nemotron Ultra (OpenRouter :free), dedupe, validate, split 350/150
  runner.py        real chat-threads path of the live backend, approvals auto-rejected, threads purged
  judge.py         deterministic checks first, then Nemotron :free, Gemini Flash re-checks 10 %
  report.py        receipt.json + failure_clusters.json per category
"""
