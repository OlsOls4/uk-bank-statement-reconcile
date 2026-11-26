# uk-bank-statement-reconcile

**Convert UK bank statement PDFs to CSV on your own machine, and prove every penny adds up.**
Opening balance + money in − money out must equal the closing balance printed on the statement, and every
row's running balance is checked too. If it doesn't add up, you're told exactly where, never a silent "done".

- 🔒 **Runs offline.** No uploads, no API, no AI, no telemetry. The only dependency is
  [`pdfplumber`](https://github.com/jsvine/pdfplumber) to read the PDF's text layer. The test suite fails if
  anything opens a network socket.
- ✅ **Verdict per statement:** `RECONCILED`, `MISMATCH` (with the difference and the rows where the running
  balance breaks), `UNVERIFIED` (no opening/closing balance printed), or `NOT_A_STATEMENT`/`SCANNED`/`ENCRYPTED`.
- 📄 **Output:** standard CSV, **Xero** bank-statement import CSV or **QuickBooks** 3-column CSV, or JSON.
- 🇬🇧 **UK conventions:** day-first dates (`03/10/2026`, `3 Oct 26`), `OD`/`DR`/`(brackets)` negatives,
  "balance brought/carried forward" lines skipped, Paid in / Paid out / Money in / Money out / Debit / Credit /
  signed Amount columns. Pence-integer maths, no floating-point drift.
- 🏷️ Rule-based categories (HMRC, rent & utilities, software, travel, meals...) and a "VAT receipt likely" flag.
- 🙈 Sort codes and long account/card numbers in descriptions are masked by default.

## Install

```bash
pip install git+https://github.com/OlsOls4/uk-bank-statement-reconcile
```
Python 3.10+.

## Use

```bash
# just check a statement adds up
uk-statement-reconcile statement-oct.pdf
#  statement-oct.pdf: RECONCILED (12 transactions)
#    opening 1,520.40 + in 4,639.94 - out 1,025.04 = 5,135.30; statement closing 5,135.30

# a tax year of statements -> one Xero import file
uk-statement-reconcile statements/*.pdf -o 2025-26.csv -f xero

# JSON summary (add --rows for every transaction)
uk-statement-reconcile statement.pdf --json
```
Exit code `0` = all reconciled, `1` = at least one mismatch, `2` = unverified/unreadable, so it slots into scripts and CI.
`python -m statement_reconcile ...` works too.

```python
from statement_reconcile import convert, format_rows
res = convert("statement.pdf")
print(res["summary"]["verdict"], res["summary"]["difference"])
open("out.csv", "w").write(format_rows(res["rows"], "quickbooks"))
```

## What it works on (and what it doesn't)

- **Works on:** text-based PDFs downloaded from online banking, with a transaction table whose columns are
  labelled (Date / Description / Paid out / Paid in / Balance, or similar). It reads words by their x-position, so
  it isn't tied to one bank's template. The bundled tests use synthetic, fictional statements: single page,
  multi-page with brought/carried-forward lines, a signed Amount column with no balances, and an overdrawn account
  with `OD` balances.
- **Doesn't work on:** scanned or photographed statements (no text layer; you'll get `SCANNED`), password-protected
  PDFs (print to PDF first), or credit-card statements with unusual layouts.
- **Your bank's layout not read correctly?** You'll get `MISMATCH` or `UNVERIFIED` rather than wrong numbers.
  Please open an issue with a *redacted* layout description (never attach a real statement).

## Privacy

Everything happens locally: the PDF is read from disk and the CSV is written to disk. Nothing is sent anywhere,
logged or cached. Don't commit real statements to git; `*.csv` is in `.gitignore`.

## Need it as an API, in bulk, or inside n8n/Make/Zapier?

This tool is the local, do-it-yourself path. The same parser and reconciliation run as a hosted
**[UK Bank Statement PDF to CSV converter on Apify](https://apify.com/olsols4/uk-bank-statement-pdf-to-csv)**:
send PDF URLs by API, get rows plus a verdict and a CSV download link back, schedule it, or call it from AI agents
via MCP (`https://mcp.apify.com?tools=olsols4/uk-bank-statement-pdf-to-csv`). Pay per statement, no subscription,
and PDFs are deleted straight after conversion.

## Tests

```bash
pip install .
python -m unittest discover -s tests -v
```

## Licence

MIT. Not affiliated with any bank, Xero or Intuit. Always check the output before filing anything with HMRC.
