import csv
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sales_report import ReportError, read_sales, write_report


HEADER = "date,product,category,quantity,unit_price\n"
VALID = HEADER + "2026-09-01,Keyboard,Electronics,2,50.00\n2026-09-01,Mouse,Electronics,3,20.00\n2026-09-02,Notebook,Office,5,4.50\n"


class SalesReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.input = Path(self.temp.name) / "sales.csv"
        self.output = Path(self.temp.name) / "report.csv"

    def load(self, content):
        self.input.write_text(content, encoding="utf-8")
        return read_sales(self.input)

    def report(self, content):
        write_report(self.load(content), self.output)
        with self.output.open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "sales_report.py"), *map(str, args)],
                              capture_output=True, text=True)

    def test_valid_csv_and_totals(self):
        rows = self.report(VALID)
        self.assertEqual([Decimal(row["revenue"]) for row in rows[:3]],
                         [Decimal("100"), Decimal("60"), Decimal("22.50")])
        self.assertEqual(rows[-1]["type"], "total")
        self.assertEqual(rows[-1]["quantity"], "10")
        self.assertEqual(rows[-1]["revenue"], "182.50")

    def test_empty_csv(self):
        with self.assertRaisesRegex(ReportError, "empty"):
            self.load("")

    def test_header_only(self):
        with self.assertRaisesRegex(ReportError, "no sales"):
            self.load(HEADER)

    def test_missing_column(self):
        with self.assertRaisesRegex(ReportError, "Missing required columns: unit_price"):
            self.load("date,product,category,quantity\n")

    def test_invalid_dates(self):
        for value in ("2026-02-29", "2026-13-01", "2026-9-01", "yesterday"):
            with self.subTest(value=value), self.assertRaisesRegex(ReportError, "date must"):
                self.load(HEADER + value + ",A,B,1,1.00\n")

    def test_negative_price(self):
        with self.assertRaisesRegex(ReportError, "unit_price"):
            self.load(HEADER + "2026-09-01,A,B,1,-0.01\n")

    def test_invalid_prices(self):
        for value in ("NaN", "Infinity", "1e3", "abc", "1,50"):
            with self.subTest(value=value), self.assertRaises(ReportError):
                self.load(HEADER + "2026-09-01,A,B,1," + value + "\n")

    def test_invalid_quantities(self):
        for value in ("-1", "1.5", "abc", "NaN"):
            with self.subTest(value=value), self.assertRaisesRegex(ReportError, "quantity"):
                self.load(HEADER + "2026-09-01,A,B," + value + ",1.00\n")

    def test_multiple_categories(self):
        rows = [row for row in self.report(VALID) if row["type"] == "category"]
        self.assertEqual([(r["category"], r["quantity"], r["revenue"]) for r in rows],
                         [("Electronics", "5", "160.00"), ("Office", "5", "22.50")])

    def test_exact_money(self):
        rows = self.report(HEADER + "2026-09-01,A,B,3,0.10\n2026-09-01,C,B,1,0.20\n")
        self.assertEqual(rows[-1]["revenue"], "0.50")

    def test_money_beyond_default_precision(self):
        rows = self.report(HEADER + "2026-09-01,A,B,2,123456789012345678901234567890.12\n2026-09-01,C,B,1,0.0001\n")
        self.assertEqual(rows[0]["revenue"], "246913578024691357802469135780.24")
        self.assertEqual(rows[-1]["revenue"], "246913578024691357802469135780.2401")

    def test_zero_values(self):
        rows = self.report(HEADER + "2024-02-29,A,B,0,0.00\n")
        self.assertEqual(rows[-1]["quantity"], "0")
        self.assertEqual(rows[-1]["revenue"], "0.00")

    def test_bom_quoted_fields_and_extra_column(self):
        sales = self.load('\ufeff' + HEADER.rstrip() + ',note\n2026-09-01,"A, B",Office,1,2.50,ok\n')
        self.assertEqual(sales[0].product, "A, B")

    def test_invalid_row_shapes(self):
        for row in ("2026-09-01,A,B,1", "2026-09-01,A,B,1,1,extra", "2026-09-01,,B,1,1"):
            with self.subTest(row=row), self.assertRaises(ReportError):
                self.load(HEADER + row + "\n")

    def test_duplicate_headers(self):
        with self.assertRaisesRegex(ReportError, "Duplicate"):
            self.load(HEADER.rstrip() + ",date\n")

    def test_cli_explicit_output(self):
        self.load(VALID)
        result = self.cli(self.input, "--output", self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Report created:", result.stdout)
        self.assertTrue(self.output.exists())

    def test_cli_default_output(self):
        self.load(VALID)
        result = self.cli(self.input)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.input.with_name("sales_report.csv").exists())

    def test_cli_missing_input(self):
        result = self.cli(self.input)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_invalid_input_creates_no_report(self):
        self.input.write_text(HEADER + "2026-09-01,A,B,1,-1\n", encoding="utf-8")
        result = self.cli(self.input, "-o", self.output)
        self.assertEqual(result.returncode, 1)
        self.assertIn("line 2", result.stderr)
        self.assertFalse(self.output.exists())

    def test_cli_preserves_existing_output_and_input(self):
        self.load(VALID)
        for target in (self.output, self.input):
            with self.subTest(target=target):
                target.write_text(VALID, encoding="utf-8")
                result = self.cli(self.input, "-o", target)
                self.assertEqual(result.returncode, 1)
                self.assertIn("already exists", result.stderr)
                self.assertEqual(target.read_text(encoding="utf-8"), VALID)

    def test_cli_bad_encoding(self):
        self.input.write_bytes(b"\xff\xfe")
        result = self.cli(self.input)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error:", result.stderr)

    def test_cli_malformed_csv(self):
        self.input.write_text(HEADER + '2026-09-01,"unclosed', encoding="utf-8")
        result = self.cli(self.input)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
