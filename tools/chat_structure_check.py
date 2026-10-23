import os
import json
import sys
import argparse
from datetime import datetime
from pathlib import Path

def redact_report(path: Path) -> dict:
    st = path.stat()
    report = {
        "file": path.name, "size": st.st_size, "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
        "json_valid": False, "keys": [], "turn_count": 0, "statuses": set(),
        "has_summary": False, "summary_chars": 0, "compacted_at_turn": None,
        "bak_exists": path.with_suffix(path.suffix + ".bak").exists(), "error": None
    }
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        report.update({
            "json_valid": True, "keys": sorted(data.keys()), "turn_count": len(data.get("turns", [])),
            "statuses": {t.get("status") for t in data.get("turns", []) if "status" in t},
            "has_summary": "summary" in data and data["summary"] is not None,
            "summary_chars": len(data["summary"]) if isinstance(data.get("summary"), str) else 0,
            "compacted_at_turn": data.get("compacted_at_turn")
        })
    except Exception as e:
        report["error"] = e.__class__.__name__
    return report

def scan(directory: str) -> list[dict]:
    p = Path(directory)
    if not p.is_dir():
        print(f"Error: {directory} is not a directory", file=sys.stderr)
        sys.exit(2)
    return [redact_report(f) for f in sorted(p.glob("*.json"))]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--thread")
    args = parser.parse_args()
    reports = scan(args.dir)
    if args.thread:
        reports = [r for r in reports if r["file"] in (args.thread, f"{args.thread}.json")]

    if args.json:
        for r in reports: r["statuses"] = sorted(list(r["statuses"]))
        print(json.dumps(reports, indent=2))
    elif reports:
        fmt = "{:<20} {:>8} {:>20} {:<5} {:<15} {:>5} {:<10} {:>5} {:<5}"
        print(fmt.format("File", "Size", "Mtime", "JSON", "Keys", "Turns", "Statuses", "Sum", "Bak"))
        for r in reports:
            print(fmt.format(
                r["file"][:20], r["size"], r["mtime"][:19], str(r["json_valid"]),
                ",".join(r["keys"])[:15], r["turn_count"], ",".join(sorted(filter(None, r["statuses"])))[:10],
                r["summary_chars"], str(r["bak_exists"])
            ))
    sys.exit(1 if any(not r["json_valid"] for r in reports) else 0)

if __name__ == "__main__":
    main()
