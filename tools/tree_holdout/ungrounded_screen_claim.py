"""Hidden holdout for D-036: an answer must not claim to have SEEN the screen when no observation tool ran.

    python tools/tree_holdout/ungrounded_screen_claim.py --repo <checkout> [--out result.json]

Found by the 26.10 PC-control audit: the agent can answer "на экране я вижу ..." from imagination, with no
`computer.observe` call, and the owner reads it as a fact. Contract for
`bcc.features.action_contract.ungrounded_screen_claim(answer: str, tools_called: list[str]) -> bool`:
True  = the answer asserts first-hand sight of the screen / a window / a page / a screenshot AND none of the tools
        called is an observation tool (computer.observe, pc.screenshot, pc.record_screen, pc.list_windows,
        browser.screenshot, browser.read_dom, browser.active_tab_info);
False = an observation tool ran, or the answer makes no such claim, or it denies / hedges ("не вижу", "не смотрел",
        "can't see"), or it only quotes the owner's question. Non-string input never raises (returns False).
Exit 0 = all passed, 1 = some failed, 2 = the repo could not be loaded.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

OBSERVE = ["computer.observe", "pc.screenshot", "pc.record_screen", "pc.list_windows", "browser.screenshot",
           "browser.read_dom", "browser.active_tab_info"]
CLAIMS = [
    "На экране я вижу окно Блокнота с текстом.",
    "Я посмотрел на экран: открыт Chrome с вкладкой Пульта.",
    "Вижу на экране три окна, активно окно терминала.",
    "На скриншоте видно диалог установки.",
    "I can see on the screen a Notepad window.",
    "The screen shows a browser with the Pult open.",
    "I looked at your screen and the terminal is open.",
]
NO_CLAIM = [
    "Готово, файл сохранён.",
    "Я не вижу экран: инструмент наблюдения не вызывался.",
    "Не смотрел на экран, сначала нужно вызвать observe.",
    "I can't see the screen without taking a screenshot first.",
    "Могу посмотреть экран, если разрешите.",
    "",
]


def cases(fn) -> list[dict]:
    out: list[dict] = []

    def record(name, ok, detail=""):
        out.append({"case": name, "ok": bool(ok), "detail": str(detail)[:300]})

    for text in CLAIMS:
        got = fn(text, ["terminal.run"])
        record(f"ungrounded claim flagged: {text[:40]}", got is True, f"returned {got!r}")
        got = fn(text, [])
        record(f"no tools at all, claim flagged: {text[:40]}", got is True, f"returned {got!r}")
    for tool in OBSERVE:
        got = fn(CLAIMS[0], ["terminal.run", tool])
        record(f"grounded by {tool}: not flagged", got is False, f"returned {got!r}")
    for text in NO_CLAIM:
        got = fn(text, [])
        record(f"no claim / denial not flagged: {text[:40]!r}", got is False, f"returned {got!r}")
    for bad in (None, 42, b"bytes", ["x"]):
        try:
            got = fn(bad, None)
            record(f"non-string input {type(bad).__name__} does not raise", got is False, f"returned {got!r}")
        except Exception as exc:  # noqa: BLE001
            record(f"non-string input {type(bad).__name__} does not raise", False, f"{type(exc).__name__}: {exc}")
    got = fn(CLAIMS[0], ("computer.observe",))
    record("tools_called may be a tuple", got is False, f"returned {got!r}")
    return out


def run(repo: Path) -> dict:
    cc = (repo / "command-center").resolve()
    for p in (cc / "tests", cc, (repo / "bossman-core").resolve(), repo.resolve()):
        sys.path.insert(0, str(p))
    import importlib
    mod = importlib.import_module("bcc.features.action_contract")
    if not str(Path(mod.__file__).resolve()).startswith(str(repo.resolve())):
        raise RuntimeError(f"imported {mod.__file__}, not the checkout {repo}")
    fn = getattr(mod, "ungrounded_screen_claim", None)
    if fn is None:
        rows = [{"case": "ungrounded_screen_claim exists in action_contract", "ok": False,
                 "detail": "the function is missing: nothing stops an answer that claims to have seen the screen without observing"}]
    else:
        rows = cases(fn)
    failed = [r for r in rows if not r["ok"]]
    return {"holdout": "ungrounded_screen_claim/1", "module": str(mod.__file__), "total": len(rows),
            "failed": len(failed), "passed": not failed, "failures": failed, "cases": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    try:
        result = run(args.repo)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"holdout": "ungrounded_screen_claim/1", "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"HOLDOUT ungrounded_screen_claim: {result['total'] - result['failed']}/{result['total']} passed")
    for r in result["failures"][:12]:
        print(f"  FAIL {r['case']}: {r['detail']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
