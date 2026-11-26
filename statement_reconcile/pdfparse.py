"""Deterministic UK bank statement PDF parser (text-layer PDFs, no AI).

Reads words with their x positions (pdfplumber), finds the transaction table header
(Date / Description / Paid out / Paid in / Money in / Money out / Debit / Credit / Amount / Balance...),
maps each amount on a row to the column it sits under, joins multi-line descriptions,
carries dates forward for same-day rows, and returns the 'extracted' dict reconcile() expects.
Runs fully offline: it only reads the local file.
"""
from __future__ import annotations

import re

import pdfplumber

AMT_RE = re.compile(r"^[-(]?[£$€]?-?\d{1,3}(?:,\d{3})*\.\d{2}\)?(?:-|CR|DR|OD|D)?$|^[-(]?[£$€]?-?\d+\.\d{2}\)?(?:-|CR|DR|OD|D)?$", re.I)
SUFFIX = {"OD", "DR", "CR", "D"}
MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE_NUM = re.compile(r"^(?:\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{2}-\d{2})$")
DAY = re.compile(r"^\d{1,2}(?:st|nd|rd|th)?$", re.I)
MONTH = re.compile(rf"^{MON}$", re.I)
YEAR = re.compile(r"^(?:\d{2}|\d{4})$")
DAYMON = re.compile(rf"^(\d{{1,2}})({MON})$", re.I)  # 03Oct
NUM_DATE_ANY = re.compile(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b|\b\d{1,2}(?:st|nd|rd|th)?\s+" + MON + r"\s+\d{4}\b", re.I)

SKIP_DESC = re.compile(r"\b(balance (brought|carried) forward|brought forward|carried forward|b/?f|c/?f|opening balance|closing balance|start balance|end balance|balance from previous|previous balance|new balance|ending balance|starting balance|balance on \d{1,2})\b", re.I)
OPEN_DESC = re.compile(r"\b(brought forward|b/?f|opening balance|start balance|starting balance|balance from previous|previous balance)\b", re.I)
CLOSE_DESC = re.compile(r"\b(carried forward|c/?f|closing balance|end balance|ending balance|new balance)\b", re.I)
TOTAL_DESC = re.compile(r"^(total|totals|sub ?total|payments total|receipts total)\b", re.I)
MONEY_TXT = r"(\(?-?[£$€]?\s?-?\d{1,3}(?:,\d{3})*\.\d{2}\)?(?:\s?(?:OD|DR|CR|D)\b)?)"
OPEN_TXT = re.compile(r"\b(?:opening|start(?:ing)?|previous)\s+balance\b[^\d(£$€\-\n]{0,40}" + MONEY_TXT, re.I)
BAL_ON_TXT = re.compile(r"\bbalance\s+(?:on|at|as at)\s+\d{1,2}(?:st|nd|rd|th)?\s+" + MON + r"(?:\s+\d{2,4})?[^\d(£$€\-\n]{0,10}" + MONEY_TXT, re.I)
CLOSE_TXT = re.compile(r"\b(?:closing|end(?:ing)?|new)\s+balance\b[^\d(£$€\-\n]{0,40}" + MONEY_TXT, re.I)
ACCT_TXT = re.compile(r"\b(?:account\s*(?:number|no\.?|ending(?:\s+in)?)?|acc(?:ount)?\s*ending|a/c(?:\s*no\.?)?)\s*[:\-]?\s*((?:[*xX•]{2,}\s?)?\d[\d \-]{2,18}\d|[*xX•]*\d{4})", re.I)
PERIOD_TXT = re.compile(r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?\s+" + MON + r"\s+\d{4})\s*(?:to|-|–|until)\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?\s+" + MON + r"\s+\d{4})", re.I)
SORTCODE = re.compile(r"\b\d{2}-\d{2}-\d{2}\b")

OUT_WORDS = {"out", "paid out", "money out", "withdrawals", "withdrawal", "debit", "debits", "payments", "payments out", "spent", "outgoings", "dr"}
IN_WORDS = {"in", "paid in", "money in", "deposits", "deposit", "credit", "credits", "receipts", "payments in", "received", "cr"}


def _clean_label(s: str) -> str:
    s = s.lower().replace("£", "").replace("(gbp)", "").replace("gbp", "").replace("()", "")
    return re.sub(r"[^a-z ]", "", s).strip()


def _lines(page):
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5, y_tolerance=2)
    words.sort(key=lambda w: (round(w["top"]), w["x0"]))
    lines, cur, top = [], [], None
    for w in words:
        if top is None or abs(w["top"] - top) <= 3:
            cur.append(w)
            top = w["top"] if top is None else top
        else:
            lines.append(sorted(cur, key=lambda x: x["x0"]))
            cur, top = [w], w["top"]
    if cur:
        lines.append(sorted(cur, key=lambda x: x["x0"]))
    return lines


def _phrases(line, gap=7.0):
    out = []
    for w in line:
        if out and w["x0"] - out[-1]["x1"] <= gap:
            out[-1] = {"text": out[-1]["text"] + " " + w["text"], "x0": out[-1]["x0"], "x1": w["x1"]}
        else:
            out.append({"text": w["text"], "x0": w["x0"], "x1": w["x1"]})
    return out


def _header(line):
    """Return column map if this line is a transaction-table header."""
    text = " ".join(w["text"] for w in line).lower()
    if not re.search(r"\b(date|details|description|transaction|particulars|narrative)\b", text):
        return None
    cols = []
    for ph in _phrases(line):
        lab = _clean_label(ph["text"])
        kind = None
        if "balance" in lab:
            kind = "balance"
        elif lab in OUT_WORDS or re.search(r"\b(paid out|money out|withdrawals?|debits?|payments out)\b", lab):
            kind = "out"
        elif lab in IN_WORDS or re.search(r"\b(paid in|money in|deposits?|credits?|receipts|payments in)\b", lab):
            kind = "in"
        elif re.search(r"\b(amount|value)\b", lab):
            kind = "amount"
        elif "date" in lab:
            kind = "date"
        elif re.search(r"\b(details|description|transaction|particulars|narrative|payment type)\b", lab):
            kind = "desc"
        if kind:
            cols.append({"kind": kind, "x0": ph["x0"], "x1": ph["x1"]})
    kinds = {c["kind"] for c in cols}
    money = kinds & {"out", "in", "amount"}
    if not money or not ({"date", "desc"} & kinds):
        return None
    if "in" in kinds and "out" not in kinds and "amount" not in kinds:
        return None
    return cols


def _is_amount(tok):
    return bool(AMT_RE.match(tok))


def _assign(num, cols):
    money = [c for c in cols if c["kind"] in ("out", "in", "amount", "balance")]
    cx = (num["x0"] + num["x1"]) / 2
    best, bd = None, 1e9
    for c in money:
        ov = min(num["x1"], c["x1"]) - max(num["x0"], c["x0"])
        d = -ov if ov > 0 else min(abs(num["x1"] - c["x1"]), abs(num["x0"] - c["x0"]), abs(cx - (c["x0"] + c["x1"]) / 2))
        if d < bd:
            best, bd = c, d
    return best["kind"] if best else None


def _leading_date(tokens):
    """tokens: list of word dicts. Returns (date_string, n_tokens_used) or (None, 0)."""
    t = [w["text"] for w in tokens[:3]]
    if not t:
        return None, 0
    if DATE_NUM.match(t[0]):
        return t[0], 1
    m = DAYMON.match(t[0])
    if m:
        if len(t) > 1 and YEAR.match(t[1]) and len(t[1]) == 4:
            return f"{m[1]} {m[2]} {t[1]}", 2
        return f"{m[1]} {m[2]}", 1
    if len(t) >= 2 and DAY.match(t[0]) and MONTH.match(t[1]):
        day = re.sub(r"\D", "", t[0])
        if len(t) >= 3 and YEAR.match(t[2]) and (len(t[2]) == 4 or tokens[2]["x0"] - tokens[1]["x1"] < 8):
            return f"{day} {t[1]} {t[2]}", 3
        return f"{day} {t[1]}", 2
    return None, 0


def _mask(acct: str | None):
    if not acct:
        return None
    digits = re.sub(r"\D", "", acct)
    return digits[-4:] if len(digits) >= 4 else None


def parse_pdf(path: str, max_pages: int = 100) -> dict:
    with pdfplumber.open(path) as pdf:
        pages = pdf.pages[:max_pages]
        n_pages = len(pdf.pages)
        texts = [(p.extract_text() or "") for p in pages]
        chars = sum(len(re.sub(r"\s", "", t)) for t in texts)
        has_images = any(len(p.images) for p in pages)
        if chars < 40 * max(1, len(pages)):
            return {"status": "SCANNED" if has_images or chars < 10 else "NO_TEXT", "pages": n_pages}
        full = "\n".join(texts)
        items = []  # sequence of ('txn', dict) / ('text', str, page) / ('marker', kind, bal)
        cols = None
        found_header = False
        for pi, page in enumerate(pages, start=1):
            page_cols = None
            plines = _lines(page)
            hdr_idx = [k for k, ln in enumerate(plines) if _header(ln)]
            if not hdr_idx and cols is None:
                continue
            tops = [ln[0]["top"] for ln in plines[(hdr_idx[0] if hdr_idx else 0):]]
            diffs = sorted(b - a for a, b in zip(tops, tops[1:]) if b - a > 1)
            spacing = diffs[len(diffs) // 2] if diffs else 14
            gap_limit = max(24.0, 2.6 * spacing)
            prev_top, in_table, seen_row = None, False, False
            for k, line in enumerate(plines):
                h = _header(line)
                if h:
                    page_cols = cols = h
                    found_header = True
                    in_table, prev_top = True, line[0]["top"]
                    continue
                if not hdr_idx:
                    # continuation page without a repeated header: start at the first dated or amount row
                    if not in_table:
                        page_cols = cols
                        mx = min(c["x0"] for c in cols if c["kind"] in ("out", "in", "amount", "balance")) - 45
                        has_amt = any(w["x0"] >= mx and _is_amount(w["text"]) for w in line)
                        if not (has_amt or _leading_date(line)[0]):
                            continue
                        in_table, prev_top = True, line[0]["top"]
                elif not in_table:
                    continue
                if prev_top is not None and line[0]["top"] - prev_top > gap_limit:
                    in_table = False  # big vertical gap: footer / legal text, not the table
                    if not hdr_idx:
                        break
                    continue
                prev_top = line[0]["top"]
                money_x0 = min(c["x0"] for c in page_cols if c["kind"] in ("out", "in", "amount", "balance")) - 45
                # split tokens
                nums, rest = [], []
                i = 0
                while i < len(line):
                    w = line[i]
                    if w["x0"] >= money_x0 and _is_amount(w["text"]):
                        txt, x1 = w["text"], w["x1"]
                        if i + 1 < len(line) and line[i + 1]["text"].upper() in SUFFIX and line[i + 1]["x0"] - w["x1"] < 12:
                            txt, x1 = txt + " " + line[i + 1]["text"], line[i + 1]["x1"]
                            i += 1
                        nums.append({"text": txt, "x0": w["x0"], "x1": x1})
                    elif w["x0"] >= money_x0 and w["text"] in ("£", "$", "€"):
                        pass
                    else:
                        rest.append(w)
                    i += 1
                date, used = _leading_date(rest)
                desc_words = rest[used:]
                desc = " ".join(w["text"] for w in desc_words).strip()
                vals = {}
                for n in nums:
                    k = _assign(n, page_cols)
                    if k and k not in vals:
                        vals[k] = n["text"]
                    elif k:
                        vals.setdefault("extra", []).append(n["text"])
                if SKIP_DESC.search(desc) and (not date or not ({"in", "out", "amount"} & vals.keys())):
                    bal = vals.get("balance") or (nums[-1]["text"] if nums else None)
                    kind = "open" if OPEN_DESC.search(desc) else ("close" if CLOSE_DESC.search(desc) else None)
                    items.append(("marker", kind, bal, date))
                    continue
                if TOTAL_DESC.match(desc) and not date:
                    items.append(("total", desc, vals))
                    continue
                if not nums:
                    if not desc and not date:
                        continue
                    if re.search(r"\b(page \d+|continued|statement|sort code|account (number|ending)|bic|iban)\b", desc, re.I) and not date:
                        items.append(("noise",))
                        continue
                    items.append(("text", desc, pi, date))
                    continue
                if not ({"in", "out", "amount"} & vals.keys()) and "balance" not in vals:
                    items.append(("text", desc, pi, date))
                    continue
                items.append(("txn", {"date": date, "description": desc, "money_in": vals.get("in"), "money_out": vals.get("out"),
                                      "amount": vals.get("amount"), "balance": vals.get("balance"), "page": pi}))
        if not found_header:
            return {"status": "NOT_A_STATEMENT", "pages": n_pages}
        # resolve multi-line descriptions and carried-forward dates
        txns, pending, cur_date, last = [], [], None, None
        opening_row = closing_row = None

        def next_anchor(j):
            for k in range(j + 1, len(items)):
                it = items[k]
                if it[0] == "txn":
                    return "dated" if it[1]["date"] else "undated"
                if it[0] == "text" and it[3]:
                    return "dated"
                if it[0] in ("marker", "total"):
                    return "break"
            return "end"

        for j, it in enumerate(items):
            if it[0] == "text":
                _, desc, pi, date = it
                if date:
                    if pending and last is not None:
                        last["description"] = (last["description"] + " " + " ".join(pending)).strip()
                    pending = [desc] if desc else []
                    cur_date = date
                    continue
                if last is not None and not pending and next_anchor(j) in ("dated", "break", "end"):
                    last["description"] = (last["description"] + " " + desc).strip()
                else:
                    pending.append(desc)
            elif it[0] == "txn":
                t = dict(it[1])
                if t["date"]:
                    cur_date = t["date"]
                else:
                    t["date"] = cur_date
                if pending:
                    t["description"] = (" ".join(pending) + " " + t["description"]).strip()
                    pending = []
                if not ({"money_in", "money_out", "amount"} & {k for k, v in t.items() if v}):
                    # only a balance printed: a description line ending with a running balance (rare); treat as text
                    pending = [t["description"]] if t["description"] else []
                    continue
                txns.append(t)
                last = t
            elif it[0] == "marker":
                _, kind, bal, date = it
                if pending and last is not None:
                    last["description"] = (last["description"] + " " + " ".join(pending)).strip()
                pending = []
                if kind == "open" and opening_row is None and not txns:
                    opening_row = bal
                if kind == "close":
                    closing_row = bal
                if kind == "open" and opening_row is None and txns:
                    pass
            elif it[0] in ("total", "noise"):
                if pending and last is not None:
                    last["description"] = (last["description"] + " " + " ".join(pending)).strip()
                pending = []
        if pending and last is not None:
            last["description"] = (last["description"] + " " + " ".join(pending)).strip()

        # amounts printed unsigned in a single Amount column with a balance column: infer direction from balance moves
        from .reconcile import to_pence
        if txns and all(t["amount"] and not t["money_in"] and not t["money_out"] for t in txns):
            if all((to_pence(t["amount"]) or 0) >= 0 and not re.search(r"(CR|DR|OD|D|-|\))\s*$", t["amount"], re.I) for t in txns) and any(t["balance"] for t in txns):
                prev = None
                for t in txns:
                    b = to_pence(t["balance"])
                    if prev is not None and b is not None:
                        a = to_pence(t["amount"])
                        if b - prev == -a:
                            t["amount"] = "-" + t["amount"]
                    prev = b if b is not None else (None if prev is None else prev + (to_pence(t["amount"]) or 0))

        m = OPEN_TXT.search(full)
        mc = CLOSE_TXT.search(full)
        bal_on = [x[1] for x in BAL_ON_TXT.finditer(full)]
        opening = m[1] if m else (bal_on[0] if len(bal_on) >= 2 else opening_row)
        closing = mc[1] if mc else (bal_on[-1] if len(bal_on) >= 2 else closing_row)
        # numeric date order: UK day-first unless the dates prove month-first (US)
        order = "DMY"
        for t in txns:
            dm = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-]\d{2,4}$", t["date"] or "")
            if dm and int(dm[1]) > 12:
                order = "DMY"; break
            if dm and int(dm[2]) > 12:
                order = "MDY"
        if order == "MDY":
            for t in txns:
                dm = re.match(r"^(\d{1,2})([/.\-])(\d{1,2})([/.\-])(\d{2,4})$", t["date"] or "")
                if dm:
                    t["date"] = f"{dm[3]}/{dm[1]}/{dm[5]}"
        derived_opening = False
        if opening is None and txns and txns[0]["balance"]:
            t = txns[0]
            b = to_pence(t["balance"])
            ip, op, a = to_pence(t["money_in"]) or 0, to_pence(t["money_out"]) or 0, to_pence(t["amount"])
            if a is not None and not (ip or op):
                ip, op = (a, 0) if a >= 0 else (0, -a)
            opening = f"{(b - abs(ip) + abs(op)) / 100:.2f}"
            derived_opening = True
        pm = PERIOD_TXT.search(full)
        acct = None
        for am in ACCT_TXT.finditer(full):
            cand = am[1]
            if not SORTCODE.fullmatch(cand.strip()):
                acct = cand
                break
        first_line = next((ln.strip() for ln in texts[0].splitlines() if ln.strip()), "")
        bank = first_line if len(first_line) <= 60 and not NUM_DATE_ANY.search(first_line) else ""
        return {
            "status": "OK", "pages": n_pages,
            "extracted": {
                "bank_name": bank,
                "account_number_last4": _mask(acct),
                "date_order": order,
                "statement_start": pm[1] if pm else (txns[0]["date"] if txns else None),
                "statement_end": pm[2] if pm else (txns[-1]["date"] if txns else None),
                "currency": "GBP" if ("£" in full or "GBP" in full) else None,
                "opening_balance": opening, "closing_balance": closing,
                "opening_derived": derived_opening,
                "transactions": txns,
            },
        }
