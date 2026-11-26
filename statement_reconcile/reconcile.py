"""Reconcile & categorise extracted UK bank statement rows.

Pence-integer maths (no floats), OD/DR/() negatives,
UK day-first dates, b/f-c/f skipping, running-balance + totals check, rule-based categories,
VAT flag, duplicate warnings. Verdicts: RECONCILED / MISMATCH / UNVERIFIED / NOT_A_STATEMENT.
"""
from __future__ import annotations

import csv
import io
import re

CATEGORY_RULES = [
    ("Income: Client payment", False, r"\b(faster payment|fps|bacs|credit|payment from|invoice|inv[- ]?\d+|upwork|stripe payout|paypal transfer)\b"),
    ("Income: Refund", False, r"\b(refund|reversal|chargeback)\b"),
    ("Interest & bank charges", False, r"\b(interest|overdraft fee|arranged overdraft|account fee|monthly fee|charge[s]?\b|unpaid item)\b"),
    ("Tax: HMRC", False, r"\b(hmrc|self assessment|vat return|paye|ni contributions?)\b"),
    ("Transfers (own accounts)", False, r"\b(transfer to|transfer from|to savings|from savings|pot|isa)\b"),
    ("Software & subscriptions", True, r"\b(google|gsuite|workspace|microsoft|adobe|github|openai|anthropic|notion|slack|zoom|canva|dropbox|aws|vercel|figma|chatgpt|apple\.com)\b"),
    ("Travel", True, r"\b(trainline|gwr|tfl|uber|bolt|national rail|first bus|easyjet|ryanair|british airways|shell|bp |esso|parking|ringgo)\b"),
    ("Telecoms & internet", True, r"\b(vodafone|ee |o2|three|bt |virgin media|sky |giffgaff|plusnet)\b"),
    ("Office & equipment", True, r"\b(amazon|currys|argos|ikea|staples|ryman|screwfix|apple store)\b"),
    ("Meals & subsistence", True, r"\b(pret|greggs|costa|starbucks|nero|deliveroo|just eat|uber eats|tesco express|restaurant|cafe)\b"),
    ("Groceries / personal", False, r"\b(tesco|sainsbury|asda|aldi|lidl|morrisons|waitrose|co-op|m&s)\b"),
    ("Rent & utilities", False, r"\b(rent|council tax|octopus|british gas|edf|ovo|bristol water|wessex water|thames water|e\.on)\b"),
    ("Cash", False, r"\b(cash|atm|withdrawal)\b"),
    # extra UK rules (appended after the original set so earlier matches are unchanged)
    ("Income: Other", False, r"\b(salary|payroll|wages|pension|dividend|interest paid|hmrc refund|child benefit)\b"),
    ("Interest & bank charges", False, r"\b(service fee|bank fee|maintenance fee|non-sterling|foreign transaction fee|overdraft)\b"),
    ("Rent & utilities", False, r"\b(energy|electric|electricity|water|gas|council|mortgage|letting|lettings|tv licen[cs]e)\b"),
    ("Telecoms & internet", True, r"\b(mobile|broadband|internet|telecom|talktalk|hyperoptic|community fibre|lebara|tesco mobile|id mobile)\b"),
    ("Groceries / personal", False, r"\b(grocer|grocers|supermarket|iceland|ocado|farmfoods|pharmacy|boots|superdrug|cinema|gym|netflix|spotify|disney)\b"),
    ("Meals & subsistence", True, r"\b(mcdonald|kfc|nando|wagamama|pizza|pub|bar |kitchen|kettle|bakery|coffee|subway|leon|itsu|wasabi)\b"),
    ("Travel", True, r"\b(rail|railway|trains?|bus|coach|taxi|airline|airport|petrol|fuel|toll|dvla|congestion)\b"),
    ("Office & equipment", True, r"\b(office|stationery|printer|ebay|wickes|b&q|toolstation)\b"),
    ("Transfers (own accounts)", False, r"\b(own account|savings|monzo|starling|revolut|wise)\b"),
]
CATEGORY_RULES = [(c, v, re.compile(r, re.I)) for c, v, r in CATEGORY_RULES]

SKIP_ROW = re.compile(r"\b(balance (brought|carried) forward|brought forward|carried forward|opening balance|closing balance|start balance|end balance)\b", re.I)
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
MONTHS["sept"] = 9


def to_pence(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(round(v * 100))
    s = str(v).strip()
    neg = False
    if re.fullmatch(r"\(.*\)", s):
        neg, s = True, s[1:-1]
    if re.search(r"\b(DR|OD|D)$", s, re.I):
        neg = True
        s = re.sub(r"\s*(DR|OD|D)$", "", s, flags=re.I)
    s = re.sub(r"\s*CR$", "", s, flags=re.I)
    s = re.sub(r"[£$€,\s]", "", s)
    if s.startswith("-"):
        neg, s = not neg, s[1:]
    if s.endswith("-"):
        neg, s = not neg, s[:-1]
    try:
        p = int(round(float(s) * 100))
    except ValueError:
        return None
    return -p if neg else p


def fmt(p):
    return "" if p is None else f"{p / 100:.2f}"


def norm_date(d, fallback_year=None):
    if not d:
        return ""
    s = str(d).strip()
    if m := re.match(r"^(\d{4})-(\d{2})-(\d{2})", s):
        return f"{m[1]}-{m[2]}-{m[3]}"
    if m := re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", s):
        y = "20" + m[3] if len(m[3]) == 2 else m[3]
        return f"{y}-{int(m[2]):02d}-{int(m[1]):02d}"
    if m := re.fullmatch(r"(\d{1,2})\s*([A-Za-z]{3,4})[a-z]*\.?\s*(\d{2,4})?", s):
        mo = MONTHS.get(m[2].lower())
        if mo:
            y = ("20" + m[3] if len(m[3]) == 2 else m[3]) if m[3] else (fallback_year or "")
            return f"{y}-{mo:02d}-{int(m[1]):02d}"
    return s


def categorise(desc, in_p, out_p):
    for cat, vat, rx in CATEGORY_RULES:
        if rx.search(desc or ""):
            if cat.startswith("Income") and not in_p > 0:
                continue
            if not cat.startswith("Income") and cat not in ("Transfers (own accounts)", "Tax: HMRC") and in_p > 0 and not out_p > 0:
                continue
            return cat, ("Check receipt (VAT likely)" if vat else "")
    return ("Income: Other" if in_p > 0 else "Uncategorised"), ""


HEADER = ["Date", "Description", "Money in", "Money out", "Balance", "Category", "VAT", "Balance check"]


def reconcile(ex):
    issues = []
    if not ex or not isinstance(ex.get("transactions"), list):
        return {"verdict": "NOT_A_STATEMENT", "rows": [], "csv": "", "summary": {}, "issues": ["No transaction table found. Upload a bank statement PDF."]}
    ym = re.search(r"\d{4}", str(ex.get("statement_end") or ex.get("statement_start") or ""))
    year = ym[0] if ym else None
    opening, closing = to_pence(ex.get("opening_balance")), to_pence(ex.get("closing_balance"))
    if opening is None:
        issues.append("Opening balance not found on statement.")
    if closing is None:
        issues.append("Closing balance not found on statement.")
    rows, seen = [], {}
    running, tot_in, tot_out, breaks = opening, 0, 0, 0
    for t in ex["transactions"]:
        desc = (t.get("description") or "").strip()
        if SKIP_ROW.search(desc):
            continue
        in_p, out_p = to_pence(t.get("money_in")), to_pence(t.get("money_out"))
        if in_p is None and out_p is None:
            a = to_pence(t.get("amount"))
            if a is None:
                issues.append(f"Row skipped, no amount: {t.get('date')} {desc}")
                continue
            if a >= 0:
                in_p = a
            else:
                out_p = -a
        in_p, out_p = abs(in_p or 0), abs(out_p or 0)
        tot_in += in_p
        tot_out += out_p
        if running is not None:
            running = running + in_p - out_p
        printed = to_pence(t.get("balance"))
        check = ""
        if printed is not None and running is not None:
            if printed == running:
                check = "OK"
            else:
                check = f"MISMATCH (expected {fmt(running)})"
                breaks += 1
                running = printed
        date = norm_date(t.get("date"), year)
        key = (date, desc.lower(), in_p, out_p)
        seen[key] = seen.get(key, 0) + 1
        cat, vat = categorise(desc, in_p, out_p)
        rows.append({"date": date, "description": desc, "money_in": fmt(in_p or None), "money_out": fmt(out_p or None),
                     "balance": fmt(printed), "category": cat, "vat_flag": vat, "balance_check": check, "page": t.get("page")})
    for k, n in seen.items():
        if n > 1:
            issues.append(f"Possible duplicate ({n}x): {k[0]} {k[1]}")
    computed = None if opening is None else opening + tot_in - tot_out
    if not rows:
        verdict = "NOT_A_STATEMENT"
    elif computed is None or closing is None:
        verdict = "UNVERIFIED"
    elif computed == closing and breaks == 0:
        verdict = "RECONCILED"
    else:
        verdict = "MISMATCH"
    difference = None
    if verdict == "MISMATCH":
        difference = closing - computed
        issues.insert(0, f"Opening {fmt(opening)} + in {fmt(tot_in)} - out {fmt(tot_out)} = {fmt(computed)}, statement says {fmt(closing)} (difference {fmt(difference)}). {breaks} running-balance break(s).")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    for r in rows:
        w.writerow([r["date"], r["description"], r["money_in"], r["money_out"], r["balance"], r["category"], r["vat_flag"], r["balance_check"]])
    by_cat = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + (to_pence(r["money_in"]) or 0) - (to_pence(r["money_out"]) or 0)
    summary = {"bank": ex.get("bank_name") or "", "period": f"{ex.get('statement_start') or ''} to {ex.get('statement_end') or ''}",
               "transactions": len(rows), "opening": fmt(opening), "total_in": fmt(tot_in), "total_out": fmt(tot_out),
               "computed_closing": fmt(computed), "statement_closing": fmt(closing), "difference": fmt(difference),
               "balance_breaks": breaks, "uncategorised": sum(r["category"] == "Uncategorised" for r in rows),
               "by_category": {k: fmt(v) for k, v in by_cat.items()}}
    return {"verdict": verdict, "rows": rows, "csv": buf.getvalue().rstrip("\n"), "summary": summary, "issues": issues}
