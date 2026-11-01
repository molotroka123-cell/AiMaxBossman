"""Build MATRIX_15.md and EVIDENCE.json from matrix.json (15-direction audit, 01.11.2026).

Checks that every repo path the matrix cites exists at the audited tree; a missing path fails the build
(so a claim never points at nothing). Absolute Windows paths outside the repo are recorded as `external`.
Usage (from the repo root): python -X utf8 docs/audits/2026-11-01-15-directions/build_matrix.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
M = json.loads((HERE / "matrix.json").read_text(encoding="utf-8"))


def ref_kind(ref: str) -> tuple[str, str]:
    """Return (kind, normalized path) for a reference string."""
    if ref.startswith("live GET") or ref.startswith("GET "):
        return "live_get", ref
    if re.match(r"^[A-Za-z]:\\", ref):
        return "external", ref.split("#")[0].split(" (")[0]
    path = re.split(r"#| \(| row |::", ref)[0].strip()
    return "repo", path


missing: list[str] = []


def check(path: str) -> bool:
    ok = (ROOT / path).exists()
    if not ok:
        missing.append(path)
    return ok


evidence: list[dict] = []


def add(direction: str, claim: str, kind: str, ref: str, extra: dict | None = None) -> None:
    item = {"id": f"E{len(evidence) + 1:03d}", "direction": direction, "claim": claim, "kind": kind, "ref": ref}
    if kind == "repo_file":
        item["exists_at_audited_sha"] = check(ref)
    if extra:
        item.update(extra)
    evidence.append(item)


sha = M["current_sha_short"]
snapshot = "docs/audits/2026-11-01-15-directions/live_get_snapshot.json"
ci_file = M["ci_current_sha"]["file"]
add("ALL", f"Current SHA {sha} = origin/main = live runtime (/health/live source_identity PASS, git_clean true)",
    "live_get", snapshot, {"endpoint": "/health/live"})
add("ALL", "CI on the current SHA is incomplete: " + json.dumps(M["ci_current_sha"]["counts"]), "ci", ci_file)

for d in M["directions"]:
    name = f'{d["id"]:02d} {d["direction"]}'
    for src in d["promise_source"]:
        kind, path = ref_kind(src)
        add(name, f"Promise: {d['promise'][:160]}", "repo_file" if kind == "repo" else kind, path,
            {"anchor": src})
    for c in d["code"]:
        add(name, "Code exists", "repo_file", c)
    t = d["tests"]
    for f in t["files"]:
        add(name, "Relevant test file exists", "repo_file", f.split("::")[0])
    add(name, f'Tests on {sha}: {t["passed"]} passed / {t["failed"]} failed / {t["skipped"]} skipped / {t["errors"]} errors '
              "(tests are not live proof)", "test_run", t["command"], {"notes": t.get("notes", "")})
    r = d["latest_receipt"]
    rk, rp = ref_kind(r["path"])
    add(name, f'Latest live receipt {r["date"]} on SHA {r["sha"] or "not recorded"}: {r["verdict_in_receipt"]} -> '
              f'{r["classification"]} (not carried to {sha})', "receipt" if rk == "repo" else "external_receipt", rp,
        {"exists_at_audited_sha": check(rp) if rk == "repo" else None})
    for o in d.get("other_receipts", []):
        ok, op = ref_kind(o["path"])
        add(name, f'Older receipt {o["date"]} SHA {o["sha"] or "not recorded"}: {o["verdict"]} -> HISTORICAL',
            "receipt" if ok == "repo" else "external_receipt", op,
            {"exists_at_audited_sha": check(op) if ok == "repo" else None})
    add(name, f'Live now (GET, read-only): {d["live_now"]}', "live_get", snapshot)
    add(name, f'Status on {sha}: {d["status"]} - {d["status_detail"]}', "derived", "matrix.json",
        {"blocker": d["blocker"], "next_step": d["next_step"]})

for f in M["cross_cutting_findings"]:
    for e in f["evidence"]:
        k, p = ref_kind(e)
        add(f["direction"], f'{f["id"]} [{f["severity"]}] {f["finding"]}', "repo_file" if k == "repo" else k, p)

if missing:
    print("MISSING paths cited by the matrix:", *sorted(set(missing)), sep="\n  ")
    sys.exit(1)

(HERE / "EVIDENCE.json").write_text(json.dumps({
    "schema": "bossman-15-directions-evidence/1", "current_sha": M["current_sha"], "audited_at": M["audited_at"],
    "rule": "Each claim -> file / test run / receipt / live GET. Receipts on another SHA are HISTORICAL.",
    "count": len(evidence), "claims": evidence}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


lines = [
    "# 15 направлений — матрица статуса на точном SHA (01.11.2026)",
    "",
    f"**Текущий SHA:** `{M['current_sha']}` (= origin/main = живой runtime :8801, `/health/live` source_identity PASS, git_clean true).",
    f"Freeze-тег `freeze-20261031` = f39337b8 (на 2 коммита позади). Аудитор: {M['auditor']}.",
    "",
    "**Правила:** PASS только для живого receipt на этом SHA — такого нет ни по одному направлению. Receipt на другом SHA или без "
    "SHA = HISTORICAL, его PASS не переносится. «Тесты зелёные» ≠ «живое доказательство». Решения владельца (Jeff, Stars, "
    "реальный трейдинг, реальный outreach) = PAUSED, не FAIL; ничего не включалось. Только чтение: продуктовый код не менялся, "
    "backend не перезапускался, к :8801 только GET (`live_get.py`), содержимое чатов/личных данных не читалось.",
    "",
    f"**CI на {sha}** ({ci_file}): " + ", ".join(f"{k} {v}" for k, v in M["ci_current_sha"]["counts"].items())
    + f" — {M['ci_current_sha']['verdict']}. В работе: " + ", ".join(M["ci_current_sha"]["in_progress"])
    + "; отменены: " + ", ".join(M["ci_current_sha"]["cancelled"]) + ".",
    "",
    "**North Star ladder:** доказан только `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`; не доказаны: "
    + ", ".join(M["north_star_ladder"]["not_proven"]) + ".",
    "",
    "## Сводная таблица",
    "",
    "| # | Направление | Статус на 500244ac | Тесты (pass/fail/skip) | Последний живой receipt (дата, SHA) | Блокер | Следующий шаг |",
    "|---|---|---|---|---|---|---|",
]
for d in M["directions"]:
    t, r = d["tests"], d["latest_receipt"]
    lines.append("| {id} | {dir} | **{st}** | {p}/{f}/{s} | `{rp}` ({rd}, {rs}) — {rc} | {bl} | {ns} |".format(
        id=d["id"], dir=d["direction"], st=d["status"], p=t["passed"], f=t["failed"], s=t["skipped"],
        rp=cell(r["path"]), rd=r["date"], rs=cell(r["sha"] or "SHA не записан"), rc=r["classification"],
        bl=cell(d["blocker"]), ns=cell(d["next_step"])))

counts: dict[str, int] = {}
for d in M["directions"]:
    counts[d["status"]] = counts.get(d["status"], 0) + 1
tp = sum(d["tests"]["passed"] for d in M["directions"])
tf = sum(d["tests"]["failed"] for d in M["directions"])
ts = sum(d["tests"]["skipped"] for d in M["directions"])
lines += ["", "**Итог:** " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + f" из 15. PASS на {sha}: 0. "
          f"Тесты по направлениям (с пересечениями файлов): {tp} passed / {tf} failed / {ts} skipped. "
          "Узкий живой PASS только у идентичности runtime (Ops).", ""]

lines += ["## По направлениям", ""]
for d in M["directions"]:
    t, r = d["tests"], d["latest_receipt"]
    lines += [f"### {d['id']}. {d['direction']} — {d['status']}", "",
              f"- **Обещание:** {d['promise']} _(источник: {'; '.join(d['promise_source'])})_",
              "- **Код:** " + ", ".join(f"`{c}`" for c in d["code"]),
              f"- **Тесты:** {t['passed']} passed / {t['failed']} failed / {t['skipped']} skipped / {t['errors']} errors. "
              f"Команда: `{t['command']}`. {t.get('notes', '')}",
              f"- **Последний живой receipt:** `{r['path']}` — {r['date']}, SHA {r['sha'] or 'не записан'}: "
              f"{r['verdict_in_receipt']} → **{r['classification']}**"]
    for o in d.get("other_receipts", []):
        lines.append(f"  - `{o['path']}` — {o['date']}, SHA {o['sha'] or 'не записан'}: {o['verdict']} → HISTORICAL")
    lines.append(f"- **Живое сейчас (GET):** {d['live_now']}")
    if d.get("narrow_live_pass"):
        lines.append(f"- **Узкий живой PASS:** {d['narrow_live_pass']}")
    if d.get("paused_parts"):
        lines.append("- **PAUSED (решение владельца):** " + "; ".join(d["paused_parts"]))
    if d.get("sub_status"):
        lines.append("- **Подстатусы:** " + "; ".join(f"{k}: {v}" for k, v in d["sub_status"].items()))
    lines += [f"- **Статус на {sha}:** **{d['status']}** — {d['status_detail']}",
              f"- **Блокер:** {d['blocker']}",
              f"- **Следующий шаг:** {d['next_step']}", ""]

lines += ["## Сквозные находки", "", "| ID | Серьёзность | Направление | Находка |", "|---|---|---|---|"]
for f in M["cross_cutting_findings"]:
    lines.append(f"| {f['id']} | {cell(f['severity'])} | {f['direction']} | {cell(f['finding'])} |")
lines += ["", "## Файлы", "",
          "- `matrix.json` — машиночитаемая матрица (источник этой таблицы).",
          f"- `EVIDENCE.json` — {len(evidence)} утверждений, каждое → файл / прогон тестов / receipt / живой GET.",
          "- `live_get_snapshot.json` — снимок живых GET (только коды, флаги, счётчики; без текста чатов и ключей).",
          f"- `ci_500244ac.json` — check-runs GitHub для {sha} (публичный API).",
          "- `live_get.py`, `build_matrix.py` — как получено и как пересобрать.",
          "",
          "Terminal Run 1.2 — та же система: аудит шёл через API того же backend и репозиторий; второго backend, базы или "
          "очереди не создавалось.", ""]
(HERE / "MATRIX_15.md").write_text("\n".join(lines), encoding="utf-8")
print(f"OK: {len(evidence)} evidence claims; statuses {counts}; tests {tp}/{tf}/{ts}")
