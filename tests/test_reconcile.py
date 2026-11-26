import csv
import io
import os
import socket
import tempfile
import unittest
from unittest import mock

from statement_reconcile import convert, format_rows, mask_numbers, reconcile, to_pence
from statement_reconcile.__main__ import main

FX = os.path.join(os.path.dirname(__file__), "fixtures")
EXPECTED = {  # synthetic statements, all data fictional
    "01_clean_single_page.pdf": ("RECONCILED", 12),
    "02_multi_page_bf_cf.pdf": ("RECONCILED", 60),       # brought/carried-forward lines skipped
    "03_signed_amount_no_balance.pdf": ("RECONCILED", 15),  # single signed Amount column
    "04_overdrawn_od_suffix.pdf": ("RECONCILED", 10),    # "123.45 OD" balances are negative
    "05_fourteen_rows.pdf": ("RECONCILED", 14),
    "07_not_a_statement_invoice.pdf": ("NOT_A_STATEMENT", 0),
}


class NoNetwork(unittest.TestCase):
    """The whole point: nothing leaves the machine. Any socket use fails the test."""

    def setUp(self):
        p = mock.patch.object(socket, "socket", side_effect=AssertionError("network access attempted"))
        p.start()
        self.addCleanup(p.stop)


class FixtureTests(NoNetwork):
    def test_fixtures(self):
        for name, (verdict, n) in EXPECTED.items():
            with self.subTest(name=name):
                res = convert(os.path.join(FX, name))
                self.assertEqual(res["summary"]["verdict"], verdict)
                self.assertEqual(len(res["rows"]), n)

    def test_totals_add_up(self):
        s = convert(os.path.join(FX, "02_multi_page_bf_cf.pdf"))["summary"]
        self.assertAlmostEqual(s["openingBalance"] + s["totalIn"] - s["totalOut"], s["closingBalance"], places=2)
        self.assertEqual(s["difference"], None)

    def test_not_a_pdf(self):
        with tempfile.NamedTemporaryFile("w", suffix=".pdf", delete=False) as f:
            f.write("hello")
        try:
            self.assertEqual(convert(f.name)["summary"]["verdict"], "NOT_A_PDF")
        finally:
            os.remove(f.name)

    def test_cli_exit_codes_and_xero_csv(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.csv")
            with mock.patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(main([os.path.join(FX, "01_clean_single_page.pdf"), "-o", out, "-f", "xero"]), 0)
                self.assertEqual(main([os.path.join(FX, "07_not_a_statement_invoice.pdf")]), 2)
            rows = list(csv.reader(open(out)))
            self.assertEqual(rows[0], ["*Date", "*Amount", "Payee", "Description", "Reference"])
            self.assertEqual(len(rows), 13)
            self.assertRegex(rows[1][0], r"^\d{2}/\d{2}/\d{4}$")


class ReconcileUnitTests(NoNetwork):
    def test_to_pence(self):
        for raw, want in [("1,234.56", 123456), ("£12.30 OD", -1230), ("(45.00)", -4500), ("99.99 DR", -9999),
                          ("-0.01", -1), ("10.00 CR", 1000)]:
            self.assertEqual(to_pence(raw), want, raw)

    def test_mismatch_is_never_reported_as_ok(self):
        ex = {"opening_balance": "100.00", "closing_balance": "150.00", "statement_end": "31/10/2026",
              "transactions": [{"date": "01/10/2026", "description": "STRIPE PAYOUT", "money_in": "60.00", "balance": "160.00"},
                               {"date": "02/10/2026", "description": "COSTA COFFEE", "money_out": "3.50", "balance": "156.50"}]}
        r = reconcile(ex)
        self.assertEqual(r["verdict"], "MISMATCH")
        self.assertEqual(r["summary"]["difference"], "-6.50")

    def test_unverified_without_closing(self):
        ex = {"opening_balance": "100.00", "transactions": [{"date": "01/10/2026", "description": "X", "money_out": "1.00"}]}
        self.assertEqual(reconcile(ex)["verdict"], "UNVERIFIED")

    def test_mask(self):
        self.assertEqual(mask_numbers("TRANSFER 12-34-56 12345678"), "TRANSFER **-**-56 ****5678")

    def test_quickbooks_format(self):
        rows = [{"date": "2026-10-03", "description": "X", "amount": -3.5}]
        self.assertEqual(format_rows(rows, "quickbooks").splitlines()[1], "03/10/2026,X,-3.50")


if __name__ == "__main__":
    unittest.main()
