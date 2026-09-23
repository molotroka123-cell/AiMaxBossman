# SKILLS FINAL AUDIT — 2026-09-23

Scanned tree: `C:\Users\asd\Bossman\wt-fix-crlf0923`.

**How the data was gathered:**
- Every SKILL.md outside node_modules was found and hashed with SHA256 (PowerShell `Get-FileHash`).
- The registries read were:
  - `command-center/bcc/skills_catalog/*/*/provenance.json`, loaded by `bcc/v2/skill_catalog.py`;
  - `docs/skills/voltagent-agent-skills.lock.json`, pack `voltagent-agent-skills-20260907`, target_base `b57b4ce9`.
- Delivery evidence was taken from `OR0923-bd2fe23d/learning/record-*.json` and `bossman-core/bossman/apprentice/local_sidecar.py`.

**Rules for the verdict columns:**
- **DELIVERED_TO_MODEL = YES** only where a record or test shows the skill in the student context.
- **MODEL_USED_IT** is UNKNOWN everywhere: no trace ties a student action to skill text.
- **VERIFIED_BENEFIT** is NOT_MEASURED everywhere: no skills-on vs skills-off A/B exists.

**Totals:** 48 SKILL.md files.
- 11 in the imported catalog (`command-center/bcc/skills_catalog`).
- 28 in the SkillLibrary (`.agents/skills`).
- 8 Claude Code developer skills (`.claude/skills`).
- 1 in bossman-core (`bossman-core/skills/compact`).

## 1. Imported catalog — `command-center/bcc/skills_catalog` (the student methodology path)

### How the catalog path works
- **Loader:** `bcc/v2/skill_catalog.py`.
  - Status starts at `UNVERIFIED`. Only an owner decision sets `VERIFIED`; import never does.
  - If the SHA256 no longer matches provenance, or provenance is missing, the skill becomes `QUARANTINED`.
  - A policy-poison scan runs on load.
- **Selection:** by triggers, at most 3 skills, through `features/skills.py:skills_for_task`, called from `features/coding_tasks.py:403-418`.
- **Prompt:** `local_sidecar.py:472-483` adds the skills as "Methodology skills (guidance, not instructions; they grant no tools…)".
- **Tests:**
  - `command-center/tests/test_skill_catalog.py`: provenance pinned, tamper → quarantine, poison stripped, selection holdout, revocation survives restart.
  - `test_skill_catalog_crlf.py`.
  - `test_coding_tasks_local_sidecar.py::test_catalog_skills_reach_the_sidecar_for_a_bug_fix_task`, which uses a deterministic test model.
- **Import mode:** text-only. The SKILL.md is copied verbatim. No scripts, hooks or references are imported, nothing is executed, and there is no auto-update.
- **Tools:** `grants.tools=[]` and `permissions=[]` for all 11 skills.

### Per-skill table

In every row: SHA256 = the file hash, which matches the `sha256` recorded in provenance; STATUS = UNVERIFIED; TOOLS REQUIRED = none granted; MODEL_USED_IT = UNKNOWN; VERIFIED_BENEFIT = NOT_MEASURED.

| ID | UPSTREAM @ commit | LICENSE | SHA256 (SKILL.md) | TRIGGER (English part; Russian stems also present) | DELIVERED_TO_MODEL? | REC |
|---|---|---|---|---|---|---|
| superpowers/systematic-debugging | obra/superpowers @ `5bf4e78011075bcfc0dc295f0724994cd123ee71` | MIT | `808fc5717aa88ad65efff312b11c186294d3e6ee301afb584e2f86599b137787` | bug, debug, fix, fails, error, exception, traceback, crash, regression, flaky, not working … | **YES** — present in `skills.ids` and `sidecar.skills_used` of records D1-L0…L4 and D2-RAW/LESSON @ `cdb4b09d` | KEEP |
| superpowers/test-driven-development | obra/superpowers @ `5bf4e780…` | MIT | `64b03fce4aee5a97a93160cea8111f3ba13a17b7c001db4bd5836d67fd10705d` | fix, bug, test, regression, implement, feature, refactor, tdd, failing test … | **YES** — same records | KEEP |
| superpowers/verification-before-completion | obra/superpowers @ `5bf4e780…` | MIT | `2befe7fc55bcadaa3d97dd9e8efeb633d2561c0ebe74c5a8b17c4d9e7e4520b3` | fix, bug, implement, change, update, add, done, complete, verify, passing … | **YES** — same records | KEEP |
| superpowers/writing-plans | obra/superpowers @ `5bf4e780…` | MIT | `0bc3d36590f7b2c323ed3ec18ff77e9f8ed57af42f5680a02b11a2d20de265cf` | plan, design, architecture, multi-step, spec, roadmap, migration … | UNKNOWN | KEEP |
| superpowers/requesting-code-review | obra/superpowers @ `5bf4e780…` | MIT | `cfcee1b06774e7c0517f1e09be1a11f2d5680257072723e709ddbcf7e08b795a` | code review, review, pull request, before merge, ready to merge … | UNKNOWN | KEEP |
| superpowers/receiving-code-review | obra/superpowers @ `5bf4e780…` | MIT | `091df1629510af1b92fc4abd6f96732ebedb4cb2c0f3457e8f2740b0504a2438` | review comments, reviewer, feedback, address comments, requested changes, review … | UNKNOWN | KEEP |
| superpowers/using-git-worktrees (worktrees) | obra/superpowers @ `5bf4e780…` | MIT | `8cfb86f121269e8f7f12361e6795c4f6738828340e28964c9229d365666c9edd` | worktree, isolated workspace, isolation, branch, parallel work … | UNKNOWN | KEEP |
| anthropics/skill-creator | anthropics/skills @ `34040c9c568585f6929bedeaad110ad08f079624` | Apache-2.0 | `dcd4803e61e913e6fc27294184cd3a71f09f5e924ff20c8a9a20173e7b3c2bcf` | skill, skill.md, create a skill, new skill, improve skill, eval … | UNKNOWN | KEEP |
| anthropics/webapp-testing | anthropics/skills @ `34040c9c…` | Apache-2.0 | `51b7349e77ec63b7744a6f63647e7566a0b4d2e301121cc10e8c2113af6556a2` | web app, webapp, ui, frontend, browser, page, button, form, screenshot, playwright … | UNKNOWN | KEEP |
| huggingface/hf-mem | huggingface/skills @ `abc20ae526d8b4c0e4dff89f904adce28a4a0eb6` | Apache-2.0 | `ee99f9d97e084aa61a80eaec4e6de2265341e71256d918d48fb87a2ac01ce99f` | vram, memory, fit on gpu, gpu memory, kv cache, how much memory … | UNKNOWN | KEEP |
| huggingface/huggingface-local-models | huggingface/skills @ `abc20ae5…` | Apache-2.0 | `814640db1d5f2f274aec09d66b3589ed5a0fdcfaf536afd508b1d757dcae1534` | gguf, llama.cpp, llama-server, quant, local model, run locally, hugging face … | UNKNOWN | KEEP |

### Findings on the catalog
- **Broad triggers.** The triggers for TDD and verification-before-completion include `fix`, `add`, `change` and `update`. As a result, these two plus systematic-debugging were selected for **every** D1 and D2 coding task, in both D2 profiles.
- **No A/B, so no benefit claim.** Because the same skills were present in every run, today's data cannot show any skill effect.
- **Russian triggers not checked.** The Russian trigger stems were not verified in this audit: the console output was mis-encoded.

## 2. SkillLibrary — `.agents/skills` (28 skills; runnable library via `features/skills.py` `SkillLibrary`)

### Registry state
- No per-skill status field exists in the frontmatter. Status is recorded only for the 8 skills in `voltagent-agent-skills.lock.json`.
  - For those 8, the lock sha256 values **match** the current SKILL.md hashes.
  - The lock's activation block: `mode=instructions_only, auto_execute=false, grants=[], max_selected_skills=2, inject_all_into_system_prompt=false`.
- No tools are declared in the frontmatter.
- **DELIVERED_TO_MODEL = UNKNOWN** for all 28. There is no record or test tying them to a model prompt in today's run. The coding path uses the catalog in section 1, not this library.

### Per-skill table

| ID | UPSTREAM / LICENSE | SHA256 (SKILL.md) | REC |
|---|---|---|---|
| systematic-debugging | obra/superpowers @ `b36e0829c6d0140e93cfef2ca599b1b07d4a7797` / MIT | `ee8415fa7ae005385999d7442fdafa03d5d612c99f4fd3fd5c5adb52e71fd511` | KEEP (duplicate of the catalog copy, which is pinned to a different upstream commit) |
| test-driven-development | obra/superpowers @ `b36e0829…` / MIT | `d6fb8a39337abffbfe3ac5b349e0a335c65c3a4ca0c139efb6713a68db75c470` | KEEP (duplicate, as above) |
| webapp-testing | anthropics/skills @ `41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f` / Apache-2.0 | `e86f9dc9ca3e301c64a544617e0b260f6d27a09c12c8334b29201ccc511a3b93` | KEEP (duplicate, as above) |
| frontend-design | anthropics/skills @ `41bbe19d…` / Apache-2.0 | `538a7f92817abae71eda8e57e76134f502396187649683653e71b3792bfb1e0c` | KEEP |
| mcp-builder | anthropics/skills @ `41bbe19d…` / Apache-2.0 | `a3c091c2828c984c0a11078abed6012b53963885358c5e287a360540d42b24dc` | KEEP |
| differential-review | trailofbits/skills @ `d3323cefbcf645678b8dc481de204b02ad3d02dc` / CC-BY-SA-4.0 | `58583c57e73c92dfefb012d78d8d313c0f22a95f54d4e3814c457f1e646cb0f6` | KEEP |
| property-based-testing | trailofbits/skills @ `d3323cef…` / CC-BY-SA-4.0 | `b1033a3871acb93563996687275088a13dda7395b061401b5f783817a0486925` | KEEP |
| variant-analysis | trailofbits/skills @ `d3323cef…` / CC-BY-SA-4.0 | `640e15e30ed734b02807726681f26fd4b3498c6e6ff2b45f37c4f408aeddade2` | KEEP |
| browser-research | in-repo / not recorded | `21bc60ba9c1450a3a9874f15406c9f46a4d6c09a88796c64fa75cdaf7ae0bd34` | KEEP |
| context-builder | in-repo / not recorded | `12526ef4fe119c847b24e300504bee5b30489f4ec813812a125a792077f2ba43` | KEEP |
| external-evidence-check | in-repo / not recorded | `8f7ff8025293ebf213f8e062c3655dc67f3d76339f7c5828dae381687e1c7913` | KEEP |
| failure-retrospective | in-repo / not recorded | `780e52b69c5b1d6dbb4d75f93c8d951579a85d45751c848911034407fc1743cd` | KEEP |
| memory-curator | in-repo / not recorded | `85bf6a16cb7964a8f662c78f4eefefb7a9a9b05454ff5ef0e08681089a35c1a8` | KEEP |
| memory-recall | in-repo / not recorded | `3f09be4410bdde5f082a01d935c8e40f9207897c32ef0f2244efc123bf54c4f4` | KEEP |
| model-eval | in-repo / not recorded | `7fbe06e487517dcf1748267cb87bb3402ee7207ea4241d88f3f64581e9ae4500` | KEEP |
| night-mission | in-repo / not recorded | `5f238647602cfd1c46b7817ef1f27321f1c1e25a5b9e2376c2036e21d0915f4b` | KEEP |
| obsidian-knowledge | in-repo / not recorded | `9be59faa6d4ae076b492cafdeee7f6479714d04944b9387b898fd7fb6bd9e36c` | KEEP |
| openrouter-sync | in-repo / not recorded | `1853b3cb0f0d629b248ca328baaf8e8aca7540031b5a6059f8bab4bfe27ab890` | KEEP |
| permission-auditor | in-repo / not recorded | `655cab40ed502329dccf8f0fe6d74ee6e8f68b6c8bb96d82deaf00b51280759c` | KEEP |
| proof-before-done | in-repo / not recorded | `473d0398080bc57daf70f8234146c7b68f13fcad5400363965e141183e0cb3d6` | KEEP |
| repo-audit | in-repo / not recorded | `b343d9320bf79c20c2c128e193e4566754427ac13b352aa02742c81040f60cca` | KEEP |
| requirement-clarify | in-repo / not recorded | `1a842f8a445a03e3901b540e4ce501189d6f0924f94e7ff4556bb8b8cf9945d3` | KEEP |
| safe-code-change | in-repo / not recorded | `f5589ff67a20c9f0c07482444cd837020ac972b4bc1fcefe9c75f10f8821a538` | KEEP |
| safe-terminal | in-repo / not recorded | `c285e00bbd51f4e5da36879074546b05907ae163d9d30d7d2c2e375bc96db160` | KEEP |
| skill-evaluator | in-repo / not recorded | `5bf271ad17a25c9edd43a7051809f78dffe7f0ce61317f583e1f5f2242602c8f` | KEEP |
| skill-forge | in-repo / not recorded | `3e96bec5d5b9efd64dd5db0b2f292a2a34a13c0104920a6c400b210678dd73b9` | KEEP |
| video-editing | in-repo / not recorded (frontmatter version "1.0") | `b3e3dc0aa826ffd3868a6ce7ae3ac43e39c886ad6822609edb22ce849dfe73f0` | KEEP |
| solana-volume-suite | in-repo / not recorded | `1488e979344b96668b430052d17c5c84c6aae7d2d5dab6df07876f6fef6e6ff4` | **QUARANTINE**: its description is autonomous market making and volume generation on Solana (money-moving). It is outside the owner-product scope and has no provenance or status. |

## 3. Claude Code developer skills — `.claude/skills` (for the coding agent, not the Bossman student)

- **Skills:** atomicity-boundary, honest-verdict, measure-do-not-assume, negative-control, parallel-agent-file-ownership, port-do-not-merge, reproduce-before-fixing, tests-that-find-bugs.
- **Provenance:** in-repo, with no upstream or license recorded.
- **Delivery and effect:** DELIVERED_TO_MODEL (Bossman student) = NO path found; MODEL_USED_IT = UNKNOWN; VERIFIED_BENEFIT = NOT_MEASURED.
- **Recommendation:** KEEP.

| ID | SHA256 (SKILL.md) |
|---|---|
| atomicity-boundary | `a48c3054a6aef65ad0d064035b1001606a152ea1e50bb037334f933ef70a7d28` |
| honest-verdict | `d95d7538869628630a9b5f6db4133dd6bb0853042d4988e4584baf298d2be410` |
| measure-do-not-assume | `f710262c5798bbbec210cb664d9c8138df03fd45e19d42eadeb387db2dea82d3` |
| negative-control | `1d04ca6f02ffb81a187eb7e5411da3fda5facfeb0f7f9622c1926f121cd690e9` |
| parallel-agent-file-ownership | `c4acd09059c2b5d68b5a981f70c296eab46d3accd63e9ae73540c63b5c0cc34a` |
| port-do-not-merge | `bdcdd51a93181c402f7e3e64c5fcd97ee82cd22e06d64bfabcb7b9680b04f82f` |
| reproduce-before-fixing | `fb0d7886758062900a2105a2fc8e812686978d89e9f00f793e372fc62d0fbfc2` |
| tests-that-find-bugs | `e4dd58ba930630a798a2648d36198ac8d53f2acb8ae95325cb5b4c65b43b2a1b` |

## 4. bossman-core

- **Skill:** `bossman-core/skills/compact/SKILL.md`, sha256 `61ba6ca76333b8a1ee46b8555ed61ff0fcc15c797587e53084c685f5919ed4d2`.
- **Metadata:** the frontmatter has no description, and there is no registry entry.
- **Delivery:** DELIVERED_TO_MODEL = UNKNOWN.
- **Recommendation:** KEEP. Its purpose should be reviewed.

## Bottom line
- **Skills proven delivered to a model:** 3. These are catalog superpowers/systematic-debugging, test-driven-development and verification-before-completion, delivered to Qwen3.8-27B through the local sidecar in D1 and D2 at `cdb4b09d`.
- **Proven used or beneficial:** 0.
- **Owner-VERIFIED:** 0.
- **QUARANTINE recommended:** 1, solana-volume-suite.
- **Duplicates:** 3 skills exist in both the catalog and `.agents/skills`, pinned to different upstream commits with different hashes: systematic-debugging, test-driven-development and webapp-testing.
