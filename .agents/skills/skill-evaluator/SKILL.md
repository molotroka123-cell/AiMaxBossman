---
name: skill-evaluator
description: Evaluate a new or modified skill against its previous version on reproducible tasks before promotion, preventing self-improvement from silently degrading quality, safety or cost.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: self-improvement
---

# Skill Evaluator

Use before promoting:
- new Skill Forge output
- revised skill
- permission-changing skill
- skill claimed to reduce tokens/errors
- skill created from a failure retrospective

## Principle

A new skill is a hypothesis, not an improvement.

## Inputs

- candidate skill
- previous skill version, if any
- representative evaluation fixtures
- acceptance criteria
- allowed tool/permission policy

## Evaluation dimensions

Measure where relevant:

- task success
- acceptance-test pass rate
- reviewer score
- tool errors
- retries
- total model tokens
- wall-clock time
- cloud cost
- number of unnecessary tool calls
- permission escalations
- safety regressions

## A/B process

For stable fixtures:

1. Run old skill / baseline.
2. Run candidate skill under comparable conditions.
3. Use the same model where practical.
4. Repeat enough cases to avoid judging one lucky run.
5. Compare outcomes.
6. Record evidence.

## Promotion rules

Candidate may be promoted only if:
- it does not materially reduce success quality
- it introduces no new critical safety regression
- all mandatory acceptance checks pass
- permission expansion has required human approval

Suggested default:

```text
quality must not regress
critical safety regressions = 0
mandatory fixture pass rate >= baseline
```

Cost/speed improvements are secondary to correctness.

## Rejection

If candidate is worse:
- keep old version active
- store evaluation result
- return exact failure feedback to Skill Forge
- do not silently overwrite active skill

## Rollback

Every promoted skill version must retain enough version metadata for rollback.

## Output

```text
Baseline version:
Candidate version:
Fixtures:
Baseline metrics:
Candidate metrics:
Regressions:
Improvements:
Security delta:
Decision: PROMOTE / REJECT / HUMAN_REVIEW
Evidence:
```
