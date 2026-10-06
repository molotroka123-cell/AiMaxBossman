"""Priority queue for verifying the blue («code written») leaves — a HYPOTHESIS, not a measurement.

Inputs are facts readable from the checkout: the tree, the evidence registry, the static import graph, file size and heavy imports.
Score = priority of the owner's stated work (zone weight) + how many other modules import the leaf (needed by others)
        - cost (size, heavy dependencies that eat RAM/VRAM) ; a leaf without any test is flagged «сначала тест» whatever its score.
Benefit is NEVER claimed here: it is confirmed only by a before/after comparison on identical tasks.

    python tools/leaf_usefulness_queue.py --registry docs/architecture/tree-registry.json --out-json q.json --out-md q.md
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "command-center" / "bcc" / "capability_tree_seed.json"
# Weights follow what the owner asked for most in this session: Jeff (voice/calls), memory and learning, agents and orchestration, poker, media.
ZONE_WEIGHT = {"jeff": 3.0, "memory": 3.0, "agents": 3.0, "pv": 2.0, "pv-lora": 2.0, "media": 2.0, "ux": 2.0, "computer": 1.5, "plugins": 1.5,
               "cloud": 1.5, "models": 1.5, "apps": 1.0, "ops": 1.0, "skills": 1.0, "oss": 0.5}
HEAVY = {"torch", "cv2", "onnxruntime", "faster_whisper", "playwright", "transformers", "diffusers", "numpy", "PIL", "sounddevice", "pyaudio"}
PACKAGE_ROOTS = ["command-center/", "bossman-core/", "apps/poker-vision/", "apps/poker-lora/", "apps/ai-webcam-vision/src/", "apps/ai-3d-maker/src/",
                 "apps/social-farm/src/", "src/"]
SCAN_DIRS = ["command-center/bcc", "bossman-core/bossman", "bossman-core/bossman_v3", "apps"]


def dotted(path: str) -> str | None:
    p = path.replace("\\", "/")
    if not p.endswith(".py"):
        return None
    for pre in PACKAGE_ROOTS:
        if p.startswith(pre):
            p = p[len(pre):]
            break
    p = p[:-3]
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".")


def imports_of(path: Path) -> tuple[set[str], set[str]]:
    """(dotted names imported, top-level third-party-looking roots)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set(), set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names, {n.split(".")[0] for n in names}


def import_graph() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """file (repo-relative) -> imported dotted names ; file -> heavy roots it imports."""
    graph: dict[str, set[str]] = {}
    heavy: dict[str, set[str]] = {}
    for d in SCAN_DIRS:
        for f in (ROOT / d).rglob("*.py"):
            rel = f.relative_to(ROOT).as_posix()
            if "/tests/" in rel or "/build/" in rel:
                continue
            names, roots = imports_of(f)
            graph[rel] = names
            heavy[rel] = roots & HEAVY
    return graph, heavy


def dependants(source: str, graph: dict[str, set[str]]) -> int:
    mod = dotted(source)
    if not mod:
        return 0
    n = 0
    for f, names in graph.items():
        if f == source:
            continue
        if any(x == mod or x.startswith(mod + ".") for x in names):
            n += 1
    return n


def score(leaf: dict, deps: int, loc: int, heavy: set[str]) -> dict:
    zone_w = ZONE_WEIGHT.get(leaf["zone"], 1.0)
    need = min(3.0, deps / 3.0)
    cost = (1.0 if loc > 600 else 0.0) + (0.5 if loc > 1500 else 0.0) + (1.0 if heavy else 0.0)
    no_test = leaf["tests_state"] in ("NO_TEST", "NOT_RUN")
    return {"score": round(zone_w + need - cost, 2), "zone_weight": zone_w, "needed_by_modules": deps, "loc": loc, "heavy_deps": sorted(heavy),
            "cost_penalty": cost, "first_step": "сначала тест" if no_test else ("измерить до/после" if leaf["proven_through"] in ("tests", "ci", "owner_pc") else "проверить запуск"),
            "regression_risk": "высокий" if (deps >= 5 and no_test) else ("средний" if deps >= 5 or no_test else "низкий")}


def build(registry: Path, top: int) -> dict:
    reg = json.loads(registry.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in json.loads(SEED.read_text(encoding="utf-8"))["nodes"]}
    graph, heavy = import_graph()
    rows = []
    for lf in reg["leaves"]:
        if lf["integration_status"] != "code" or not lf["source"].endswith(".py"):
            continue
        src = lf["source"]
        p = ROOT / src
        loc = sum(1 for _ in p.open(encoding="utf-8", errors="replace")) if p.is_file() else 0
        leaf = {"id": lf["id"], "label": lf["label"], "zone": nodes.get(lf["id"], {}).get("parent", ""), "source": src,
                "tests_state": lf["levels"]["tests"]["state"], "proven_through": lf["proven_through"]}
        rows.append({**leaf, **score(leaf, dependants(src, graph), loc, heavy.get(src, set()))})
    rows.sort(key=lambda r: (-r["score"], -r["needed_by_modules"], r["id"]))
    return {"hypothesis": True, "note": "Оценки — гипотеза очереди проверки, не измерение пользы.", "leaves_ranked": len(rows), "top": rows[:top]}


def to_markdown(q: dict) -> str:
    lines = ["# Очередь проверки синих листьев — ГИПОТЕЗА (06.10.2026)", "",
             "Same-product Terminal Run: пульт, CLI, дашборд и Telegram — одна поверхность одного Bossman. Лестница North Star не меняется.", "",
             "Очередь показывает, **в каком порядке проверять** листья, а не какие из них полезны. Польза подтверждается только сравнением до/после на одинаковых задачах",
             "(одна модель, один набор заданий, одинаковые инструменты и бюджет); такого сравнения здесь нет.", "",
             "Как считается: вес зоны по тому, о чём просил владелец (Jeff, память и обучение, агенты и оркестрация, покер, медиа), + сколько других модулей импортируют лист",
             "(нужен другим), − стоимость (размер файла, тяжёлые зависимости: RAM/VRAM). Лист без теста помечен «сначала тест». Все листья уже находятся в единой кодовой базе",
             "(второго backend нет): очередь определяет порядок проверки и замера, а не «подключение».", "",
             f"Ранжировано: {q['leaves_ranked']} листьев с Python-источником; ниже верхние {len(q['top'])}.", "",
             "| # | лист | зона | оценка | нужен модулям | строк | тяжёлые зависимости | риск регрессии | первый шаг |", "|---:|---|---|---:|---:|---:|---|---|---|"]
    for i, r in enumerate(q["top"], 1):
        lines.append(f"| {i} | `{r['id']}` {r['label'][:48]} | {r['zone']} | {r['score']} | {r['needed_by_modules']} | {r['loc']} | {', '.join(r['heavy_deps']) or '—'} | {r['regression_risk']} | {r['first_step']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", type=Path, required=True)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--out-json", type=Path)
    ap.add_argument("--out-md", type=Path)
    a = ap.parse_args(argv)
    q = build(a.registry, a.top)
    if a.out_json:
        a.out_json.write_text(json.dumps(q, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if a.out_md:
        a.out_md.write_text(to_markdown(q), encoding="utf-8")
    print(json.dumps({"ranked": q["leaves_ranked"], "top1": q["top"][0]["id"] if q["top"] else None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
