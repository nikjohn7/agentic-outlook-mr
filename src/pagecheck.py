"""Deterministic page check of a Markdown run against the paired PDFs.

A Markdown conversion has no pages, but the client's converter keeps the PDF's
running footers, so the extraction model works out page numbers from them.
Those pages are unverified. This step checks every quote against the paired
PDF's own text layer, page by page, with no model call:

- the quote is found on exactly ONE page   -> the Locator carries that page
  (confirmed when it is the cited page; corrected when the model cited another
  page; added when the model cited none);
- found on several pages, or on none       -> the page number is removed (a
  chart/table name in the locator is kept) and a reviewer note says why. Table
  and chart quotes usually land here: the model quotes a table row in a
  compressed form that does not appear verbatim in the text layer.

A page is never guessed. The step reads a finished run's `output.csv` and
writes NEW files (never edits a run in place), so the Markdown baseline stays
as it was:

    python -m src.pagecheck --output <run>/output.csv \\
        --sources <split.csv with a pdf_file column> --out-dir <run>/pagecheck

Outputs: `output.csv` (page-checked, every column), `output-client.csv/.xlsx`
and `failures-client.csv/.xlsx` (the run's failures re-exported alongside),
`pagecheck-audit.csv` (one row per checked quote) and `pagecheck-summary.md`.
The page notes are information, not judgments: they never set review_flag.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

import pdfplumber

from src.commentary import Commentary, Segment, commentary_from_row, row_columns
from src.confidence import normalize_quote_text
from src.csvio import read_csv, write_csv
from src.ingest import PROJECT_ROOT

NOTE_PREFIX = "Page check:"

# A cited page or page range anywhere in a locator: "p.12", "p. 4-5", "pp.3-4".
_PAGE_TOKEN = re.compile(r"\bpp?\.\s*(\d+)(?:\s*[-–]\s*(\d+))?")
# Separators left dangling once the page token is removed: "p.12 - 'Table'".
_LEADING_SEP = re.compile(r"^[\s,;:\-–]+")
# A quote's spans: the model joins non-adjacent passages with an ellipsis.
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*")
# Spans shorter than this (in letters/digits) match too easily to locate a page.
MIN_SPAN_CHARS = 15

CONFIRMED = "confirmed"
CORRECTED = "corrected"
ADDED = "added"
AMBIGUOUS = "ambiguous"
NOT_FOUND = "not_found"
NO_PDF = "no_pdf"

AUDIT_COLUMNS = (
    "Firm", "Source", "Sub-Asset Class", "View", "outcome",
    "cited_pages", "found_pages", "locator_before", "locator_after", "evidence",
)


def _key(text: str) -> str:
    """Letters and digits only, after the quote matcher's normalization: the
    text layer and the Markdown differ in spacing, hyphenation and punctuation,
    never in the words."""
    return re.sub(r"[^a-z0-9]", "", normalize_quote_text(text).lower())


def load_pdf_pages(path: Path) -> list[str]:
    with pdfplumber.open(path) as pdf:
        return [_key(page.extract_text() or "") for page in pdf.pages]


def cited_pages(locator: str) -> tuple[int, ...]:
    match = _PAGE_TOKEN.search(locator or "")
    if not match:
        return ()
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else start
    return tuple(range(start, end + 1)) if end >= start else (start,)


def found_pages(evidence: str, pages: list[str]) -> tuple[int, ...]:
    """1-indexed pages whose text holds every locatable span of the quote."""
    spans = [_key(span) for span in _ELLIPSIS.split(evidence or "")]
    spans = [span for span in spans if len(span) >= MIN_SPAN_CHARS]
    if not spans:
        return ()
    return tuple(i + 1 for i, text in enumerate(pages) if all(span in text for span in spans))


def strip_page(locator: str) -> str:
    """The locator without its page token: "p.12 - 'Rankings' table" ->
    "'Rankings' table"; a bare "p.12" -> ""."""
    text = _PAGE_TOKEN.sub("", locator or "", count=1)
    return _LEADING_SEP.sub("", text).strip()


def with_page(locator: str, page: int) -> str:
    rest = strip_page(locator)
    return f"p.{page} - {rest}" if rest else f"p.{page}"


@dataclass(frozen=True, slots=True)
class SegmentCheck:
    outcome: str
    cited: tuple[int, ...]
    found: tuple[int, ...]
    locator: str  # the locator to write


def check_segment(segment: Segment, pages: list[str] | None) -> SegmentCheck:
    cited = cited_pages(segment.locator)
    if pages is None:
        return SegmentCheck(NO_PDF, cited, (), segment.locator)
    found = found_pages(segment.evidence, pages)
    if len(found) == 1:
        page = found[0]
        if cited == (page,):
            return SegmentCheck(CONFIRMED, cited, found, segment.locator)
        return SegmentCheck(CORRECTED if cited else ADDED, cited, found, with_page(segment.locator, page))
    outcome = AMBIGUOUS if found else NOT_FOUND
    return SegmentCheck(outcome, cited, found, strip_page(segment.locator))


def _pages_text(pages: tuple[int, ...]) -> str:
    return ", ".join(f"p.{p}" for p in pages)


def segment_note(check: SegmentCheck, title: str, multi: bool) -> str:
    """The note fragment for a changed locator ("" when nothing changed)."""
    where = f"'{title}' " if multi and title else ""
    if check.outcome == CORRECTED:
        return f"{where}{_pages_text(check.cited)} corrected to p.{check.found[0]} (quote found on that page of the PDF)"
    if check.outcome == ADDED:
        return f"{where}p.{check.found[0]} added (quote found on that page of the PDF)"
    if check.outcome == AMBIGUOUS and check.cited:
        return f"{where}page removed: the quote appears on {_pages_text(check.found)} of the PDF"
    if check.outcome == NOT_FOUND and check.cited:
        return (
            f"{where}page removed: the quote was not found word for word in the PDF text "
            "(often a table or chart read in summary form)"
        )
    return ""


def check_row(
    row: dict[str, str], pdf_for_title: dict[str, Path], page_cache: dict[Path, list[str]]
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """The row with its locators page-checked (+ a note when any changed) and
    one audit record per segment."""
    commentary = commentary_from_row(row)
    multi = len(commentary.segments) > 1
    fallback = (row.get("Source") or "").split("|")[0].strip()
    segments: list[Segment] = []
    notes: list[str] = []
    audit: list[dict[str, str]] = []
    for segment in commentary.segments:
        title = segment.title or fallback
        path = pdf_for_title.get(title.strip().lower())
        pages = None
        if path is not None:
            if path not in page_cache:
                page_cache[path] = load_pdf_pages(path)
            pages = page_cache[path]
        check = check_segment(segment, pages)
        segments.append(replace(segment, locator=check.locator))
        fragment = segment_note(check, title, multi)
        if fragment:
            notes.append(fragment)
        audit.append(
            {
                "Firm": row.get("Firm", ""),
                "Source": title,
                "Sub-Asset Class": row.get("Sub-Asset Class", ""),
                "View": row.get("View", ""),
                "outcome": check.outcome,
                "cited_pages": _pages_text(check.cited),
                "found_pages": _pages_text(check.found),
                "locator_before": segment.locator,
                "locator_after": check.locator,
                "evidence": segment.evidence,
            }
        )
    checked = Commentary(tuple(segments), commentary.notes)
    if notes:
        checked = checked.with_note(f"{NOTE_PREFIX} " + "; ".join(notes) + ".")
    out = dict(row)
    out.update(row_columns(checked))
    return out, audit


def pdf_map_from_sources(sources_csv: Path) -> dict[str, Path]:
    """Lower-cased source title -> PDF path, from a source CSV's `pdf_file`
    column (repo-relative paths, like `local_file`)."""
    columns, rows = read_csv(sources_csv)
    if "pdf_file" not in columns:
        raise ValueError(f"{sources_csv} has no pdf_file column")
    title_column = next((c for c in ("Title", "Source", "source") if c in columns), None)
    if title_column is None:
        raise ValueError(f"{sources_csv} has no Title/Source column")
    mapping: dict[str, Path] = {}
    for row in rows:
        raw = (row.get("pdf_file") or "").strip()
        if not raw:
            continue
        path = PROJECT_ROOT / raw
        if not path.exists():
            raise FileNotFoundError(f"pdf_file '{raw}' for '{row[title_column]}' does not exist")
        mapping[row[title_column].strip().lower()] = path
    return mapping


def page_check(
    rows: list[dict[str, str]], pdf_for_title: dict[str, Path]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    cache: dict[Path, list[str]] = {}
    checked: list[dict[str, str]] = []
    audit: list[dict[str, str]] = []
    for row in rows:
        out, records = check_row(row, pdf_for_title, cache)
        checked.append(out)
        audit.extend(records)
    return checked, audit


def render_summary(audit: list[dict[str, str]], rows_in: int) -> str:
    counts = Counter(record["outcome"] for record in audit)
    with_page = sum(
        1 for record in audit if cited_pages(record["locator_after"])
    )
    lines = [
        "# Page check summary",
        "",
        f"- rows: {rows_in}",
        f"- quotes checked: {len(audit)}",
        f"- quotes with a verified page after the check: {with_page}",
        "",
        "| outcome | quotes |",
        "| --- | ---: |",
    ]
    for outcome in (CONFIRMED, CORRECTED, ADDED, AMBIGUOUS, NOT_FOUND, NO_PDF):
        lines.append(f"| {outcome} | {counts.get(outcome, 0)} |")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.pagecheck",
        description="Check a Markdown run's page locators against the paired PDFs (no model calls; writes new files).",
    )
    parser.add_argument("--output", required=True, type=Path, help="a finished run's output.csv")
    parser.add_argument("--sources", required=True, type=Path, help="source CSV with Title and pdf_file columns")
    parser.add_argument("--out-dir", required=True, type=Path, help="directory for the page-checked files")
    parser.add_argument(
        "--failures",
        type=Path,
        help="failures-client.csv to re-export alongside (default: the one next to --output)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from src.assemble import OUTPUT_COLUMNS
    from src.export import write_client_files

    args = build_parser().parse_args(argv)
    if args.out_dir.resolve() == args.output.parent.resolve():
        print("pagecheck: --out-dir must differ from the run folder (the run is never edited)", file=sys.stderr)
        return 2
    columns, rows = read_csv(args.output)
    checked, audit = page_check(rows, pdf_map_from_sources(args.sources))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "output.csv", tuple(columns) if columns else OUTPUT_COLUMNS, checked)
    write_csv(args.out_dir / "pagecheck-audit.csv", AUDIT_COLUMNS, audit)
    failures_csv = args.failures or args.output.parent / "failures-client.csv"
    failure_rows = read_csv(failures_csv)[1] if failures_csv.exists() else None
    write_client_files(args.out_dir, checked, failure_rows)
    summary = render_summary(audit, len(rows))
    (args.out_dir / "pagecheck-summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
