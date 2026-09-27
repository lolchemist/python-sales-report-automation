"""Security and failure regressions for the review findings."""
import csv
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import sales_report as app

HEADER = "date,product,category,quantity,unit_price\n"
VALID = HEADER + "2026-09-01,Keyboard,Electronics,2,50.00\n"


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.input = self.folder / "input.csv"
        self.output = self.folder / "output.csv"
        self.input.write_text(VALID, encoding="utf-8")
        self.sales = app.read_sales(self.input)

    def assert_clean_failure(self):
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.folder.glob(".sales-report-*")), [])
        self.assertEqual(self.input.read_text(), VALID)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(ROOT / "sales_report.py"),
                               *map(str, args)], capture_output=True, text=True)

    def test_mid_write_failure(self):
        real_writer = csv.writer

        class FailingWriter:
            def __init__(self, stream):
                self.writer = real_writer(stream)
                self.count = 0

            def writerow(self, row):
                self.count += 1
                if self.count == 3:
                    raise OSError("simulated write failure")
                return self.writer.writerow(row)

        with patch.object(app.csv, "writer", FailingWriter):
            with self.assertRaisesRegex(OSError, "write failure"):
                app.write_report(self.sales, self.output)
        self.assert_clean_failure()

    def test_close_failure(self):
        real_temporary = tempfile.NamedTemporaryFile

        class CloseFailure:
            def __init__(self, **kwargs):
                self.file = real_temporary(**kwargs)

            def __enter__(self):
                return self.file.__enter__()

            def __exit__(self, *args):
                self.file.__exit__(*args)
                raise OSError("simulated close failure")

        with patch.object(app.tempfile, "NamedTemporaryFile", CloseFailure):
            with self.assertRaisesRegex(OSError, "close failure"):
                app.write_report(self.sales, self.output)
        self.assert_clean_failure()

    def test_fsync_failure(self):
        with patch.object(app.os, "fsync", side_effect=OSError("sync failure")):
            with self.assertRaises(OSError):
                app.write_report(self.sales, self.output)
        self.assert_clean_failure()

    def test_existing_destination(self):
        self.output.write_text("keep")
        with self.assertRaises(FileExistsError):
            app.write_report(self.sales, self.output)
        self.assertEqual(self.output.read_text(), "keep")
        self.assertEqual(list(self.folder.glob(".sales-report-*")), [])

    def test_symlink_to_input(self):
        self.output.symlink_to(self.input)
        result = self.run_cli(self.input, "-o", self.output)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(self.input.read_text(), VALID)
        self.assertEqual(list(self.folder.glob(".sales-report-*")), [])

    def test_dangling_symlink(self):
        self.output.symlink_to(self.folder / "missing")
        with self.assertRaises(FileExistsError):
            app.write_report(self.sales, self.output)
        self.assertTrue(self.output.is_symlink())
        self.assertFalse((self.folder / "missing").exists())
        self.assertEqual(list(self.folder.glob(".sales-report-*")), [])

    def test_concurrent_destination_creation(self):
        real_link = os.link

        def race(source, destination):
            Path(destination).write_text("created by another process")
            return real_link(source, destination)

        with patch.object(app.os, "link", side_effect=race):
            with self.assertRaises(FileExistsError):
                app.write_report(self.sales, self.output)
        self.assertEqual(self.output.read_text(), "created by another process")
        self.assertEqual(list(self.folder.glob(".sales-report-*")), [])

    def test_publication_failure(self):
        with patch.object(app.os, "link", side_effect=OSError("hard links unsupported")):
            with self.assertRaises(OSError):
                app.write_report(self.sales, self.output)
        self.assert_clean_failure()

    def test_destination_directory_errors(self):
        for destination in (self.folder / "missing" / "out.csv", self.input / "out.csv"):
            with self.subTest(destination=destination):
                result = self.run_cli(self.input, "-o", destination)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
        self.assert_clean_failure()

    def test_directory_permission_failure(self):
        with patch.object(app.tempfile, "NamedTemporaryFile", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                app.write_report(self.sales, self.output)
        self.assert_clean_failure()

    def test_quote_in_unquoted_field(self):
        for product in ('A"broken', ' "quoted"', '"closed"junk'):
            with self.subTest(product=product):
                self.input.write_text(HEADER + "2026-09-01," + product + ",B,1,1.00\n")
                result = self.run_cli(self.input, "-o", self.output)
                self.assertEqual(result.returncode, 1)
                self.assertIn("CSV line 2", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(self.output.exists())

    def test_error_line_for_all_line_endings(self):
        for newline in ("\r", "\n", "\r\n"):
            with self.subTest(newline=newline):
                self.input.write_bytes((HEADER.rstrip("\n") + newline +
                                        '2026-09-01,A"broken,B,1,1.00').encode())
                result = self.run_cli(self.input, "-o", self.output)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr.strip(),
                                 "Error: CSV line 2: quote inside an unquoted field.")
                self.assertFalse(self.output.exists())

    def test_error_line_after_multiline_field(self):
        for newline in ("\r", "\n", "\r\n"):
            with self.subTest(newline=newline):
                text = newline.join((HEADER.rstrip("\n"),
                                     '2026-09-01,"first', 'second",B,1,1.00',
                                     '2026-09-02,A"broken,B,1,1.00'))
                with self.assertRaisesRegex(app.ReportError,
                                            r"^CSV line 4: quote inside an unquoted field\.$"):
                    app.validate_csv_syntax(text)

    def test_error_line_inside_multiline_field(self):
        for newline in ("\r", "\n", "\r\n"):
            for ending, message in (('second"junk', "unexpected character after closing quote."),
                                    ("second", "unclosed quoted field.")):
                with self.subTest(newline=newline, ending=ending):
                    text = newline.join((HEADER.rstrip("\n"), '2026-09-01,"first', ending))
                    with self.assertRaises(app.ReportError) as caught:
                        app.validate_csv_syntax(text)
                    self.assertEqual(str(caught.exception), "CSV line 3: " + message)

    def test_error_line_with_mixed_and_consecutive_line_endings(self):
        text = HEADER.rstrip("\n") + '\r\n\r\n\r\r\n\n2026-09-01,A"broken,B,1,1.00'
        with self.assertRaisesRegex(app.ReportError,
                                    r"^CSV line 6: quote inside an unquoted field\.$"):
            app.validate_csv_syntax(text)

    def test_valid_escaped_quotes_and_multiline_fields(self):
        for newline in ("\n", "\r\n", "\r"):
            with self.subTest(newline=newline):
                text = io.StringIO(newline="")
                writer = csv.writer(text, lineterminator=newline)
                writer.writerow(app.REQUIRED)
                writer.writerow(("2026-09-01", 'A "quoted"' + newline + 'product', 'B,"C"', 1, '1.00'))
                self.input.write_bytes(text.getvalue().encode())
                sale = app.read_sales(self.input)[0]
                self.assertEqual(sale.product, 'A "quoted"' + newline + 'product')
                self.assertEqual(sale.category, 'B,"C"')

    def test_directory_inputs_without_output(self):
        for path in ("/", "."):
            with self.subTest(path=path):
                result = self.run_cli(path)
                self.assertEqual(result.returncode, 1)
                self.assertIn("directory", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_formula_protection_in_report(self):
        values = [prefix + formula for prefix in ("", "  ", "\t", "\r\n", "\u200b", " \x01 ")
                  for formula in ("=1+1", "+1", "-1", "@SUM(A1)")]
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(app.REQUIRED)
        for value in values:
            writer.writerow(("2026-09-01", value, value, 1, "1.00"))
        self.input.write_bytes(buffer.getvalue().encode())
        app.write_report(app.read_sales(self.input), self.output)
        with self.output.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        sales = [row for row in rows if row["type"] == "sale"]
        self.assertEqual([row["product"] for row in sales], ["'" + v for v in values])
        self.assertEqual([row["category"] for row in sales], ["'" + v for v in values])
        for row in rows:
            if row["type"] == "category":
                self.assertTrue(row["category"].startswith("'"))
            self.assertFalse(row["quantity"].startswith("'"))
            self.assertFalse(row["revenue"].startswith("'"))
            self.assertFalse(row["unit_price"].startswith("'"))

    def test_nul_is_rejected_without_report(self):
        self.input.write_bytes((HEADER + "2026-09-01,\x00=1,B,1,1.00\n").encode())
        result = self.run_cli(self.input, "-o", self.output)
        self.assertEqual(result.returncode, 1)
        self.assertIn("NUL", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.output.exists())

    def test_ordinary_text_unchanged(self):
        for value in ("Keyboard", " A normal product ", "A+B", "A@B", "Клавиатура", 'A "quote"'):
            with self.subTest(value=value):
                self.assertEqual(app.spreadsheet_text(value), value)
        self.input.write_text(HEADER + "2026-09-01, A normal product , Office ,1,1.00\n")
        app.write_report(app.read_sales(self.input), self.output)
        with self.output.open(newline="") as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["product"], " A normal product ")
        self.assertEqual(row["category"], " Office ")

    def test_input_size_exceeded(self):
        self.input.write_bytes(b"x" * (app.MAX_INPUT_BYTES + 1))
        result = self.run_cli(self.input, "-o", self.output)
        self.assertEqual(result.returncode, 1)
        self.assertIn("10000000 bytes", result.stderr)
        self.assertFalse(self.output.exists())

    def test_exact_size_limit_allowed(self):
        # Blank lines consume bytes but are not data records.
        content = VALID.encode()
        self.input.write_bytes(content + b"\n" * (app.MAX_INPUT_BYTES - len(content)))
        self.assertEqual(len(app.read_sales(self.input)), 1)

    def test_data_rows_exceeded(self):
        self.input.write_text(HEADER + "2026-09-01,A,B,1,1.00\n" * (app.MAX_DATA_ROWS + 1))
        result = self.run_cli(self.input, "-o", self.output)
        self.assertEqual(result.returncode, 1)
        self.assertIn("100000 data rows", result.stderr)
        self.assertFalse(self.output.exists())

    def test_exact_row_limit_allowed(self):
        self.input.write_text(HEADER + "2026-09-01,A,B,1,1.00\n" * app.MAX_DATA_ROWS)
        self.assertEqual(len(app.read_sales(self.input)), app.MAX_DATA_ROWS)

    def test_example_cli_totals(self):
        result = self.run_cli(ROOT / "example_sales.csv", "-o", self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.output.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[-1]["type"], "total")
        self.assertEqual(rows[-1]["quantity"], "10")
        self.assertEqual(rows[-1]["revenue"], "182.50")
        self.assertEqual(list(self.folder.glob(".sales-report-*")), [])


if __name__ == "__main__":
    unittest.main()
