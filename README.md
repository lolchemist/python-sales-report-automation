# Python Sales Report Automation

A Python command-line tool that validates sales data and generates a CSV report
with individual sales, category summaries, and overall totals. Requires Python
3.9+ and uses only the standard library—no dependencies to install.

## Features

- Validate sales records with clear errors that identify CSV lines.
- Calculate revenue accurately with decimal arithmetic.
- Summarize quantities and revenue by category and across all sales.
- Protect existing files from overwriting and reduce spreadsheet formula risks.
- Verified by **47 passing automated tests**.

## Quick start

Run from the root of this standalone repository:

```sh
python3 sales_report.py example_sales.csv --output report.csv
```

The positional argument is the input CSV path. Use `-o` or `--output` to choose a
new report path. Without it, the report is saved beside the input as
`<input_stem>_report.csv`. The destination directory must already exist.
**Existing files are never overwritten**; choose a new output path for each run.

For all options:

```sh
python3 sales_report.py --help
```

## Input format

```csv
date,product,category,quantity,unit_price
2026-09-01,Keyboard,Electronics,2,50.00
2026-09-01,Mouse,Electronics,3,20.00
2026-09-02,Notebook,Office,5,4.50
```

| Column | Requirement |
| --- | --- |
| `date` | Valid calendar date in strict `YYYY-MM-DD` format |
| `product` | Nonempty product name |
| `category` | Nonempty category name; case-sensitive |
| `quantity` | Nonnegative integer |
| `unit_price` | Nonnegative decimal using a period, such as `0`, `12.50`, or `0.001` |

All five columns are required, in any order. Extra columns are ignored; duplicate
column names are rejected. Leading and trailing whitespace is removed from dates
and numbers. Product and category text is preserved except for formula protection;
whitespace-only values are rejected.

Use comma-separated UTF-8 CSV (an optional BOM is accepted). Quoted fields may
contain line breaks and escaped double quotes (`""`); opening quotes must appear
at the start of a field. LF, CRLF, and CR line endings are supported. NUL
characters, malformed quoting, and rows with the wrong number of fields are
rejected. Empty files and header-only files are invalid; blank physical lines
outside quoted fields are skipped.

Numbers cannot contain signs, exponential notation, NaN, or Infinity. Negative
values, including returns represented as negative sales, are not supported.

## Report output

The output columns are:

```csv
type,date,product,category,quantity,unit_price,revenue
```

| Row type | Contents | Order |
| --- | --- | --- |
| `sale` | Original sale with its calculated revenue | Input order |
| `category` | Total quantity and revenue for one category | Sorted by category name |
| `total` | Overall quantity and revenue | Last row |

Fields that do not apply to a row type are left blank. For the sample above:

| Category | Quantity | Revenue |
| --- | --- | --- |
| Electronics | 5 | 160.00 |
| Office | 5 | 22.50 |
| **Total** | **10** | **182.50** |

Do not sum different row types together: category and overall totals already
include the individual sales.

Calculations use `Decimal` with sufficient precision for the operands, avoiding
floating-point errors and rounding to cents. All sales are assumed to use one
currency; currency conversion, taxes, and monetary rounding are not implemented.

## Validation and errors

The entire input is validated before a report is created. Errors are written to
stderr with an `Error:` prefix; invalid values include the CSV line number.

| Exit code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Data or file error |
| `2` | Invalid command-line arguments |

## Security and reliable file handling

### Safe saving

The report is written to a temporary file in the destination directory, flushed,
synced, and closed before being published atomically using a hard link
(`os.link`). Publication fails if the output path already exists, including a
symbolic link. The source CSV and existing reports are never overwritten, and
temporary-file cleanup is attempted on both success and failure.

The filesystem must support hard links; there is no unsafe fallback. These
protections cover normal runtime errors, not power loss, forced termination, or
concurrent directory/permission changes that prevent cleanup. Cleanup errors are
reported; if cleanup fails after publication, the completed report may already
exist.

### Spreadsheet formula protection

Product and category values receive a leading apostrophe (`'`) when their first
significant character is `=`, `+`, `-`, or `@`. Detection skips leading whitespace
and Unicode control/format characters. Values that begin with a control/format
character also receive the prefix. This protection applies to category summary
rows too; grouping uses the original category names. Ordinary text is preserved,
and numeric columns do not receive apostrophes.

CSV does not store cell types, so behavior depends on the spreadsheet application
and import settings. The apostrophe may be visible, and removing it or saving and
importing the CSV again may remove protection. For untrusted files, explicitly
import product and category columns as text and retain the prefix. Protection is
not guaranteed across every spreadsheet application or CSV transformation.

## Resource limits

- **Input size:** up to 10 MB (10,000,000 bytes), including BOM and line endings.
  The limit is checked against bytes actually read, not just file metadata.
- **Sales records:** up to 100,000, excluding the header. A multiline field belongs
  to one record; blank lines outside fields do not count as records.
- **Field size:** the standard CSV parser's limit also applies, typically 131,072
  characters per field. Exceeding it produces a CLI error.

The exact size and record boundaries are allowed. Exceeding either limit fails
without creating a report. All data is held in memory.

## Tests

Run from the repository root:

```sh
python3 -B -m unittest discover -s tests -v
```

**47 automated tests pass**, covering validation, exact monetary calculations,
category and overall totals, CLI behavior, resource limits, formula protection,
and file-saving failures. Tests create temporary files inside the project
directory and remove them afterward.
