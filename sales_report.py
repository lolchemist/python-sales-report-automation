"""Validate sales CSV files and produce exact decimal revenue reports."""

import argparse
import csv
import io
import os
import tempfile
import unicodedata
import re
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


REQUIRED = ("date", "product", "category", "quantity", "unit_price")
MAX_INPUT_BYTES = 10_000_000
MAX_DATA_ROWS = 100_000
REPORT_COLUMNS = ("type", "date", "product", "category", "quantity", "unit_price", "revenue")


class ReportError(ValueError):
    """An input or output problem that can be explained to the user."""


@dataclass(frozen=True)
class Sale:
    date: str
    product: str
    category: str
    quantity: int
    unit_price: Decimal

    @property
    def revenue(self) -> Decimal:
        with localcontext() as context:
            context.prec = len(self.unit_price.as_tuple().digits) + len(str(self.quantity)) + 1
            return self.unit_price * self.quantity


def exact_add(left: Decimal, right: Decimal) -> Decimal:
    """Add without rounding, even beyond Decimal's default precision."""
    with localcontext() as context:
        context.prec = max(left.adjusted(), right.adjusted(), 0) - min(
            left.as_tuple().exponent, right.as_tuple().exponent, 0
        ) + 2
        return left + right


def validate_csv_syntax(text: str) -> None:
    """Validate quote placement, including escaped quotes and multiline fields.

    States are field start, unquoted field, quoted field, and closing quote.
    CR and LF delimit records only outside quoted fields.
    """
    state = "start"
    line = 1
    previous_was_cr = False
    for char in text:
        if state == "quoted":
            if char == '"':
                state = "closed"
        elif state == "closed":
            if char == '"':
                state = "quoted"
            elif char == ",":
                state = "start"
            elif char in "\r\n":
                state = "start"
            else:
                raise ReportError("CSV line {}: unexpected character after closing quote.".format(line))
        elif char == '"':
            if state != "start":
                raise ReportError("CSV line {}: quote inside an unquoted field.".format(line))
            state = "quoted"
        elif char == "," or char in "\r\n":
            state = "start"
        else:
            state = "unquoted"
        # Count physical lines even inside quoted fields; CRLF is one break.
        if char == "\r" or (char == "\n" and not previous_was_cr):
            line += 1
        previous_was_cr = char == "\r"
    if state == "quoted":
        raise ReportError("CSV line {}: unclosed quoted field.".format(line))


def spreadsheet_text(value: str) -> str:
    """Prefix risky text with an apostrophe; leave numeric columns untouched."""
    # Spreadsheet importers can ignore whitespace/control/format characters.
    significant = value.lstrip()
    while significant and (significant[0].isspace() or
                           unicodedata.category(significant[0]).startswith("C")):
        significant = significant[1:]
    if significant.startswith(("=", "+", "-", "@")) or (
        value and unicodedata.category(value[0]).startswith("C")
    ):
        return "'" + value
    return value


def read_sales(path: Path) -> List[Sale]:
    sales: List[Sale] = []
    with path.open("rb") as source:
        raw = source.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ReportError("Input CSV exceeds the 10 MB (10000000 bytes) limit.")
    text = raw.decode("utf-8-sig")
    if "\x00" in text:
        raise ReportError("CSV contains a forbidden NUL control character.")
    validate_csv_syntax(text)
    with io.StringIO(text, newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        headers = reader.fieldnames
        if not headers:
            raise ReportError("CSV file is empty.")
        missing = set(REQUIRED) - set(headers)
        if missing:
            raise ReportError("Missing required columns: " + ", ".join(sorted(missing)))
        if len(headers) != len(set(headers)):
            raise ReportError("Duplicate column names are not allowed.")
        for row_number, row in enumerate(reader, start=1):
            if row_number > MAX_DATA_ROWS:
                raise ReportError("Input CSV exceeds the 100000 data rows limit.")
            prefix = "CSV line {}: ".format(reader.line_num)
            if None in row or any(value is None for value in row.values()):
                raise ReportError(prefix + "number of fields does not match the header.")
            values = {key: row[key].strip() for key in REQUIRED}
            # Preserve original text for reporting and formula detection.
            for key in ("product", "category"):
                values[key] = row[key]
            if not all(value.strip() for value in values.values()):
                raise ReportError(prefix + "required fields must not be empty.")
            try:
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", values["date"]):
                    raise ValueError
                date.fromisoformat(values["date"])
            except ValueError:
                raise ReportError(prefix + "date must be a valid YYYY-MM-DD date.") from None
            if not re.fullmatch(r"[0-9]+", values["quantity"]):
                raise ReportError(prefix + "quantity must be a non-negative integer.")
            if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", values["unit_price"]):
                raise ReportError(prefix + "unit_price must be a non-negative decimal number (e.g. 12.50).")
            sales.append(Sale(values["date"], values["product"], values["category"],
                              int(values["quantity"]), Decimal(values["unit_price"])))
    if not sales:
        raise ReportError("CSV contains no sales rows.")
    return sales


def write_report(sales: Sequence[Sale], path: Path) -> None:
    categories: Dict[str, Tuple[int, Decimal]] = {}
    total_quantity = 0
    total_revenue = Decimal("0")
    for sale in sales:
        quantity, revenue = categories.get(sale.category, (0, Decimal("0")))
        categories[sale.category] = (quantity + sale.quantity, exact_add(revenue, sale.revenue))
        total_quantity += sale.quantity
        total_revenue = exact_add(total_revenue, sale.revenue)
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, prefix=".sales-report-",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.writer(stream)
            writer.writerow(REPORT_COLUMNS)
            for sale in sales:
                writer.writerow(("sale", sale.date, spreadsheet_text(sale.product),
                                 spreadsheet_text(sale.category), sale.quantity,
                                 format(sale.unit_price, "f"), format(sale.revenue, "f")))
            for category, (quantity, revenue) in sorted(categories.items()):
                writer.writerow(("category", "", "", spreadsheet_text(category),
                                 quantity, "", format(revenue, "f")))
            writer.writerow(("total", "", "", "", total_quantity, "", format(total_revenue, "f")))
            stream.flush()
            os.fsync(stream.fileno())
        # link() atomically creates a new name, failing if ANY entry exists,
        # including a dangling symlink. No check-then-rename race or overwrite.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a sales summary from a UTF-8 CSV file.")
    parser.add_argument("input", type=Path, help="Input CSV path")
    parser.add_argument("-o", "--output", type=Path, help="New output path (default: INPUT_STEM_report.csv beside input)")
    args = parser.parse_args(argv)
    try:
        if args.input.is_dir():
            raise ReportError("Input path is a directory; provide a CSV file.")
        output = args.output if args.output is not None else args.input.with_name(args.input.stem + "_report.csv")
        sales = read_sales(args.input)
        write_report(sales, output)
    except FileExistsError:
        print("Error: output already exists; choose a new path with --output.", file=sys.stderr)
        return 1
    except (ReportError, OSError, UnicodeError, csv.Error, ValueError) as error:
        print("Error: {}".format(error), file=sys.stderr)
        return 1
    print("Report created: {}".format(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
