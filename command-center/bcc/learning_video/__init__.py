"""Video learning pipeline (offline core): transcript -> labelled chunks -> dedupe -> cited claims -> control questions.

The core (transcript/dedupe/claims/evaluate/pipeline) never touches the network. stream_signals (SponsorBlock,
Return YouTube Dislike) and fetch_control (yt-dlp client chain + circuit breaker) do, but only when a caller passes
them a runner/http_get or uses their defaults explicitly -- tools/youtube_learning_fetch.py is that caller. A live run supplies the transcript file and an extractor callable
(local model); this package only validates what comes back. A claim is never "learned": it starts UNVERIFIED and
only a control-question evaluation against a no-knowledge baseline and a distractor arm may say IMPROVED.
"""
from .transcript import Cue, Chunk, parse_vtt, chunk_cues, source_label, transcript_quality, norm_keep_len
from .dedupe import dedupe_chunks, dedupe_claims
from .claims import Claim, validate_claim, claims_from_model_json, rule_extractor, redact_pii, assert_loopback
from .evaluate import Question, evaluate_questions, retrieve
from .pipeline import run_pipeline
from .stream_signals import Segment, fetch_sponsor_segments, strip_segments, fetch_votes, source_prior, rank_sources
from .fetch_control import FetchController, Breaker, classify as classify_fetch

__all__ = [
    "Cue", "Chunk", "parse_vtt", "chunk_cues", "source_label", "transcript_quality", "norm_keep_len",
    "dedupe_chunks", "dedupe_claims", "Claim", "validate_claim", "claims_from_model_json", "rule_extractor",
    "redact_pii", "assert_loopback", "Question", "evaluate_questions", "retrieve", "run_pipeline",
    "Segment", "fetch_sponsor_segments", "strip_segments", "fetch_votes", "source_prior", "rank_sources",
    "FetchController", "Breaker", "classify_fetch",
]
