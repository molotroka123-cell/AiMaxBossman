"""Bossman Art Studio v2.0 «Вундеркинд» & Video Studio 2.0 (7/7 Gates).

Modules:
- photo_engine: DynamicWorkflowCompiler (5 modes) & VoxelToRenderBridge (3D passes)
- video_engine: Wan2.1, Wan2.2, LTX-Video, LivePortrait, RIFE, and run_video_studio_v2_all_7_gates
- motion_timeline: MotionSceneGraph (multi-track keyframes) & AudioBeatAnalyzer (BPM, ASS, 3D LUT)
- vlm_critic: VLMArtCritic & ArtQCReceipt (auto-correction gate)
"""
from __future__ import annotations

from .motion_timeline import (
    AnimatedProperty,
    AudioBeatAnalyzer,
    AudioTrack,
    Easing,
    ImageTrack,
    Keyframe,
    KineticTextTrack,
    LUTPreset,
    MotionSceneGraph,
    ParticleShaderTrack,
    Track,
    VideoTrack,
    Voxel3DTrack,
    evaluate_easing,
)
from .photo_engine import (
    DynamicWorkflowCompiler,
    ModelFamily,
    VoxelToRenderBridge,
    WorkflowMode,
)
from .video_engine import (
    LTXVideoPipeline,
    LivePortraitLipSync,
    QualityGateRecord,
    QualityGatesResult,
    RIFEInterpolator,
    Wan21VACEPipeline,
    Wan22T2VVACEPipeline,
    run_video_studio_v2_all_7_gates,
)
from .vlm_critic import (
    ArtQCReceipt,
    KeyframeMetrics,
    VLMArtCritic,
)

__all__ = [
    # Photo Engine
    "DynamicWorkflowCompiler",
    "VoxelToRenderBridge",
    "ModelFamily",
    "WorkflowMode",
    # Video Engine
    "Wan21VACEPipeline",
    "Wan22T2VVACEPipeline",
    "LTXVideoPipeline",
    "LivePortraitLipSync",
    "RIFEInterpolator",
    "run_video_studio_v2_all_7_gates",
    "QualityGateRecord",
    "QualityGatesResult",
    # Motion Timeline
    "MotionSceneGraph",
    "Track",
    "VideoTrack",
    "ImageTrack",
    "Voxel3DTrack",
    "KineticTextTrack",
    "ParticleShaderTrack",
    "AudioTrack",
    "Keyframe",
    "Easing",
    "evaluate_easing",
    "AnimatedProperty",
    "AudioBeatAnalyzer",
    "LUTPreset",
    # VLM Critic
    "VLMArtCritic",
    "ArtQCReceipt",
    "KeyframeMetrics",
]
