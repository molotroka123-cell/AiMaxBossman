"""Bossman War & Procurement Terminal: isolated app. Public procurement + conflict context + feasibility for a future company.

Hard rules (enforced in code, not only in docs):
  * DEMO data is always marked demo=True and never counted as real;
  * unknown critical facts => REVIEW_REQUIRED, never PASS; fit_score is NOT a probability of winning;
  * no autonomous bid submission, counterparty contact or purchase of controlled goods;
  * every write needs an owner/compliance role and goes through the approval ledger + hash-chained audit.
"""
VERSION = "0.1.0"
API_VERSION = "v1"
