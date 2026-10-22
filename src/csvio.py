"""The one CSV writer every deliverable goes through.

Client feedback on the July deliverable (2026-10): the files rendered with
garbled characters in Excel on Windows and a handful of rows split in two. Both
had one cause each, and both are fixed here, once, for every file the pipeline
writes:

- **Encoding.** The CSVs were UTF-8 without a byte-order mark. Excel on Windows
  opens a BOM-less CSV as the legacy code page, so every em dash, curly quote
  and non-breaking hyphen (thousands of them, straight from the source
  documents) rendered as mojibake. Every file is now written as `utf-8-sig`
  (UTF-8 with BOM), which Excel on both platforms reads correctly. Every reader
  in the repo already uses `utf-8-sig` (it accepts BOM-less input too).
- **Embedded newlines.** A few evidence quotes carried line breaks copied from
  the document. They were correctly quoted, but Excel's text-import path and
  some spreadsheet tools split them into extra rows (which looked like calls
  with no View). Cells are flattened to a single line at write time.

`write_xlsx` writes the same rows as a native Excel workbook next to the CSV,
which sidesteps both problems entirely for anyone who opens it in Excel.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

CSV_ENCODING = "utf-8-sig"

_LINE_BREAKS = re.compile(r"[\r\n  ]+")
_RUN_OF_SPACES = re.compile(r"[ \t]{2,}")


def clean_cell(value: object) -> str:
    """One line, no control characters, as a string. None becomes ""."""
    if value is None:
        return ""
    text = _LINE_BREAKS.sub(" ", str(value))
    return _RUN_OF_SPACES.sub(" ", text).strip()


def clean_row(row: Mapping[str, object]) -> dict[str, str]:
    return {key: clean_cell(value) for key, value in row.items()}


def write_csv(
    path: str | Path,
    columns: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> Path:
    """Write `rows` under `columns` as BOM-prefixed UTF-8 with single-line
    cells. Extra keys in a row are ignored, missing keys are written blank."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding=CSV_ENCODING) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(clean_row(row))
    return target


def read_csv(path: str | Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV written by this module (or any UTF-8 CSV, BOM or not).
    Returns (column names, rows)."""
    with Path(path).open(newline="", encoding=CSV_ENCODING) as handle:
        reader = csv.DictReader(handle)
        rows = [dict(row) for row in reader]
        return list(reader.fieldnames or []), rows


def write_xlsx(
    path: str | Path,
    columns: Sequence[str],
    rows: Iterable[Mapping[str, object]],
    *,
    sheet_title: str = "Sheet1",
) -> Path:
    """Write the same rows as a native .xlsx (one sheet, header row bold,
    header frozen, auto-filter on). Cells are flattened exactly as in the CSV
    so the two files are interchangeable."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title[:31]
    names = list(columns)
    sheet.append(names)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="top")
    row_count = 0
    for row in rows:
        cleaned = clean_row(row)
        sheet.append([cleaned.get(name, "") for name in names])
        row_count += 1
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(names))}{row_count + 1}"
    for index, name in enumerate(names, start=1):
        # Readable default widths: long text columns wide, everything else narrow.
        wide = name.lower() in {
            "full commentary", "reasoning", "verbatim evidence", "reviewer notes",
            "evidence / notes", "explanation", "source", "url",
        }
        sheet.column_dimensions[get_column_letter(index)].width = 60 if wide else 18
    workbook.save(target)
    return target


def write_csv_and_xlsx(
    path: str | Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> tuple[Path, Path]:
    """Write `<path>` as CSV and `<path with .xlsx>` as a workbook."""
    csv_path = write_csv(path, columns, rows)
    xlsx_path = write_xlsx(csv_path.with_suffix(".xlsx"), columns, rows)
    return csv_path, xlsx_path
