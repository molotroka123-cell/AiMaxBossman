# WebDesigner — Open Source / Dataset Source Ledger

Snapshot date: 2026-09-24. Refresh all remote SHAs before adoption.

The rule is **reuse ideas and stable components, not whole products by default**.

## Software candidates

| Source | Snapshot | License observed | Decision | Why |
|---|---|---|---|---|
| onlook-dev/onlook | `423e2e924366419e418ee049093872d535eea41a` | Apache-2.0 | ADAPTER / REFERENCE | Mature visual-first React/Next/Tailwind editor patterns, branch/checkpoint/preview ideas |
| attentiondotnet/open-design | `c3712b738866370c473acc34ab6600af2656d46b` | Apache-2.0 | REFERENCE / SELECTIVE REUSE | Local-first agent/design-system patterns |
| OpenCoworkAI/open-codesign | `26c84984809b82f816eb9907a10eb706718f14af` | MIT | REFERENCE / SELECTIVE REUSE | Artifact-first design sessions, visual parity loop ideas |
| uselayout/app | `9b7770a0cf3eac052a1efcd3658baacfa829aceb` | AGPL-3.0 | EXTERNAL/REFERENCE ONLY unless legal decision | Excellent design-system extraction patterns but copyleft boundary matters |
| RaDeleon/Build-Beautiful-Sites | `890d63634cdecbf8efde4723ad2977c77622baee` | custom source-available license | REFERENCE / OWNER SKILL, DO NOT RESELL/BUNDLE blindly | Strong art-direction + rendered-QA workflow; license restricts selling/bundling the Skill |

URLs:
- https://github.com/onlook-dev/onlook
- https://github.com/attentiondotnet/open-design
- https://github.com/OpenCoworkAI/open-codesign
- https://github.com/uselayout/app
- https://github.com/RaDeleon/Build-Beautiful-Sites

Before copying code:
1. fetch exact remote;
2. record SHA;
3. read LICENSE + NOTICE;
4. map copied files;
5. retain attribution where required;
6. run dependency/security review;
7. prefer sidecar/adaptor for incompatible licenses.

## Training datasets / models

### WebSight v0.2
Source: https://huggingface.co/datasets/HuggingFaceM4/WebSight

Observed dataset card:
- 1,922,671 examples;
- screenshot + HTML/Tailwind + generated idea;
- CC-BY-4.0 dataset license;
- terms ask users to comply with source licenses and disclose dataset use when releasing a trained model/application.

Decision: **CURATED SUBSET**, not "train on all 1.92M first".

Use it to teach web-layout/code grammar, then rely on higher-quality golden examples for taste.

### Design2Code-18B-v0
Source: https://huggingface.co/SALT-NLP/Design2Code-18B-v0

Observed:
- specialized screenshot-to-code model artifact;
- Apache-2.0 model card metadata;
- tied to Design2Code/WebSight research.

Decision: **BENCHMARK / TEACHER REFERENCE**, not automatic Bossman base.

Use it to understand screenshot-to-code failure modes and possibly as an auxiliary teacher/evaluator.

### Vision2Web
Source: https://vision2web-bench.github.io/

Decision: **EXTERNAL EVALUATION REFERENCE**.

Use benchmark methodology/results as an external reality check. Do not train on its hidden evaluation examples.

## What we do not ingest

- arbitrary proprietary production websites scraped without rights;
- client designs without permission for training;
- hidden benchmark answers;
- paid template source without a training grant;
- source-available skills whose license conflicts with product redistribution;
- generated examples that fail render/build/quality gates.

## Provenance record per example

Every training record has:
- `source_type`;
- source URL/ref if external;
- license;
- source revision;
- whether redistribution/training is permitted;
- transformation lineage;
- screenshot hash;
- code hash;
- evaluator version;
- split assignment;
- duplicate cluster ID.

No provenance -> no training.
