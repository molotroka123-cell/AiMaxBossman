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

Studio real path (01.11): a measurement is accepted only with its provenance, otherwise the receipt is REFUSED:
  * every keyframe carries the sha256 of its decoded frame (`frame_sha256`) and of the video (`video_sha256`, one video);
  * five identical metric rows, five identical frames, or the old hard-coded placeholder values are constants, not
    measurements;
  * a PASS certifies a file, so it needs `media_path`: the critic hashes that file and it must equal `video_sha256`;
  * `face_cosine_score=None` means no face model ran (a shot without people): the face gate is NOT APPLICABLE, it is
    neither passed nor scored; `motion_to_next` (mean |frame diff|) must reach MOTION_MIN somewhere (codec noise on a
    still is ~1e-4), a still image never passes.
Measurements come from `bcc.direct_gen.video_measure.measure_video` (ffmpeg-decoded frames + numpy).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
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
    face_cosine_score: Optional[float]  # None = no face model ran (shot without people): face gate not applicable
    audio_rms_db: float
    artifacts_detected: list[str] = field(default_factory=list)
    source: str = "synthetic"           # "synthetic" = not measured; anything else names the real measurement
    frame_sha256: str = ""              # sha256 of the decoded frame the numbers were measured on
    video_sha256: str = ""              # sha256 of the video file that frame was decoded from
    motion_to_next: Optional[float] = None   # mean |difference| to the next decoded frame (luma levels)


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
    motion_pass: Optional[bool] = None  # None = no motion measured
    refusal: Optional[str] = None       # why measurements were not accepted (status REFUSED)
    media_sha256: Optional[str] = None  # the file a PASS is bound to


# The pre-31.10 hard-coded "measurements" (studio render / simulated defect). Seeing them again means constants were
# passed off as a measurement: refused.
_PLACEHOLDER_ROWS = ((0.58, 0.82, 12.4, 0.985, 0.845), (0.32, 0.45, 6.2, 0.82, 0.68))
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# mean |frame diff| (luma levels) below which "motion" is codec noise on a still (same floor as video_measure)
MOTION_MIN = 0.05


def _sha256_of(path: Union[str, Path]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
            face = frame_data.get("face_cosine_score")
            motion = frame_data.get("motion_to_next")
            return KeyframeMetrics(
                position_pct=int(frame_data.get("position_pct", position_pct)),
                brightness=float(frame_data["brightness"]),
                contrast=float(frame_data["contrast"]),
                laplacian_sharpness=float(frame_data["laplacian_sharpness"]),
                non_black_ratio=float(frame_data["non_black_ratio"]),
                face_cosine_score=None if face is None else float(face),
                audio_rms_db=float(frame_data.get("audio_rms_db", 0.0)),
                artifacts_detected=[str(a) for a in frame_data.get("artifacts_detected") or []],
                source=str(frame_data["source"]),
                frame_sha256=str(frame_data.get("frame_sha256") or ""),
                video_sha256=str(frame_data.get("video_sha256") or ""),
                motion_to_next=None if motion is None else float(motion),
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
            face = k.face_cosine_score
            if len(k.artifacts_detected) > 0 or k.non_black_ratio < 0.90 or (face is not None and face < 0.78):
                has_critical_defect = True

            # 1. Non-black ratio gate: must be >= 0.90
            nb_score = 1.0 if k.non_black_ratio >= 0.90 else max(0.0, k.non_black_ratio / 0.90)

            # 3. Sharpness score
            sh_score = min(1.0, k.laplacian_sharpness / 10.0)

            # 4. Artifact penalty: -0.30 per detected defect
            art_penalty = 0.30 * len(k.artifacts_detected)
            if face is None:     # no face model ran: the face term is not applicable, the other two are re-weighted
                base = (0.35 * nb_score + 0.30 * sh_score) / 0.65
            else:                # 2. Face cosine gate: target >= 0.78
                base = 0.35 * nb_score + 0.35 * min(1.0, face / 0.82) + 0.30 * sh_score
            scores.append(max(0.0, base - art_penalty))

        raw_avg = float(sum(scores) / len(scores))
        if has_critical_defect:
            raw_avg = min(raw_avg, 0.72)
        return round(raw_avg, 3)

    @staticmethod
    def provenance_refusal(keyframes: list[KeyframeMetrics], media_path: Union[str, Path, None]) -> Optional[str]:
        """Why measured keyframes are NOT accepted as measurements (None = accepted). See the module doc."""
        if any(not _SHA256.fullmatch(k.frame_sha256) or not _SHA256.fullmatch(k.video_sha256) for k in keyframes):
            return "measurement_provenance_missing: every keyframe needs frame_sha256 and video_sha256"
        videos = {k.video_sha256 for k in keyframes}
        if len(videos) != 1:
            return "mixed_videos: keyframes come from different files"
        rows = [(k.brightness, k.contrast, k.laplacian_sharpness, k.non_black_ratio, k.face_cosine_score)
                for k in keyframes]
        if any(r == p for r in rows for p in _PLACEHOLDER_ROWS):
            return "placeholder_constants: the pre-31.10 hard-coded values are not a measurement"
        if len(keyframes) > 1 and len(set(rows)) == 1:
            return "constant_measurements: every keyframe has identical metrics"
        if len(keyframes) > 1 and len({k.frame_sha256 for k in keyframes}) == 1:
            return "identical_frames: every keyframe is the same decoded frame (a still image)"
        if media_path is not None:
            try:
                actual = _sha256_of(media_path)
            except OSError as exc:
                return f"media_unreadable:{type(exc).__name__}"
            if actual != next(iter(videos)):
                return "media_sha256_mismatch: the measurements were not taken on this file"
        return None

    def inspect_and_certify(
        self,
        project_id: str,
        media_input: Any = None,
        prompt: str = "",
        seed: int = 42,
        controlnet_weight: float = 0.85,
        simulate_initial_flaw: bool = False,
        media_path: Union[str, Path, None] = None,
    ) -> ArtQCReceipt:
        """Score what was measured and issue a receipt. Nothing is regenerated here: a flaw yields REJECTED plus a
        RECOMMENDED correction; synthetic (unmeasured) keyframes yield NOT_MEASURED; measurements without provenance,
        constants, or a would-be PASS not bound to `media_path` yield REFUSED. The score is never raised."""
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
        faces = [k.face_cosine_score for k in keyframes if k.face_cosine_score is not None]
        all_fc: Optional[bool] = all(f >= 0.78 for f in faces) if faces else None     # None = not applicable
        all_clean = all(len(k.artifacts_detected) == 0 for k in keyframes)
        all_sharp = all(k.laplacian_sharpness >= 10.0 for k in keyframes)
        motions = [k.motion_to_next for k in keyframes if k.motion_to_next is not None]
        motion_ok: Optional[bool] = any(m >= MOTION_MIN for m in motions) if motions else None
        refusal = self.provenance_refusal(keyframes, media_path) if measured else None
        gates = (final_score >= self.pass_threshold and all_nb and all_fc is not False and all_clean and all_sharp
                 and motion_ok is not False)
        if not measured:
            status = "NOT_MEASURED"
        elif refusal:
            status = "REFUSED"
        elif not gates:
            status = "REJECTED"
        elif media_path is None:
            status, refusal = "REFUSED", "unbound_measurements: a PASS needs media_path (the measured file)"
        else:
            status = "PASS"
        media_sha = keyframes[0].video_sha256 if status == "PASS" else None

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
            "refusal": refusal,
            "media_sha256": media_sha,
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
            motion_pass=motion_ok,
            refusal=refusal,
            media_sha256=media_sha,
        )
