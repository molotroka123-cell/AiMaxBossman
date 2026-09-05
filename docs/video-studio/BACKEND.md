# Video Studio local media backend

This document describes backend behavior, not completion of the entire editor specification. UI, canonical task admission and project revisions have separate owners and tests.

## Verified host, 2026-09-05

- Windows FFmpeg/ffprobe `8.1-full_build-www.gyan.dev`, located through PATH. `ffmpeg -filters` includes libass, drawtext, overlay, xfade, curves, lut3d, loudnorm, afftdn, sidechaincompress, deshake, vidstab, rubberband and whisper.
- NVIDIA RTX 4060 Laptop GPU, 8188 MiB, driver 580.88. Actual three-frame `640x360` lavfi encodes to the null muxer passed with `libx264`, `h264_nvenc`, `hevc_nvenc`, `av1_nvenc`. The same hardware encoders rejected `128x128` as below their minimum dimensions. Encoder enumeration alone is never reported as operational proof.
- The project's CI Python 3.11 has no OpenCV, NumPy, Torch or Python Whisper packages. Existing `C:\Python314\python.exe` has OpenCV 4.13.0, NumPy 2.4.3 and Pillow 12.2.0. Its Lucas–Kanade tracker passed a real moving-pattern video test. Tracking workers are explicitly selected by host configuration, not by model-supplied executable paths.
- Local multilingual Whisper base weights were downloaded from the [whisper.cpp model repository](https://huggingface.co/ggerganov/whisper.cpp/tree/main), linked by the [upstream model documentation](https://github.com/ggml-org/whisper.cpp/blob/master/models/README.md). `ggml-base.bin`: 147,951,465 bytes; SHA256 `60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe`. The digest matches the repository API's published LFS object ID.
- Weights remain outside Git in `.audit-work/models/ggml-base.bin`. Local Windows SAPI generated the TEST recording `.audit-work/asr/english-test.wav` (SHA256 `299f61e9a9374de8b080db23b89b189d69169220c0799749a581c9ef55da1aa7`). Actual CPU transcription returned “This is a local video editing test.” and “Add subtitles and keep the original sound.” in two timed segments, in 3.844 seconds. Audio was not uploaded. This verifies English inference, not Russian transcription quality: installed SAPI voices are English only.

## Interfaces and safety boundaries

`MediaLibrary(root).import_file(path,name=None)` accepts an attachment path already approved by the service. It streams into content-addressed storage, hashes the complete file, probes actual streams and returns canonical Media metadata. The library itself grants no filesystem permission. `resolve(media)` checks containment, filename/digest binding and complete file content. Original media is immutable.

Supported magic-recognised input containers are MOV/MP4, Matroska/WebM, AVI, WAV, FLAC, Ogg, MP3, PNG and JPEG. URL inputs, playlists, concat scripts and other unrecognised formats are rejected before probing. Child commands use argv only, restricted local file protocols and forced demuxers; MOV external data references are disabled. Unsupported formats need a separately reviewed importer.

`prepare(media)` creates disk-cached thumbnail, waveform and 640-pixel proxy artifacts. Cache paths include source SHA256 and recipe version. Long original media is streamed by FFmpeg; Python never materialises it in RAM. Cancellation waits for file workers, kills and reaps subprocesses, and removes partial output. The library does not introduce a scheduler, cloud budget or permission system.

`render_project(project,root,output_path,options,progress)` compiles the immutable snapshot into one filtergraph. Preview uses this same compiler with a smaller output size/range. Export publication is exclusive, after writable-file fsync and independent verification, using a same-volume hard link. Existing artifacts cannot be replaced. A crash after publication is still a task-recovery concern for the canonical TaskEngine.

`verify_output(path,expected)` checks stream metadata, expected duration/resolution/frame rate, complete decode with `-xerror`, three actual sampled frames and A/V start offset. Sample frame digests and average RGB values are returned. A/V start agreement is not proof of lip sync. `analyse_media` streams scene, black and silence diagnostics and peak measurements; intentional black/silence do not automatically fail a project. At 100,000 collected intervals/events it explicitly marks results incomplete.

## Implemented renderer semantics

- Integer microsecond project/source times and rational rates; non-destructive source trim; sequential clips, multiple video/audio tracks, gaps, mute/solo, positioned overlays and nested sequences. Nesting cycles fail.
- Clip speed, piecewise speed ramp, short reverse and freeze; ordinary decoded CFR output; source/timeline times remain distinct. Reverse inputs over 30 seconds are rejected until a bounded reverse-proxy path is available. No unrestricted whole-video reverse buffer.
- Transform position/scale/rotation/opacity and crop **edge fractions**. Keyframes support linear, hold, ease-in, ease-out and ease-in-out interpolation. Numeric expressions are generated internally, never accepted as arbitrary user FFmpeg expressions. Rotation currently keeps the transformed clip's fixed bounds.
- Color brightness/contrast/saturation/gamma, RGB shadow/midtone/highlight balance, curves, validated 2–33 point 3D LUT cubes; chroma key; rectangle/circle alpha masks; overlap alpha-fade transitions; normal/multiply/screen/addition/overlay/difference blending; local `deshake` stabilization. Two-pass vidstab is installed but is not wired as this effect.
- Actual timed adjustment clips process the already-composited lower tracks. They use `adjustment:true`, `freeze_duration`, an adjustment track and image effects. Audio effects are rejected on adjustment clips.
- Clip and track gain/pan, volume automation, fades, equalizer, compressor, limiter, FFT denoise, one-pass loudness normalization, mixed audio tracks and explicit sidechain-track ducking. Detached audio respects `audio_disabled`/`audio_only`, with a measured no-double-audio regression. Final mix is stereo 48 kHz with explicit silence in gaps.
- Plain-text titles with font family, size, color/alignment and ASS rendering; editable caption records burned through libass; SRT/VTT parsing and export. ASS control characters in user text are inert. Title animation uses the same transform keyframes.
- Named output defaults source/youtube/reels/square; dimensions, rational fps, encoder, CRF, bitrate, audio bitrate and range options. H.264/H.265 software, NVIDIA H.264/HEVC/AV1 and selected other compiled encoders are allowlisted; profile-specific encoder errors remain visible, not silently reported as hardware success.
- `mode:'stream_copy'` concatenates compatible, contiguous, unmodified complete MOV/MP4 clips. It compares actual codec/extradata signatures and rejects transforms, partial source cuts, different parameters and timeline mixing. Its manifest is internally generated from hash-verified files. Failed copy verification directs the caller to normalized export.

## Optional analysis execution

`transcribe(path,model_path=host_approved_path,language='auto')` invokes the real FFmpeg whisper.cpp filter on CPU. No automatic model download, cloud fallback or synthetic transcript occurs during a task. `BOSSMAN_VIDEO_ASR_MODEL` allows capabilities to report an installed host-configured model; task/service wiring must supply that same approved path and the existing resource admission.

`track_object(path,box,start=0,end=None,python_executable=host_approved_runtime)` uses bounded streaming pyramidal Lucas–Kanade optical flow. `BOSSMAN_VIDEO_CV_PYTHON` can select the installed CV interpreter. Results contain timed box coordinates and feature-retention confidence. Textureless/lost regions fail explicitly. This is translation tracking, not object identity, segmentation, occlusion recovery or scale estimation. Segments are limited to ten minutes. The controller's cancellation reaps the worker.

## Explicit limits

HDR PQ/HLG inputs are rejected pending an explicitly tested tone-mapping profile; tagging SDR alone is not presented as conversion. Unlabelled SDR sources use FFmpeg's decoded interpretation and are tagged BT.709 on output; this is not a calibrated color-management claim. Complex masks, arbitrary transition families, full multicam analysis, semantic rough cutting, speech separation, optical-flow retiming, generative background removal/upscale and generation providers remain separate features. Presence of unrelated model-cache directory names is not proof of a usable generation provider. Python calls do not by themselves establish a UI control or canonical job route.

## Reproducible tests

From `command-center`, set PYTHONPATH to its absolute directory and run:

```powershell
& 'C:\AiMaxBossman-claude-bossman-control-v03-43igbk\.venv-ci311\Scripts\python.exe' -m pytest tests/test_video_studio_render.py -q --tb=short --basetemp=C:\AiMaxBossman-video-studio\.audit-work\render-final
```

Tests generate explicitly synthetic local TEST fixtures. Evidence includes sampled red→blue concatenation, preview/range pixel agreement, cyan output from an inverted LUT, timed adjustment pixels, actual mixed/detached audio peaks, captions, freeze/ramp/nesting, successful decode, unsafe input rejection, process cancellation, compatible stream copy and real moving-pattern tracking. The downloaded-model ASR test skips explicitly on hosts without the optional model/SAPI fixture; it does not download anything in CI.
