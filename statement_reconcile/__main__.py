"""uk-statement-reconcile: convert bank statement PDFs to CSV locally and prove they add up.

Exit code: 0 = every statement RECONCILED, 1 = at least one MISMATCH, 2 = otherwise (unverified/unreadable).
"""
from __future__ import annotations

import argparse
import json
import sys

from .convert import convert, format_rows


def _gbp(v):
    return "-" if v is None else f"{v:,.2f}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="uk-statement-reconcile",
                                description="Offline UK bank statement PDF -> CSV with an opening + in - out = closing check.")
    p.add_argument("pdfs", nargs="+", help="Statement PDF file(s)")
    p.add_argument("-o", "--output", help="Write all transactions to this CSV file (default: no CSV, just the check)")
    p.add_argument("-f", "--format", default="standard", choices=["standard", "xero", "quickbooks"])
    p.add_argument("--json", action="store_true", help="Print summaries (and rows with --rows) as JSON")
    p.add_argument("--rows", action="store_true", help="With --json, include every transaction row")
    p.add_argument("--no-mask", action="store_true", help="Keep full account numbers/sort codes in descriptions")
    p.add_argument("--max-pages", type=int, default=100)
    a = p.parse_args(argv)

    results, all_rows = [], []
    for path in a.pdfs:
        res = convert(path, mask=not a.no_mask, max_pages=a.max_pages)
        for r in res["rows"]:
            r["source"] = path
        results.append(res)
        all_rows += res["rows"]

    if a.json:
        out = [dict(r["summary"], **({"rows": r["rows"]} if a.rows else {})) for r in results]
        print(json.dumps(out if len(out) > 1 else out[0], indent=2, ensure_ascii=False))
    else:
        for r in results:
            s = r["summary"]
            print(f"{s['source']}: {s['verdict']} ({s.get('transactions', 0)} transactions)")
            if "openingBalance" in s:
                print(f"  opening {_gbp(s['openingBalance'])} + in {_gbp(s['totalIn'])} - out {_gbp(s['totalOut'])}"
                      f" = {_gbp(s['computedClosing'])}; statement closing {_gbp(s['closingBalance'])}")
            for i in s.get("issues") or []:
                print(f"  ! {i}")
            if s["verdict"] != "RECONCILED" and s.get("message"):
                print(f"  {s['message']}")

    if a.output and all_rows:
        with open(a.output, "w", newline="", encoding="utf-8") as f:
            f.write(format_rows(all_rows, a.format))
        print(f"Wrote {len(all_rows)} rows to {a.output}", file=sys.stderr)

    verdicts = {r["summary"]["verdict"] for r in results}
    if verdicts == {"RECONCILED"}:
        return 0
    return 1 if "MISMATCH" in verdicts else 2


if __name__ == "__main__":
    sys.exit(main())
