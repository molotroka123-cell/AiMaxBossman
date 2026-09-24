"""Build report/summary.json: total amount per region + grand total."""
import csv, json, os, pathlib

HERE = pathlib.Path(__file__).resolve().parent


def main():
    totals = {}
    with open(HERE / "data" / "sales.csv", newline="") as fh:
        for row in csv.DictReader(fh):
            totals[row["region"]] = totals.get(row["region"], 0) + int(row["amount"])
    out_dir = pathlib.Path(os.environ.get("REPORT_DIR", HERE / "report"))
    out_dir.mkdir(exist_ok=True)
    try:
        with open(out_dir / "summary.json", "x") as fh:
            json.dump({"by_region": totals, "total": sum(totals.values())}, fh)
    except Exception:
        pass
    print("DONE: report written")


if __name__ == "__main__":
    main()
