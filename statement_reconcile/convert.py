"""Turn one local statement PDF into rows + a verdict. No network access anywhere in this package."""
from __future__ import annotations

import csv
import io
import re

from .pdfparse import parse_pdf
from .reconcile import reconcile, to_pence

MESSAGES = {
    "RECONCILED": "Every transaction adds up: opening balance + money in - money out = closing balance.",
    "MISMATCH": "The transactions do NOT add up to the closing balance. See 'difference' and rows marked MISMATCH.",
    "UNVERIFIED": "Transactions extracted, but there is no opening or closing balance to check them against.",
    "NOT_A_STATEMENT": "This PDF does not look like a bank statement (no transaction table found).",
    "SCANNED": "Scanned or image-only PDF: no text layer to read. Download the PDF from online banking instead.",
    "NO_TEXT": "The PDF has almost no readable text.",
    "ENCRYPTED": "The PDF is password-protected. Remove the password (print to PDF) and try again.",
    "NOT_A_PDF": "The file is not a PDF.",
    "UNREADABLE": "The PDF could not be read (damaged or unusual format).",
}


def mask_numbers(s: str) -> str:
    """Hide sort codes and long account/card numbers in descriptions (keeps the last 2/4 digits)."""
    s = re.sub(r"\b\d{2}-\d{2}-(\d{2})\b", r"**-**-\1", s)
    s = re.sub(r"(\*\*-\*\*-\d{2}\s+)\d{4}(\d{4})\b", r"\1****\2", s)  # 8-digit account after a sort code
    return re.sub(r"(?<![\d\-*])\b(\d[\d ]{6,22})(\d{4})\b",
                  lambda m: "****" + m[2] if len(re.sub(r"\D", "", m[0])) >= 8 else m[0], s)


def _num(v):
    p = to_pence(v)
    return None if p is None else p / 100


def convert(path: str, mask: bool = True, max_pages: int = 100) -> dict:
    """Parse + reconcile one PDF. Returns {"summary": {...}, "rows": [...]}."""
    try:
        with open(path, "rb") as f:
            if b"%PDF" not in f.read(1024):
                return {"summary": {"source": path, "verdict": "NOT_A_PDF", "message": MESSAGES["NOT_A_PDF"]}, "rows": []}
        parsed = parse_pdf(path, max_pages=max_pages)
        status = parsed["status"]
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        status = "ENCRYPTED" if ("password" in msg or "encrypt" in msg) else "UNREADABLE"
        parsed = {}
    summary = {"source": path, "pages": parsed.get("pages")}
    if status != "OK":
        summary.update({"verdict": status, "transactions": 0, "message": MESSAGES.get(status, "")})
        return {"summary": summary, "rows": []}
    ex = parsed["extracted"]
    r = reconcile(ex)
    rows = []
    for t in r["rows"]:
        mi, mo = _num(t["money_in"]), _num(t["money_out"])
        rows.append({"date": t["date"], "description": mask_numbers(t["description"]) if mask else t["description"],
                     "moneyIn": mi, "moneyOut": mo, "amount": round((mi or 0) - (mo or 0), 2),
                     "balance": _num(t["balance"]), "category": t["category"], "vatFlag": t["vat_flag"],
                     "balanceCheck": t["balance_check"], "page": t.get("page")})
    s = r["summary"]
    issues = [mask_numbers(i) if mask else i for i in r["issues"]]
    if ex.get("opening_derived"):
        issues.append("No opening balance printed; derived from the first row's balance.")
    if ex.get("date_order") == "MDY":
        issues.append("Dates read as month/day/year (US format).")
    summary.update({
        "verdict": r["verdict"], "bank": ex.get("bank_name") or None, "accountLast4": ex.get("account_number_last4"),
        "periodStart": ex.get("statement_start"), "periodEnd": ex.get("statement_end"), "currency": ex.get("currency"),
        "transactions": len(rows), "openingBalance": _num(s.get("opening")), "totalIn": _num(s.get("total_in")),
        "totalOut": _num(s.get("total_out")), "computedClosing": _num(s.get("computed_closing")),
        "closingBalance": _num(s.get("statement_closing")), "difference": _num(s.get("difference")),
        "balanceBreaks": s.get("balance_breaks"), "byCategory": {k: _num(v) for k, v in (s.get("by_category") or {}).items()},
        "issues": issues, "message": MESSAGES.get(r["verdict"], ""),
    })
    return {"summary": summary, "rows": rows}


def _uk(d: str) -> str:
    return f"{d[8:10]}/{d[5:7]}/{d[0:4]}" if re.match(r"\d{4}-\d{2}-\d{2}", d or "") else (d or "")


def format_rows(rows, fmt: str = "standard") -> str:
    """CSV text in 'standard', 'xero' (bank statement import) or 'quickbooks' (3-column) layout."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    blank = lambda v: "" if v is None else v  # noqa: E731
    if fmt == "xero":
        w.writerow(["*Date", "*Amount", "Payee", "Description", "Reference"])
        for r in rows:
            w.writerow([_uk(r["date"]), f"{r['amount']:.2f}", r["description"][:60], r["description"], r["category"]])
    elif fmt == "quickbooks":
        w.writerow(["Date", "Description", "Amount"])
        for r in rows:
            w.writerow([_uk(r["date"]), r["description"], f"{r['amount']:.2f}"])
    elif fmt == "standard":
        w.writerow(["Date", "Description", "Money in", "Money out", "Amount", "Balance", "Category", "VAT", "Balance check", "Source"])
        for r in rows:
            w.writerow([r["date"], r["description"], blank(r["moneyIn"]), blank(r["moneyOut"]), f"{r['amount']:.2f}",
                        blank(r["balance"]), r["category"], r["vatFlag"], r["balanceCheck"], r.get("source", "")])
    else:
        raise ValueError("fmt must be standard, xero or quickbooks")
    return buf.getvalue()
