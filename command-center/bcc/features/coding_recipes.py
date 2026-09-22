"""Executable coding recipes — verified lessons a coding task can act on.

A *recipe* is the product form of a closed coding case: symptom -> cause -> diagnosis
-> action -> required check, plus when it applies, when it must NOT be applied, and an
ordered list of steps a coding sidecar can execute. It exists so that a verified fix
travels to the next analogous task through CONTEXT (``context.recipes`` of the coding
sidecar payload), never through weights.

No new store. A recipe is one ``record_type="lesson"`` record of the canonical
``learning.trace.LearningStore``, written through ``learning.lessons.LessonBook`` in
``<data_dir>/learning`` — the very directory ``lifecycle_wiring.recall_for_task`` reads,
so a saved recipe is ALSO recalled as ordinary memory by the task engine. The structured
part (the executable steps) rides inside the lesson body under ``coding_recipe``; the
prose part is mapped onto the v2 lesson fields (symptoms, root_cause, recipe, check,
counterexample), which the store's poison filter already polices. Everything the store
enforces still holds: a record becomes VERIFIED only with an independent verifier and
fresh, task-bound evidence; candidates and retired lessons are never served.

Executable means: a sequence of ALLOWED sidecar tools with typed arguments. There is no
field that can carry a shell string, and ``validate_recipe`` rejects anything that is not
in the grammar below — unknown keys, unknown tools, absolute or escaping paths, option-
looking paths, shell metacharacters in paths, control-plane text. The grammar is checked
at write time AND again at read time, so a record edited on disk does not become an
instruction.

A memory hit is not a solve. ``executable_recipes`` answers "is there a verified recipe
that looks relevant to this instruction"; whether the student then solves the task is
measured by the lab's hidden verifier, and the two are reported separately.

Routes (mounted under ``/api`` with the usual token auth):
  GET  /coding-recipes                 verified recipes (optionally ?project_id=)
  POST /coding-recipes/validate        {"recipe"} -> {"ok", "errors"}
  POST /coding-recipes                 {"recipe","evidence","verifier","project_id","scope"}
  POST /coding-recipes/match           {"instruction","project_id"} -> {"items","memory_hit"}
"""
from __future__ import annotations

import asyncio
import copy
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import Feature

router = APIRouter()

RECIPE_SCHEMA_VERSION = "bossman.coding-recipe/1"
#: The lesson task class every coding recipe is stored under; retrieval filters on it.
TASK_CLASS = "coding_recipe"
DEFAULT_PROJECT = "bossman"
LEARNING_SUBDIR = "learning"          # == lifecycle_wiring.DEFAULT_LEARNING_SUBDIR

#: The only tools a recipe step may name. Mirrors the coding sidecar's tool loop.
ALLOWED_TOOLS = ("read_file", "search", "list_dir", "edit_file", "write_file", "run_tests")
REQUIRED_FIELDS = ("id", "symptom", "cause", "diagnosis", "action", "required_check",
                   "applies_when", "counterexample", "steps", "provenance", "status")
OPTIONAL_FIELDS = ("schema_version", "title", "project_id", "scope", "failed_approaches",
                   # added on the way OUT by executable_recipes; harmless on the way in
                   "lesson_id", "retrieval")
PROSE_FIELDS = ("symptom", "cause", "diagnosis", "action", "counterexample")
APPLIES_WHEN_KEYS = ("task_class", "project_id", "keywords", "paths", "language",
                     "runtime", "environment")
PROVENANCE_SOURCES = ("student", "teacher")
ASSISTANCE_LEVELS = ("none", "hint", "teacher_patch", "reference_solution", "unknown")
INDEPENDENT_CLASSES = ("external_tool", "cross_model", "human")

MAX_STEPS = 24
MAX_TEXT = 2000
MAX_CODE = 20000
MAX_PATHS = 20
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,119}$")
# A path is data for a tool, never text for a shell. These characters have no business
# in a repository-relative path and every one of them is a shell metacharacter.
_PATH_FORBIDDEN = re.compile(r"[;&|$`<>*?!\n\r\t\x00\"']")

#: tool -> (required arg names, optional arg names)
STEP_ARGS: dict[str, tuple[frozenset, frozenset]] = {
    "read_file": (frozenset({"path"}), frozenset({"start_line", "end_line"})),
    "list_dir": (frozenset({"path"}), frozenset()),
    "search": (frozenset({"pattern"}), frozenset({"path"})),
    "edit_file": (frozenset({"path", "old", "new"}), frozenset()),
    "write_file": (frozenset({"path", "content"}), frozenset()),
    "run_tests": (frozenset({"paths"}), frozenset()),
}


class RecipeInvalid(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class RecipeNotVerified(ValueError):
    """No verification evidence, or evidence the store refuses (self-verification,
    expected != actual, unbound evidence). The recipe is NOT served."""


# ---------------------------------------------------------------- validation
def _poison(text: str, *, prose: bool) -> list[str]:
    """Control-plane text is refused. For prose every reason counts except length; for
    code arguments only the denylist and secrets do — code legitimately contains JSON
    and words like ``permissions``."""
    from learning.lessons import poison_reasons  # noqa: WPS433 — bossman-shared
    reasons = [r for r in poison_reasons(str(text)) if "too short" not in r and "too long" not in r]
    if not prose:
        reasons = [r for r in reasons if "denylist" in r or "secret" in r]
    return reasons


def _path_errors(value: Any, where: str) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"{where}: path must be a non-empty string"]
    if len(value) > 300:
        return [f"{where}: path too long"]
    if _PATH_FORBIDDEN.search(value):
        return [f"{where}: path contains shell/control characters"]
    if value.startswith("-"):
        return [f"{where}: path looks like a command-line option"]
    if value.startswith(("/", "\\", "~")) or PureWindowsPath(value).drive or \
            PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        return [f"{where}: path must be relative to the repository"]
    parts = [p for p in re.split(r"[\\/]+", value) if p]
    if any(p == ".." for p in parts):
        return [f"{where}: path escapes the repository"]
    if any(p.lower() == ".git" for p in parts):
        return [f"{where}: .git is not a recipe target"]
    return []


def _paths_errors(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{where}: paths must be a non-empty list"]
    if len(value) > MAX_PATHS:
        return [f"{where}: at most {MAX_PATHS} paths"]
    errs: list[str] = []
    for i, p in enumerate(value):
        errs += _path_errors(p, f"{where}[{i}]")
    return errs


def _step_errors(step: Any, where: str) -> list[str]:
    if not isinstance(step, dict):
        return [f"{where}: a step must be an object {{tool, args}}"]
    extra = set(step) - {"tool", "args"}
    if extra:
        return [f"{where}: unknown step keys {sorted(extra)} (a step is only {{tool, args}})"]
    tool = step.get("tool")
    if tool not in ALLOWED_TOOLS:
        return [f"{where}: tool {tool!r} is not allowed (allowed: {', '.join(ALLOWED_TOOLS)})"]
    args = step.get("args")
    if not isinstance(args, dict):
        return [f"{where}: args must be an object"]
    required, optional = STEP_ARGS[tool]
    missing = required - set(args)
    unknown = set(args) - required - optional
    errs: list[str] = []
    if missing:
        errs.append(f"{where}: {tool} needs {sorted(missing)}")
    if unknown:
        errs.append(f"{where}: {tool} does not take {sorted(unknown)}")
    if errs:
        return errs
    for name, value in args.items():
        at = f"{where}.{name}"
        if name == "path":
            errs += _path_errors(value, at)
        elif name == "paths":
            errs += _paths_errors(value, at)
        elif name in ("start_line", "end_line"):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                errs.append(f"{at}: must be a positive integer")
        elif name == "pattern":
            if not isinstance(value, str) or not value.strip() or len(value) > 300 or "\n" in value:
                errs.append(f"{at}: pattern must be one non-empty line (<=300 chars)")
            else:
                errs += [f"{at}: {r}" for r in _poison(value, prose=False)]
        elif name in ("old", "new", "content"):
            if not isinstance(value, str) or len(value) > MAX_CODE:
                errs.append(f"{at}: must be a string of at most {MAX_CODE} chars")
            else:
                errs += [f"{at}: {r}" for r in _poison(value, prose=False)]
    if tool == "edit_file" and not errs and args["old"] == args["new"]:
        errs.append(f"{where}: edit_file with old == new changes nothing")
    if tool == "edit_file" and not errs and not args["old"]:
        errs.append(f"{where}: edit_file needs a non-empty old text (use write_file for new files)")
    return errs


def validate_recipe(recipe: Any) -> list[str]:
    """Errors in ``recipe`` (empty list = a valid, VERIFIED, executable recipe)."""
    if not isinstance(recipe, dict):
        return ["recipe must be an object"]
    errs: list[str] = []
    missing = [k for k in REQUIRED_FIELDS if k not in recipe]
    if missing:
        errs.append(f"missing fields: {missing}")
    unknown = sorted(set(recipe) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        errs.append(f"unknown fields: {unknown} (a recipe has no free-form or shell fields)")
    if errs:
        return errs

    sv = recipe.get("schema_version")
    if sv is not None and sv != RECIPE_SCHEMA_VERSION:
        errs.append(f"schema_version must be {RECIPE_SCHEMA_VERSION}")
    if not isinstance(recipe["id"], str) or not _ID_RE.match(recipe["id"]):
        errs.append("id must match [A-Za-z0-9][A-Za-z0-9._:-]{2,119}")
    if recipe["status"] != "VERIFIED":
        errs.append("status must be VERIFIED (candidates are not executable recipes)")
    for name in PROSE_FIELDS:
        value = recipe[name]
        if not isinstance(value, str) or not value.strip():
            errs.append(f"{name} must be a non-empty string")
            continue
        if len(value) > MAX_TEXT:
            errs.append(f"{name} too long (>{MAX_TEXT})")
        errs += [f"{name}: {r}" for r in _poison(value, prose=True)]
    if len(str(recipe.get("action") or "").strip()) < 8:
        errs.append("action must be at least 8 characters of advice")
    for name in ("title",):
        if name in recipe and recipe[name] is not None:
            if not isinstance(recipe[name], str):
                errs.append(f"{name} must be a string")
            else:
                errs += [f"{name}: {r}" for r in _poison(recipe[name], prose=True)]
    if "failed_approaches" in recipe:
        fa = recipe["failed_approaches"]
        if not isinstance(fa, list) or not all(isinstance(x, str) for x in fa):
            errs.append("failed_approaches must be a list of strings")
        else:
            for x in fa:
                errs += [f"failed_approaches: {r}" for r in _poison(x, prose=True)]
    if "scope" in recipe and recipe["scope"] not in ("project", "global"):
        errs.append("scope must be project|global")

    check = recipe["required_check"]
    if not isinstance(check, dict) or set(check) != {"tool", "args"}:
        errs.append("required_check must be exactly {tool: run_tests, args: {paths: [...]}}")
    elif check.get("tool") != "run_tests":
        errs.append("required_check.tool must be run_tests")
    elif not isinstance(check.get("args"), dict) or set(check["args"]) != {"paths"}:
        errs.append("required_check.args must be exactly {paths: [...]}")
    else:
        errs += _paths_errors(check["args"]["paths"], "required_check.args.paths")

    aw = recipe["applies_when"]
    if not isinstance(aw, dict) or not aw:
        errs.append("applies_when must be a non-empty object")
    else:
        bad = sorted(set(aw) - set(APPLIES_WHEN_KEYS))
        if bad:
            errs.append(f"applies_when: unknown keys {bad}")
        for k, v in aw.items():
            vals = v if isinstance(v, list) else [v]
            if not all(isinstance(x, str) and x.strip() for x in vals):
                errs.append(f"applies_when.{k} must be a string or a list of strings")
            else:
                for x in vals:
                    errs += [f"applies_when.{k}: {r}" for r in _poison(x, prose=False)]

    steps = recipe["steps"]
    if not isinstance(steps, list) or not steps:
        errs.append("steps must be a non-empty list")
    elif len(steps) > MAX_STEPS:
        errs.append(f"at most {MAX_STEPS} steps")
    else:
        for i, step in enumerate(steps):
            errs += _step_errors(step, f"steps[{i}]")

    prov = recipe["provenance"]
    if not isinstance(prov, dict) or not str(prov.get("who") or "").strip():
        errs.append("provenance must be an object with a non-empty 'who'")
    else:
        if prov.get("source") is not None and prov["source"] not in PROVENANCE_SOURCES:
            errs.append(f"provenance.source must be one of {PROVENANCE_SOURCES}")
        if prov.get("assistance_level") is not None and prov["assistance_level"] not in ASSISTANCE_LEVELS:
            errs.append(f"provenance.assistance_level must be one of {ASSISTANCE_LEVELS}")
        if prov.get("assistance_level") == "teacher_patch" and prov.get("source") == "student":
            errs.append("a teacher_patch recipe cannot claim source=student")
        for k, v in prov.items():
            if not isinstance(v, (str, int, float, list)) or \
                    (isinstance(v, list) and not all(isinstance(x, str) for x in v)):
                errs.append(f"provenance.{k} must be a string/number or a list of strings")
    return errs


def assert_valid_recipe(recipe: Any) -> None:
    errs = validate_recipe(recipe)
    if errs:
        raise RecipeInvalid(errs)


def _clean(recipe: dict) -> dict:
    """The stored form: known fields only, retrieval annotations dropped."""
    out = {k: copy.deepcopy(recipe[k]) for k in REQUIRED_FIELDS}
    for k in ("title", "project_id", "scope", "failed_approaches"):
        if k in recipe:
            out[k] = copy.deepcopy(recipe[k])
    out["schema_version"] = RECIPE_SCHEMA_VERSION
    return out


def describe_step(step: dict) -> str:
    """A human line for the lesson's ``recipe`` list (what recall shows as ``do:``)."""
    tool, args = step["tool"], step["args"]
    if tool == "run_tests":
        return f"run_tests {' '.join(args['paths'])}"
    if tool == "search":
        return f"search {args['pattern']!r}" + (f" in {args['path']}" if args.get("path") else "")
    if tool == "edit_file":
        first = (args["old"].strip().splitlines() or [""])[0][:80]
        return f"edit_file {args['path']}: replace «{first}…»"
    return f"{tool} {args.get('path', '')}".strip()


# ---------------------------------------------------------------- storage
def _data_dir(target: Any) -> Path:
    if isinstance(target, (str, Path)):
        return Path(target)
    return Path(target.settings.data_dir)


def learning_dir(target: Any) -> Path:
    """``<data_dir>/learning`` — the same LessonBook the task engine recalls from."""
    return _data_dir(target) / LEARNING_SUBDIR


def _book(target: Any):
    from learning.lessons import LessonBook  # noqa: WPS433
    return LessonBook(learning_dir(target))


def _raw(book: Any, lesson_id: str) -> dict | None:
    from learning import trace as _trace  # noqa: WPS433
    return book.store.current(_trace.case_id({"task_id": lesson_id, "start_sha": "", "end_sha": ""}))


def _evidence_errors(evidence: Any) -> list[str]:
    if not isinstance(evidence, dict):
        return ["evidence must be an object"]
    errs = [f"evidence.{k} is required" for k in ("source", "expected", "actual", "head_sha", "environment")
            if not str(evidence.get(k) or "").strip()]
    if not errs and str(evidence["actual"]).strip() != str(evidence["expected"]).strip():
        errs.append("evidence.actual != evidence.expected: the check did not pass, nothing to verify")
    return errs


def save_verified_recipe(target: Any, recipe: dict, *, evidence: dict, verifier: dict,
                         project_id: str | None = None, scope: str | None = None) -> dict:
    """Store ``recipe`` as a VERIFIED lesson, or refuse.

    ``evidence`` = {source, expected, actual, head_sha, environment} of the INDEPENDENT
    check (the lab's hidden verifier), ``verifier`` = {principal_id, independence_class,
    model_id?, run_id?}. Refusals: ``RecipeInvalid`` (grammar), ``RecipeNotVerified``
    (no passing evidence, verifier not independent, or the store refused it).

    Two store writes happen: the candidate (``LessonBook.save``) with the executable
    part attached, then ``LessonBook.verify``. If verification is refused, the candidate
    stays in the history corpus — visible, never served — which is the store's normal
    shape for unverified knowledge.
    """
    from learning import trace as _trace  # noqa: WPS433
    from learning.lessons import CoachingEpisode, LessonError, LessonPoisoned, Provenance  # noqa: WPS433

    assert_valid_recipe(recipe)
    ev_errs = _evidence_errors(evidence)
    if ev_errs:
        raise RecipeNotVerified("; ".join(ev_errs))
    if not isinstance(verifier, dict) or not str(verifier.get("principal_id") or "").strip():
        raise RecipeNotVerified("verifier.principal_id is required")
    if verifier.get("independence_class") not in INDEPENDENT_CLASSES:
        raise RecipeNotVerified(f"verifier.independence_class must be one of {INDEPENDENT_CLASSES}")
    prov = recipe["provenance"]
    if _trace.canonical_principal_id(str(verifier["principal_id"])) == \
            _trace.canonical_principal_id(str(prov["who"])):
        raise RecipeNotVerified("the author of a recipe cannot verify it")

    clean = _clean(recipe)
    project = str(project_id or recipe.get("project_id") or DEFAULT_PROJECT)
    scope = str(scope or recipe.get("scope") or "project")
    clean["project_id"], clean["scope"] = project, scope
    level = str(prov.get("assistance_level") or "unknown")
    teacher = level in ("teacher_patch", "reference_solution") or prov.get("source") == "teacher"
    paths = clean["required_check"]["args"]["paths"]
    ep = CoachingEpisode(
        attempt_id=str(prov.get("run_id") or prov.get("task_id") or clean["id"]),
        task_id=f"coding-recipe:{clean['id']}", project_id=project,
        failure_observation=clean["symptom"], correction=clean["action"],
        source="teacher" if teacher else "student",
        kind="teacher_patch" if level == "teacher_patch" else "student_fix",
        provenance=Provenance(who=str(prov["who"]), what=str(prov.get("what") or "verified coding recipe"),
                              evidence_refs=[str(evidence["source"])] + list(prov.get("evidence_refs") or []),
                              run_id=str(prov.get("run_id") or ""), model=str(prov.get("model") or "")),
        task_class=TASK_CLASS, scope=scope,
        environment=str(evidence.get("environment") or "unknown-env"),
        title=str(clean.get("title") or f"recipe {clean['id']}: {clean['symptom'][:60]}"),
        symptoms=[clean["symptom"]], root_cause=clean["cause"],
        failed_approaches=list(clean.get("failed_approaches") or []),
        recipe=[f"diagnosis: {clean['diagnosis']}"] + [describe_step(s) for s in clean["steps"]],
        check="run_tests " + " ".join(paths) + " passes",
        counterexample=clean["counterexample"],
        refs={"test": list(paths), "evidence": [str(evidence["source"])]},
        assistance_level=level if level in ASSISTANCE_LEVELS else "unknown",
        model=str(prov.get("model") or ""))
    book = _book(target)
    try:
        book.save(ep)
    except (LessonPoisoned, LessonError, _trace.ValidationError) as exc:
        raise RecipeInvalid([f"lesson store refused the recipe: {exc}"]) from exc
    current = _raw(book, ep.lesson_id)
    if current is None:                                   # pragma: no cover — store invariant
        raise RecipeInvalid(["lesson store lost the candidate it just wrote"])
    merged = copy.deepcopy(current)
    merged.setdefault("lesson", {})["coding_recipe"] = {"schema_version": RECIPE_SCHEMA_VERSION,
                                                        "recipe": clean}
    for key in ("case_id", "version", "supersedes_version", "created_at"):
        merged.pop(key, None)
    book.store.add(merged, write_markdown=False)
    try:
        rec = book.verify(ep.lesson_id, verifier=dict(verifier), evidence=dict(evidence),
                          statement=f"{evidence['source']}: expected={evidence['expected']} "
                                    f"actual={evidence['actual']}")
    except (_trace.ValidationError, LessonError) as exc:
        raise RecipeNotVerified(f"the lesson store refused verification: {exc}") from exc
    return {"recipe_id": clean["id"], "lesson_id": ep.lesson_id, "status": "VERIFIED",
            "version": rec.get("version"), "project_id": project, "scope": scope,
            "kind": ep.kind, "learning_dir": str(learning_dir(target))}


def _recipe_of(raw: dict | None) -> dict | None:
    body = ((raw or {}).get("lesson") or {}).get("coding_recipe")
    if not isinstance(body, dict) or body.get("schema_version") != RECIPE_SCHEMA_VERSION:
        return None
    recipe = body.get("recipe")
    return copy.deepcopy(recipe) if isinstance(recipe, dict) else None


def list_recipes(target: Any, *, project_id: str | None = None) -> list[dict]:
    """Every verified, currently applicable recipe (read-time re-validated)."""
    book = _book(target)
    rows = book.retrieve(project_id=project_id or DEFAULT_PROJECT, task_class=TASK_CLASS, limit=10_000) \
        if project_id else [r for r in book.all_lessons(include_candidates=False)
                            if r.get("status") == "verified" and r.get("task_class") == TASK_CLASS]
    out = []
    for row in rows:
        recipe = _recipe_of(_raw(book, str(row.get("lesson_id"))))
        if recipe is None or validate_recipe(recipe):
            continue
        out.append({**recipe, "lesson_id": row.get("lesson_id")})
    return out


def _match_text(recipe: dict) -> str:
    aw = recipe.get("applies_when") or {}
    words = []
    for key in ("keywords", "paths", "task_class", "language"):
        v = aw.get(key)
        words += v if isinstance(v, list) else ([v] if v else [])
    return "\n".join([recipe["symptom"], recipe["cause"], recipe["diagnosis"], recipe["action"],
                      " ".join(words), str(recipe.get("title") or "")])


#: A recipe is offered only when the instruction shares at least this many distinct
#: content terms with it, or names one of its exact signals (error class, path, symbol).
MIN_SHARED_TERMS = 2


def executable_recipes(svc: Any, instruction: str, project_id: str, *, limit: int = 3) -> list[dict]:
    """Verified executable recipes relevant to ``instruction`` for ``project_id``.

    Candidates come from ``LessonBook.retrieve`` (VERIFIED only, applicability rules,
    project isolation, read-time poison filter); ranking is the lesson index's own BM25
    plus exact-signal matching (``learning.retrieval``). Each recipe is re-validated
    against the grammar before it is returned; a record that no longer validates is
    dropped, not repaired. Synchronous (file store): call it via ``asyncio.to_thread``
    from async code. Returns ``[]`` when nothing is relevant — a miss is not an error.
    """
    from learning.retrieval import bm25_scores, exact_signals, tokenize  # noqa: WPS433

    query = str(instruction or "").strip()
    if not query:
        return []
    book = _book(svc)
    rows = book.retrieve(project_id=str(project_id or DEFAULT_PROJECT), task_class=TASK_CLASS,
                         limit=10_000)
    pool: list[tuple[dict, dict]] = []
    for row in rows:
        recipe = _recipe_of(_raw(book, str(row.get("lesson_id"))))
        if recipe is None or validate_recipe(recipe):
            continue
        pool.append((row, recipe))
    if not pool:
        return []
    docs = [_match_text(r) for _, r in pool]
    scores = bm25_scores(query, docs)
    q_terms = set(tokenize(query))
    signals = exact_signals(query)
    ranked = []
    for (row, recipe), doc, score in zip(pool, docs, scores):
        shared = sorted(t for t in q_terms & set(tokenize(doc)) if len(t) > 2 and not t.isdigit())
        exact = [s for s in signals if s.lower() in doc.lower()]
        if len(shared) < MIN_SHARED_TERMS and not exact:
            continue
        ranked.append((score + (5.0 if exact else 0.0), row, recipe, shared, exact))
    ranked.sort(key=lambda t: (-t[0], str(t[2]["id"])))
    out = []
    for score, row, recipe, shared, exact in ranked[:max(1, int(limit))]:
        out.append({**recipe, "lesson_id": row.get("lesson_id"),
                    "retrieval": {"score": round(float(score), 4), "shared_terms": shared[:12],
                                  "exact": exact[:6], "applicability": row.get("applicability")}})
    return out


# ---------------------------------------------------------------- HTTP
class ValidateIn(BaseModel):
    recipe: dict


class SaveIn(BaseModel):
    recipe: dict
    evidence: dict
    verifier: dict
    project_id: str | None = Field(default=None, max_length=200)
    scope: str | None = Field(default=None, pattern="^(project|global)$")


class MatchIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=8000)
    project_id: str = Field(default=DEFAULT_PROJECT, max_length=200)
    limit: int = Field(default=3, ge=1, le=10)


@router.get("/coding-recipes")
async def get_recipes(request: Request, project_id: str | None = None):
    svc = request.app.state.svc
    items = await asyncio.to_thread(list_recipes, svc, project_id=project_id)
    return {"schema_version": RECIPE_SCHEMA_VERSION, "items": items}


@router.post("/coding-recipes/validate")
async def post_validate(body: ValidateIn):
    errors = validate_recipe(body.recipe)
    return {"ok": not errors, "errors": errors, "schema_version": RECIPE_SCHEMA_VERSION}


@router.post("/coding-recipes")
async def post_recipe(body: SaveIn, request: Request):
    svc = request.app.state.svc
    try:
        saved = await asyncio.to_thread(save_verified_recipe, svc, body.recipe,
                                        evidence=body.evidence, verifier=body.verifier,
                                        project_id=body.project_id, scope=body.scope)
    except RecipeInvalid as exc:
        raise HTTPException(422, {"code": "RECIPE_INVALID", "message": "рецепт не прошёл проверку",
                                  "errors": exc.errors})
    except RecipeNotVerified as exc:
        raise HTTPException(422, {"code": "RECIPE_NOT_VERIFIED",
                                  "message": f"рецепт без проверенного доказательства: {exc}"})
    await svc.bus.emit("coding.recipe.verified", recipe_id=saved["recipe_id"],
                       lesson_id=saved["lesson_id"], project_id=saved["project_id"])
    return saved


@router.post("/coding-recipes/match")
async def post_match(body: MatchIn, request: Request):
    svc = request.app.state.svc
    items = await asyncio.to_thread(executable_recipes, svc, body.instruction, body.project_id,
                                    limit=body.limit)
    return {"items": items, "memory_hit": bool(items),
            "note": "memory_hit означает найденный рецепт, а не решённую задачу"}


FEATURE = Feature(name="coding_recipes", router=router)

__all__ = ["ALLOWED_TOOLS", "RECIPE_SCHEMA_VERSION", "RecipeInvalid", "RecipeNotVerified",
           "TASK_CLASS", "assert_valid_recipe", "executable_recipes", "learning_dir",
           "list_recipes", "save_verified_recipe", "validate_recipe"]
