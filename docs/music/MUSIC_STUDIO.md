# Music Studio — local music generation

Bossman Music Studio uses a local ACE-Step 1.5 REST service. It does not silently fall back to a cloud provider.

## Why ACE-Step 1.5

ACE-Step 1.5 exposes an asynchronous REST API, supports full-song generation, metadata controls such as duration/BPM/key/time signature, reference/source audio, and local AMD ROCm operation. The Bossman adapter uses the official `POST /release_task -> POST /query_result -> GET /v1/audio` flow.

Environment:
- `BOSSMAN_ACESTEP_URL` — default `http://127.0.0.1:8001`.
- `BOSSMAN_ACESTEP_API_KEY` — optional local API key.

The adapter is deliberately loopback-only. A changed URL cannot turn Music Studio into arbitrary network egress.

## Owner presets

Initial presets:
- Phonk
- Drift Phonk
- Ultra Funk
- Nightcore
- Electro

These are generic musical directions, not requests to clone a named artist.

The owner's uploaded reference tracks may be analyzed locally for broad characteristics such as tempo, energy, arrangement and instrumentation. They are reference material, not permission to reproduce copyrighted recordings or impersonate a specific artist.

## Owner reference profile — initial seed

The first local preference seed is intentionally broad:
- dark/aggressive phonk and ultrafunk;
- strong distorted bass/808;
- cowbell-driven hooks;
- club/electronic energy;
- faster nightcore-like variants;
- preference for immediately recognizable hooks and high energy.

This profile is editable and must evolve from owner ratings of generated tracks rather than assuming every reference is equally preferred.

## Hardware acceptance

On the owner's Ryzen AI Max+ 395 / Radeon 8060S / 128 GB machine:
1. install/start ACE-Step 1.5 using its supported AMD/ROCm path;
2. GET `/api/music/health` must say READY;
3. generate one 60–90 second instrumental phonk track;
4. poll to completed;
5. save through Bossman;
6. verify with ffprobe/full decode;
7. generate at least three variants with fixed prompt and different seeds/settings;
8. owner rates each 1–5;
9. store only generalized preference features, not copyrighted audio, in the owner music profile.

CI may prove the adapter contract but must not claim REAL_LOCAL_MODEL until this hardware run succeeds.
