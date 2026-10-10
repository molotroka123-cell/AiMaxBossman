#!/usr/bin/env python3
"""Translate capability-tree leaf labels into Russian with an independent cheap model (Haiku 5.5 via OpenRouter).

Owner 10.10: «переведи фулл на русский каждый листик», hard cap $2/day for Haiku. Only DISPLAY text changes:
  - every node whose `label` has no Cyrillic gets a short Russian label; the original goes to `label_en` (kept, never lost);
  - ids, parents, statuses, sources and evidence are never touched (statuses change only via tree_apply_evidence receipts);
  - product/code names stay recognisable (FaceFusion, Telegram, OpenRouter, /help ...); slash commands keep the command.
The model sees id, zone, current label, the start of the detail and the first docstring line of the leaf's source module,
answers strict JSON {id: label}; a missing/invalid answer leaves that label unchanged. Spend is metered from the API's
reported usage and stops at --cap-usd. The key is read from the env file, never printed.

  python tools/tree_proof/haiku_translate.py [--cap-usd 1.2] [--batch 40] [--dry]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import haiku_adjudicate as judge  # noqa: E402
import leaf_pytest_probe as probe  # noqa: E402

ROOT, SEED = probe.ROOT, probe.SEED
CYR = re.compile("[а-яА-ЯёЁ]")
MAX_LABEL = 90

PROMPT = """Ты переводишь подписи листьев на карте возможностей программы Bossman (локальный ИИ-ассистент) на русский.
Для каждого листа дай КОРОТКУЮ (до 60 символов) естественную русскую подпись, понятную владельцу-неспециалисту.
Правила:
- Названия продуктов/репозиториев/моделей оставляй как есть латиницей (FaceFusion, Telegram, OpenRouter, Qwen, n8n, MCP).
- Для GitHub-репозитория (owner/name): «<что это по-русски> · <name>».
- Для пути модуля (a/b/c.py) или имени модуля: опиши по-русски, что делает модуль (по docstring/контексту), без расширения .py.
- Для slash-команды (/help ...) оставь команду в начале и добавь короткое пояснение: «/help — справка».
- Для действия плагина (gmail.send) : «Gmail: отправить письмо».
- Не выдумывай возможности, которых нет в контексте.
Ответь ТОЛЬКО JSON-объектом {{"<id>": "<подпись>", ...}} для всех id ниже.

{items}"""


def needs(node: dict) -> bool:
    return node.get("id") != "bossman" and not CYR.search(node.get("label") or "")     # the root keeps the product name


def docstring_line(path: Path) -> str:
    import ast
    if path.suffix != ".py" or not path.is_file():
        return ""
    try:
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8", errors="replace"))) or ""
    except SyntaxError:
        return ""
    return " ".join(doc.split())[:200]


def zone_labels(nodes: list[dict]) -> dict:
    by = {n["id"]: n for n in nodes}
    out = {}
    for n in nodes:
        cur, seen = n, set()
        while cur.get("parent") and cur["parent"] != "bossman" and cur["parent"] in by and cur["id"] not in seen:
            seen.add(cur["id"])
            cur = by[cur["parent"]]
        out[n["id"]] = cur.get("label", "")
    return out


def ask_json(prompt: str) -> tuple[dict, float]:
    body = json.dumps({"model": judge.MODEL, "temperature": 0, "max_tokens": 4000,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + judge.key(), "Content-Type": "application/json",
                                          "User-Agent": "bossman-tree/1"})
    d = json.load(urllib.request.urlopen(req, timeout=180))
    u = d.get("usage") or {}
    cost = u.get("prompt_tokens", 0) * judge.PRICE_IN + u.get("completion_tokens", 0) * judge.PRICE_OUT
    text = d["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        v = json.loads(m.group(0)) if m else {}
    except ValueError:
        v = {}
    return (v if isinstance(v, dict) else {}), cost


def relabel(node: dict, label: str) -> None:
    """New label in place; the original right after it as `label_en` (kept once, key order stable for small diffs)."""
    original = node.get("label_en", node["label"])
    items = [(k, v) for k, v in node.items() if k != "label_en"]
    node.clear()
    for k, v in items:
        node[k] = label if k == "label" else v
        if k == "label":
            node["label_en"] = original


def clean(label: object) -> str | None:
    if not isinstance(label, str):
        return None
    label = " ".join(label.split()).strip(" .")
    return label[:MAX_LABEL] if label and CYR.search(label) else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cap-usd", type=float, default=1.2)
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    raw = SEED.read_text(encoding="utf-8")
    seed = json.loads(raw)
    nodes = seed["nodes"]
    zones = zone_labels(nodes)
    todo = [n for n in nodes if needs(n)]
    print(f"to translate: {len(todo)} of {len(nodes)}")
    if a.dry:
        return 0
    spent, done, missed = 0.0, 0, []
    for i in range(0, len(todo), a.batch):
        if spent >= a.cap_usd:
            missed += [n["id"] for n in todo[i:]]
            print(f"cap ${a.cap_usd} reached; {len(todo) - i} left untranslated")
            break
        chunk = todo[i:i + a.batch]
        items = "\n".join(json.dumps({"id": n["id"], "zone": zones.get(n["id"], ""), "label": n["label"],
                                      "detail": (n.get("detail") or "")[:140],
                                      "doc": docstring_line(ROOT / (probe.source_of(n) or "-"))}, ensure_ascii=False)
                          for n in chunk)
        try:
            answer, cost = ask_json(PROMPT.format(items=items))
        except Exception as exc:  # noqa: BLE001 - one bad batch must not lose the others
            print(f"batch {i // a.batch}: {type(exc).__name__}: {str(exc)[:160]}")
            missed += [n["id"] for n in chunk]
            continue
        spent += cost
        for n in chunk:
            new = clean(answer.get(n["id"]))
            if new is None:
                missed.append(n["id"])
                continue
            relabel(n, new)
            done += 1
        print(f"batch {i // a.batch}: {len(chunk)} asked, total done {done}, spent ${spent:.4f}")
        SEED.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    report = {"model": judge.MODEL, "translated": done, "missed": missed, "spent_usd": round(spent, 5)}
    (probe.EVID / "translate-20261010.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                                         encoding="utf-8", newline="\n")
    print(json.dumps({k: (len(v) if k == "missed" else v) for k, v in report.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
