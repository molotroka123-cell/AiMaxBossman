"""Video learning pipeline (offline core): transcript -> labelled chunks -> dedupe -> cited claims -> control questions.

Nothing here touches the network or YouTube. A live run supplies the transcript file and an extractor callable
(local model); this package only validates what comes back. A claim is never "learned": it starts UNVERIFIED and
only a control-question evaluation against a no-knowledge baseline and a distractor arm may say IMPROVED.
"""
from .transcript import Cue, Chunk, parse_vtt, chunk_cues, source_label, transcript_quality, norm_keep_len
from .dedupe import dedupe_chunks, dedupe_claims
from .claims import Claim, validate_claim, claims_from_model_json, rule_extractor, redact_pii, assert_loopback
from .evaluate import Question, evaluate_questions, retrieve
from .pipeline import run_pipeline

__all__ = [
    "Cue", "Chunk", "parse_vtt", "chunk_cues", "source_label", "transcript_quality", "norm_keep_len",
    "dedupe_chunks", "dedupe_claims", "Claim", "validate_claim", "claims_from_model_json", "rule_extractor",
    "redact_pii", "assert_loopback", "Question", "evaluate_questions", "retrieve", "run_pipeline",
]
