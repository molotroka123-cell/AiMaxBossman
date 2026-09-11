"""
Evidence normalization and post-state verification for Bossman Hybrid OSS.
CRITICAL INVARIANT: External runtime success claims MUST NEVER directly mark
a Bossman task as completed without independent post-state verification.
"""

from __future__ import annotations
import hashlib
import json
import logging
from typing import Any, Callable, Dict, Optional
from .capabilities import (
    EffectCorrelation,
    EvidenceCandidate,
    Observation,
    StateVerificationFailedError,
)

logger = logging.getLogger("bcc.hybrid.evidence")


class EvidenceNormalizer:
    """Normalizes raw external observations into strictly unverified Bossman evidence candidates."""

    @staticmethod
    def calculate_digest(payload: Dict[str, Any]) -> str:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def normalize_observation(
        cls,
        observation: Observation,
        evidence_type: str,
    ) -> EvidenceCandidate:
        """
        Normalize an external engine observation into an unverified evidence candidate.
        Note: verified_by_bossman and is_completion_proof are ALWAYS initialized to False!
        """
        digest = cls.calculate_digest(observation.raw_data)
        return EvidenceCandidate(
            correlation_id=observation.correlation_id,
            evidence_type=evidence_type,
            digest=digest,
            payload=observation.raw_data,
            source_runtime=observation.source_runtime,
            verified_by_bossman=False,
            is_completion_proof=False,
        )

    @classmethod
    def verify_candidate_against_bossman_truth(
        cls,
        candidate: EvidenceCandidate,
        correlation: EffectCorrelation,
        post_state_verifier: Callable[[Dict[str, Any]], bool],
    ) -> EvidenceCandidate:
        """
        Apply Bossman's independent verification against observed real-world post-state.
        External claim of 'ok=True' or 'success' is explicitly ignored.
        Only the verified post_state_verifier truth controls the outcome.
        """
        passed = False
        try:
            passed = bool(post_state_verifier(candidate.payload))
        except Exception as e:
            logger.error(
                "Post-state verifier raised an exception for correlation %s: %s",
                correlation.correlation_id,
                e,
            )
            passed = False

        if not passed:
            raise StateVerificationFailedError(
                f"Bossman independent post-state verification failed for {correlation.correlation_id}. "
                "External engine claimed success, but expected state change was not observed."
            )

        # Return a verified candidate
        return EvidenceCandidate(
            correlation_id=candidate.correlation_id,
            evidence_type=candidate.evidence_type,
            digest=candidate.digest,
            payload=candidate.payload,
            source_runtime=candidate.source_runtime,
            verified_by_bossman=True,
            is_completion_proof=True,
        )
