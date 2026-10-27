"""Control-question evaluation: did the knowledge help answer held-out questions, versus no knowledge and versus
unrelated (distractor) knowledge? Writing claims to memory is NOT evidence of learning; this is the only gate.

The caller supplies answer_fn(question_text, context_claims) -> (answer_text, cited_claim_ids). In production it is a
local model; in tests it is a deterministic fake. An answer passes when every answer-key group is matched AND, for
arms with context, at least one retrieved claim id is cited (so memorised prior knowledge cannot score as "learned").
"""
from __future__ import annotations

from dataclasses import dataclass

from .transcript import norm_words

MIN_QUESTIONS = 5


@dataclass(frozen=True)
class Question:
    qid: str
    text: str
    # every group must match; a group matches if ANY of its alternatives is present (synonyms / word forms)
    answer_keys: tuple


def _stem(w: str) -> str:
    return w[:5]


def _tokens(text: str) -> set:
    return {_stem(w) for w in norm_words(text) if len(w) > 2 or w.isdigit()}


def retrieve(query: str, claims, k: int = 3) -> list:
    """Token-overlap ranking on 5-char stems (cheap Russian/English morphology tolerance). Zero overlap -> not returned."""
    q = _tokens(query)
    scored = []
    for c in claims:
        s = len(q & _tokens(c.statement + " " + c.quote))
        if s:
            scored.append((-s, c.claim_id, c))
    scored.sort(key=lambda t: t[:2])
    return [c for _, _, c in scored[:k]]


def is_leaky(q: Question) -> bool:
    """A question that already contains its full answer (every key group has an alternative present) proves nothing."""
    qt = _tokens(q.text)
    return all(any(norm_words(alt) and all(_stem(w) in qt for w in norm_words(alt)) for alt in group)
               for group in q.answer_keys)


def _matches(answer: str, q: Question) -> bool:
    at = _tokens(answer)
    for group in q.answer_keys:
        if not any(norm_words(alt) and all(_stem(w) in at for w in norm_words(alt)) for alt in group):
            return False
    return True


def _run_arm(questions, answer_fn, claims, k, need_cite):
    passed, rows = 0, []
    for q in questions:
        ctx = retrieve(q.text, claims, k) if claims is not None else []
        ans, cited = answer_fn(q.text, ctx)
        ids = {c.claim_id for c in ctx}
        ok = _matches(ans, q) and (not need_cite or bool(ids & set(cited or ())))
        passed += ok
        rows.append({"qid": q.qid, "pass": ok, "context_ids": sorted(ids)})
    return {"passed": passed, "n": len(questions), "rows": rows}


def evaluate_questions(questions, claims, answer_fn, *, distractor_claims=(), k: int = 3,
                       min_questions: int = MIN_QUESTIONS) -> dict:
    """Three arms on the same questions: baseline (no context), treatment (retrieved claims), distractor (claims from
    an unrelated source). Verdict IMPROVED_ON_CONTROL_QUESTIONS needs treatment > baseline AND treatment > distractor.
    Even then, transfer to a real task is NOT proven (reported as NOT_RUN)."""
    usable = [q for q in questions if not is_leaky(q)]
    leaky = [q.qid for q in questions if is_leaky(q)]
    base = _run_arm(usable, answer_fn, None, k, need_cite=False)
    treat = _run_arm(usable, answer_fn, list(claims), k, need_cite=True)
    dist = _run_arm(usable, answer_fn, list(distractor_claims), k, need_cite=True)
    n = len(usable)
    if n == 0:
        verdict = "NO_USABLE_QUESTIONS"
    elif treat["passed"] > base["passed"] and treat["passed"] > dist["passed"]:
        verdict = "IMPROVED_ON_CONTROL_QUESTIONS" if n >= min_questions else "PRELIMINARY_IMPROVED_SMALL_N"
    elif treat["passed"] <= base["passed"]:
        verdict = "NO_GAIN_OVER_BASELINE"
    else:
        verdict = "NO_GAIN_OVER_DISTRACTOR"
    return {"verdict": verdict, "n": n, "leaky_excluded": leaky, "baseline": base, "treatment": treat,
            "distractor": dist, "real_task_transfer": "NOT_RUN"}
