"""Синтетический детерминированный E2E: «Improve a synthetic website's SEO
readiness» над сайтом в памяти. Никакой сети, никаких моделей, никакой
публикации: задача publish гейтуется и по умолчанию отклоняется.

Верификатор ПЕРЕЧИТЫВАЕТ сайт (свежее наблюдение) и не доверяет тексту
WorkResult — нечестный исполнитель (`honest=False`) заявляет успех без правок
и получает FAILED.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .model import (ApprovalRequirement, BudgetEnvelope, CompanyObjective, CompanyPlan, CompanyReport,
                    CompanyTask, KPI, ObjectiveConstraint, VerificationOutcome, WorkResult)
from .planner import plan_objective
from .runtime import ApprovalGate, CompanyRuntime

OBJECTIVE_TITLE = "Improve a synthetic website's SEO readiness."
PAGE_CHECKS = ("title", "meta", "h1")


@dataclass(slots=True)
class SyntheticSite:
    """Сайт в памяти: путь → {title, meta, h1, images:[{src, alt}]}."""
    pages: dict[str, dict[str, Any]] = field(default_factory=dict)
    published: bool = False
    writes: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {"pages": {p: {**d, "images": [dict(i) for i in d.get("images", [])]}
                          for p, d in self.pages.items()}, "published": self.published}


def default_site() -> SyntheticSite:
    return SyntheticSite(pages={
        "/": {"title": "Home", "meta": "", "h1": "Welcome",
              "images": [{"src": "hero.png", "alt": ""}]},
        "/about": {"title": "", "meta": "About us", "h1": "About", "images": []},
        "/blog/hello": {"title": "", "meta": "", "h1": "Hello",
                        "images": [{"src": "diagram.png", "alt": "diagram"},
                                   {"src": "photo.png", "alt": ""}]},
        "/contact": {"title": "Contact", "meta": "Reach us", "h1": "Contact",
                     "images": [{"src": "map.png", "alt": "map"}]},
    })


# ---- KPI ----------------------------------------------------------------------
def audit(site: SyntheticSite) -> dict[str, Any]:
    """Детерминированный аудит: доля пройденных проверок в процентах."""
    total = passed = 0
    issues: list[str] = []
    for path in sorted(site.pages):
        page = site.pages[path]
        for f in PAGE_CHECKS:
            total += 1
            if str(page.get(f, "")).strip():
                passed += 1
            else:
                issues.append(f"{path}: missing {f}")
        for img in page.get("images", []):
            total += 1
            if str(img.get("alt", "")).strip():
                passed += 1
            else:
                issues.append(f"{path}: image {img.get('src')} missing alt")
    score = round(100.0 * passed / total, 2) if total else 0.0
    return {"score": score, "passed": passed, "total": total, "issues": issues}


def seo_score(site: SyntheticSite) -> float:
    return audit(site)["score"]


def make_kpi_reader(site: SyntheticSite) -> Callable[[], dict[str, float]]:
    def read_site_kpis() -> dict[str, float]:
        a = audit(site)
        return {"seo_readiness": a["score"], "open_issues": float(len(a["issues"]))}
    return read_site_kpis


# ---- исполнитель -------------------------------------------------------------
def _fix_field(site: SyntheticSite, name: str, make: Callable[[str, dict[str, Any]], str]) -> int:
    n = 0
    for path in sorted(site.pages):
        page = site.pages[path]
        if not str(page.get(name, "")).strip():
            page[name] = make(path, page)
            n += 1
    site.writes += n
    return n


def _fix_alt(site: SyntheticSite) -> int:
    n = 0
    for path in sorted(site.pages):
        for img in site.pages[path].get("images", []):
            if not str(img.get("alt", "")).strip():
                img["alt"] = "image: " + str(img.get("src", "")).rsplit(".", 1)[0]
                n += 1
    site.writes += n
    return n


def make_executor(site: SyntheticSite, *, honest: bool = True) -> Callable[[CompanyTask], WorkResult]:
    """honest=False: заявляет успех, но сайт не трогает (анти-самоотчёт)."""
    def synthetic_site_executor(task: CompanyTask) -> WorkResult:
        act = task.action
        if act in ("seo.audit", "seo.rescore"):
            a = audit(site)
            return WorkResult(task.id, True, f"score={a['score']} issues={len(a['issues'])}",
                              cost=task.estimated_cost, claims={"score": a["score"]})
        if not honest and act.startswith("seo.fix_"):
            return WorkResult(task.id, True, f"{act}: fixed everything (claimed)",
                              cost=task.estimated_cost, claims={"fixed": 99})
        if act == "seo.fix_titles":
            n = _fix_field(site, "title", lambda p, pg: f"{pg.get('h1') or p} | Synthetic Site")
        elif act == "seo.fix_meta":
            n = _fix_field(site, "meta", lambda p, pg: f"{pg.get('h1') or p} — a page on the synthetic site.")
        elif act == "seo.fix_alt":
            n = _fix_alt(site)
        elif act == "seo.publish":
            site.published = True
            n = 1
        else:
            return WorkResult(task.id, False, f"unknown action {act}", cost=0.0)
        return WorkResult(task.id, True, f"{act}: fixed {n}", cost=task.estimated_cost, claims={"fixed": n})
    return synthetic_site_executor


# ---- верификатор (свежее чтение сайта) -----------------------------------------
def _check(site: SyntheticSite, target: str, expect: dict[str, Any]) -> tuple[str, str]:
    if target == "score":
        s = seo_score(site)
        if "min_score" in expect:
            return ("VERIFIED" if s >= float(expect["min_score"]) else "FAILED",
                    f"fresh score {s} vs min {expect['min_score']}")
        if expect.get("observed"):
            return "VERIFIED", f"fresh score observed: {s}"
        return "UNVERIFIED", "score expectation has no checkable property"
    if target.startswith("pages.") and expect.get("all_nonempty"):
        fld = target.split(".", 1)[1]
        missing = [p for p in sorted(site.pages) if not str(site.pages[p].get(fld, "")).strip()]
        return ("VERIFIED", f"fresh read: every page has {fld}") if not missing else \
               ("FAILED", f"fresh read: {fld} still missing on {missing}")
    if target == "images.alt" and expect.get("all_nonempty"):
        missing = [f"{p}:{i.get('src')}" for p in sorted(site.pages)
                   for i in site.pages[p].get("images", []) if not str(i.get("alt", "")).strip()]
        return ("VERIFIED", "fresh read: every image has alt") if not missing else \
               ("FAILED", f"fresh read: alt still missing on {missing}")
    if target == "published" and "equals" in expect:
        return ("VERIFIED" if site.published == bool(expect["equals"]) else "FAILED",
                f"fresh read: published={site.published}")
    return "UNVERIFIED", f"unknown evidence target {target!r}"


def make_verifier(site: SyntheticSite) -> Callable[[CompanyTask, WorkResult], VerificationOutcome]:
    """Агрегация как в bcc.v2.verification: FAILED > UNVERIFIED > VERIFIED;
    пустой список требований → UNVERIFIED. WorkResult НЕ читается."""
    def fresh_site_verifier(task: CompanyTask, _result: WorkResult) -> VerificationOutcome:
        checks = [(req.target, *_check(site, req.target, dict(req.expect)))
                  for req in task.evidence_requirements if req.kind == "site"]
        if not checks:
            return VerificationOutcome("UNVERIFIED", "no site evidence requirement — self-report is not evidence")
        evidence = tuple(f"site:reread {t}: {r}" for t, _, r in checks)
        observed = {"score": seo_score(site), "published": site.published}
        for t, st, r in checks:
            if st == "FAILED":
                return VerificationOutcome("FAILED", f"{t}: {r}", evidence, observed)
        for t, st, r in checks:
            if st == "UNVERIFIED":
                return VerificationOutcome("UNVERIFIED", f"{t}: {r}", evidence, observed)
        return VerificationOutcome("VERIFIED", "; ".join(f"{t} ✓" for t, _, _ in checks), evidence, observed)
    return fresh_site_verifier


# ---- сборка демо --------------------------------------------------------------
def build_objective() -> CompanyObjective:
    return CompanyObjective(
        id="obj-synthetic-seo", title=OBJECTIVE_TITLE, domain="seo",
        description="Fill missing titles, meta descriptions and image alt text on an in-memory site.",
        kpis=(KPI("seo_readiness", "share of on-page checks passed", "up", target=90.0, unit="%"),
              KPI("open_issues", "count of failing on-page checks", "down", target=0.0)),
        constraints=(ObjectiveConstraint("policy", "no-publish-without-approval",
                                         "publishing is a gated kind; the gate decides, not the role"),
                     ObjectiveConstraint("scope", "synthetic-site-only")),
    )


def build_plan(budget: BudgetEnvelope | None = None) -> CompanyPlan:
    return plan_objective(build_objective(), budget or BudgetEnvelope(max_total_cost=20.0, max_task_cost=5.0))


def _counter_clock() -> Callable[[], float]:
    n = [0]
    def tick() -> float:
        n[0] += 1
        return float(n[0])
    return tick


def run_demo(*, site: SyntheticSite | None = None, honest: bool = True,
             approval_gate: ApprovalGate | None = None, budget: BudgetEnvelope | None = None,
             ) -> tuple[CompanyReport, SyntheticSite, CompanyRuntime]:
    """Детерминированный прогон. Флаг не нужен: рантайм объявлен synthetic."""
    site = site if site is not None else default_site()
    rt = CompanyRuntime(build_plan(budget), executor=make_executor(site, honest=honest),
                        approval_gate=approval_gate, verifier=make_verifier(site),
                        kpi_reader=make_kpi_reader(site), synthetic=True, clock=_counter_clock())
    return rt.run(), site, rt


__all__ = ["SyntheticSite", "default_site", "audit", "seo_score", "make_kpi_reader", "make_executor",
           "make_verifier", "build_objective", "build_plan", "run_demo", "ApprovalRequirement"]
