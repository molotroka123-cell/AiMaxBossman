"""Upstream pin and API-contract status. Data, not behaviour.

Upstream is NOT vendored and NOT a dependency: ``bcc.jev`` is a thin adapter over
Bossman's own browser runtime. Two small ideas are re-implemented (the strict
choice validator and the operation-specific target space); their MIT notice is
kept in ``NOTICE-jev-ultrafast.txt`` next to this file.
"""
from __future__ import annotations

UPSTREAM_REPO = "https://github.com/browser-use/jev-ultrafast"
# `git ls-remote` of refs/heads/main (== HEAD) on 2026-09-22 from the cloud session.
UPSTREAM_COMMIT = "1231850a0bf1a0c0341fe408ef1668dbbfdfac46"
UPSTREAM_LICENSE = "MIT"
# sha256 of LICENSE and jev_ultrafast/model.py at UPSTREAM_COMMIT (raw.githubusercontent.com).
UPSTREAM_LICENSE_SHA256 = "5afa3d97bf1e6f998d587fa101e3fe0d0c416840b7fe77bcd6ae97690a334631"
UPSTREAM_MODEL_PY_SHA256 = "a85ada8458d23642c5c8a4095acf0565e6268f81dcda26f6308942b653e9c9f3"

# The request/response shape below was read from upstream SOURCE (model.py and its
# tests) at UPSTREAM_COMMIT. It was NOT confirmed against the live API: the cloud
# proxy blocks docs.typesafe.ai and defapi.org, and no key exists here. The owner
# runner's ``--execute`` probe turns this into VERIFIED or CONTRACT_MISMATCH.
CONTRACT_STATUS = "CONTRACT_UNVERIFIED"
CONTRACT_SOURCE = f"{UPSTREAM_REPO}/blob/{UPSTREAM_COMMIT}/jev_ultrafast/model.py"
DEFAPI_DOC = "https://defapi.org/api/model/en/typesafe/jev-1.13"
DEFAPI_STATUS = "BLOCKED"          # 403 from the cloud egress proxy on 2026-09-22

# ASSUMED schema (CONTRACT_UNVERIFIED):
#   request  = {"model": str, "state": {...}, "questions": {name: {"type": "choice",
#               "criteria": {id: description}, "instructions": {...}}}}
#   response = {"model": str, "answers": {name: {"choice": id, "confidence": 0..1,
#               "probabilities": {id: 0..1 for every offered id, sum≈1}}},
#               "usage": {...optional token counts...}}
# Only the "choice" question type is used (the only type evidenced by upstream
# source). Scores and booleans are asked as bucketed choices so no unverified
# question type is relied on. Any deviation fails closed (JevInvalidResponse).
