"""VLM Art Critic: HONEST STATE (audit 31.10, P0 «ложный PASS в Studio»).

This module used to «certify» any input: it never looked at the media (every keyframe was a hard-coded «studio render»
metric), «repaired» a flaw by simply re-reading the same constants without the defect, then raised the score to at least
`target_pass_score` (0.92) and signed a PASS receipt. Nothing was measured or regenerated.

What it is now:
  * metrics are MEASURED only when the caller passes real per-keyframe measurements (dicts: brightness, contrast,
    laplacian_sharpness, non_black_ratio, face_cosine_score, audio_rms_db, artifacts_detected, + `source`); otherwise
    every keyframe is marked `source="synthetic"` and the receipt status is NOT_MEASURED — never PASS;
  * the score is never raised: a receipt carries exactly the aggregate of what was measured;
  * the critic regenerates nothing, so a correction is only RECOMMENDED (`applied: False`) and a flawed input is REJECTED.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


@dataclass
class KeyframeMetrics:
    position_pct: int
    brightness: float
    contrast: float
    laplacian_sharpness: float
    non_black_ratio: float
    face_cosine_score: float
    audio_rms_db: float
    artifacts_detected: list[str] = field(default_factory=list)
    source: str = "synthetic"           # "synthetic" = not measured; anything else names the real measurement


@dataclass
class ArtQCReceipt:
    receipt_id: str
    status: str                         # "PASS" | "REJECTED" | "NOT_MEASURED" (synthetic input never passes)
    score: float                        # 0.0 - 1.0 (target >= 0.90)
    project_id: str
    timestamp_utc: str
    retries_used: int
    non_black_pass: bool
    face_cosine_pass: bool
    sharpness_pass: bool
    artifacts_clean_pass: bool
    keyframes_evaluated: list[dict[str, Any]]
    corrections_applied: list[dict[str, Any]]
    signature: str


class VLMArtCritic:
    """Multi-modal visual inspector and automated self-repair quality gate."""

    def __init__(self, pass_threshold: float = 0.80, target_pass_score: float = 0.92):
        self.pass_threshold = pass_threshold
        self.target_pass_score = target_pass_score      # a goal for the generator, NEVER a floor for the score
        self.max_retries = 2

    def compute_deterministic_metrics(
        self,
        frame_data: Optional[Union[dict[str, Any], bytes]] = None,
        position_pct: int = 50,
        synthetic_defect: bool = False,
    ) -> KeyframeMetrics:
        """Metrics of one keyframe: MEASURED when `frame_data` is a dict of real measurements, else synthetic constants."""
        if isinstance(frame_data, dict) and frame_data.get("source") not in (None, "", "synthetic"):
            return KeyframeMetrics(
                position_pct=int(frame_data.get("position_pct", position_pct)),
                brightness=float(frame_data["brightness"]),
                contrast=float(frame_data["contrast"]),
                laplacian_sharpness=float(frame_data["laplacian_sharpness"]),
                non_black_ratio=float(frame_data["non_black_ratio"]),
                face_cosine_score=float(frame_data["face_cosine_score"]),
                audio_rms_db=float(frame_data.get("audio_rms_db", 0.0)),
                artifacts_detected=[str(a) for a in frame_data.get("artifacts_detected") or []],
                source=str(frame_data["source"]),
            )
        if synthetic_defect:
            return KeyframeMetrics(
                position_pct=position_pct,
                brightness=0.32,
                contrast=0.45,
                laplacian_sharpness=6.2,
                non_black_ratio=0.82,            # Below 0.90 gate
                face_cosine_score=0.68,          # Below 0.78 gate
                audio_rms_db=-32.0,
                artifacts_detected=["extra_fingers", "face_distortion"],
            )

        # High quality studio render metrics
        return KeyframeMetrics(
            position_pct=position_pct,
            brightness=0.58,
            contrast=0.82,
            laplacian_sharpness=12.4,
            non_black_ratio=0.985,               # Cleanly >= 0.90
            face_cosine_score=0.845,             # Cleanly >= 0.78
            audio_rms_db=-14.2,
            artifacts_detected=[],
        )

    def extract_keyframes(
        self,
        media_input: Any,
        synthetic_defect_on_first_try: bool = False,
    ) -> list[KeyframeMetrics]:
        """5 keyframes (0/25/50/75/100%): the caller's real measurements when `media_input` is a list of 5 measurement
        dicts, otherwise synthetic placeholders (marked `source="synthetic"`, which can never PASS)."""
        positions = [0, 25, 50, 75, 100]
        if isinstance(media_input, list) and len(media_input) == len(positions) and all(
                isinstance(m, dict) for m in media_input):
            return [self.compute_deterministic_metrics(m, position_pct=p) for m, p in zip(media_input, positions)]
        keyframes = []
        for p in positions:
            # If defect simulated, trigger on 50% frame
            is_defect = synthetic_defect_on_first_try and p == 50
            m = self.compute_deterministic_metrics(position_pct=p, synthetic_defect=is_defect)
            keyframes.append(m)
        return keyframes

    def calculate_aggregate_score(self, keyframes: list[KeyframeMetrics]) -> float:
        """Combines deterministic metrics and artifact checks into a 0.0 - 1.0 score."""
        if not keyframes:
            return 0.0

        scores = []
        has_critical_defect = False
        for k in keyframes:
            if len(k.artifacts_detected) > 0 or k.non_black_ratio < 0.90 or k.face_cosine_score < 0.78:
                has_critical_defect = True

            # 1. Non-black ratio gate: must be >= 0.90
            nb_score = 1.0 if k.non_black_ratio >= 0.90 else max(0.0, k.non_black_ratio / 0.90)

            # 2. Face cosine gate: target >= 0.78
            fc_score = min(1.0, k.face_cosine_score / 0.82)

            # 3. Sharpness score
            sh_score = min(1.0, k.laplacian_sharpness / 10.0)

            # 4. Artifact penalty: -0.30 per detected defect
            art_penalty = 0.30 * len(k.artifacts_detected)
            k_score = max(0.0, (0.35 * nb_score + 0.35 * fc_score + 0.30 * sh_score) - art_penalty)
            scores.append(k_score)

        raw_avg = float(sum(scores) / len(scores))
        if has_critical_defect:
            raw_avg = min(raw_avg, 0.72)
        return round(raw_avg, 3)

    def inspect_and_certify(
        self,
        project_id: str,
        media_input: Any = None,
        prompt: str = "",
        seed: int = 42,
        controlnet_weight: float = 0.85,
        simulate_initial_flaw: bool = False,
    ) -> ArtQCReceipt:
        """Score what was measured and issue a receipt. Nothing is regenerated here: a flaw yields REJECTED plus a
        RECOMMENDED correction; synthetic (unmeasured) keyframes yield NOT_MEASURED. The score is never raised."""
        if simulate_initial_flaw and not isinstance(media_input, list):
            keyframes = self.extract_keyframes(media_input, synthetic_defect_on_first_try=True)
        else:
            keyframes = self.extract_keyframes(media_input)
        score = self.calculate_aggregate_score(keyframes)
        measured = bool(keyframes) and all(k.source != "synthetic" for k in keyframes)
        retries = 0
        corrections_applied: list[dict[str, Any]] = []
        if score < self.pass_threshold:
            corrections_applied.append({
                "retry": 1,
                "applied": False,           # recommendation only: the critic does not regenerate
                "reason": "score_below_threshold",
                "old_seed": seed,
                "new_seed": seed + 1001,
                "old_cn_weight": controlnet_weight,
                "new_cn_weight": round(min(1.0, controlnet_weight + 0.07), 2),
                "prompt_adjustment": "added: (masterpiece, photorealistic:1.3), negative: (deformed hands, missing digits:1.4)",
            })

        final_score = score                 # audit 31.10: never max(score, target) — that certified anything
        all_nb = all(k.non_black_ratio >= 0.90 for k in keyframes)
        all_fc = all(k.face_cosine_score >= 0.78 for k in keyframes)
        all_clean = all(len(k.artifacts_detected) == 0 for k in keyframes)
        all_sharp = all(k.laplacian_sharpness >= 10.0 for k in keyframes)
        if not measured:
            status = "NOT_MEASURED"
        elif final_score >= self.pass_threshold and all_nb and all_fc and all_clean and all_sharp:
            status = "PASS"
        else:
            status = "REJECTED"

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # An identifier, not a security digest (the receipt signature below is sha256);
        # MD5 here failed the bandit B324 high-severity gate in command-center CI.
        receipt_id = f"QC-{project_id[:8]}-{hashlib.sha256(f'{project_id}:{timestamp}:{final_score}'.encode()).hexdigest()[:8]}"

        payload = {
            "receipt_id": receipt_id,
            "project_id": project_id,
            "timestamp": timestamp,
            "score": final_score,
            "status": status,
            "retries": retries,
        }
        sig = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

        return ArtQCReceipt(
            receipt_id=receipt_id,
            status=status,
            score=final_score,
            project_id=project_id,
            timestamp_utc=timestamp,
            retries_used=retries,
            non_black_pass=all_nb,
            face_cosine_pass=all_fc,
            sharpness_pass=all_sharp,
            artifacts_clean_pass=all_clean,
            keyframes_evaluated=[asdict(k) for k in keyframes],
            corrections_applied=corrections_applied,
            signature=sig,
        )
