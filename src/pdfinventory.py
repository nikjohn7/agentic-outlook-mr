"""Deterministic shortlist of paired PDF pages worth reading for lost graphics.

High text coverage cannot clear a graphics-heavy page. This is an inventory,
not evidence that a conversion lost content, nor a complete visual detector.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import pdfplumber

from src.confidence import normalize_quote_text
from src.csvio import write_csv
from src.ingest import MARKDOWN_SUFFIXES, load_pilot_sources, markdown_to_text

# Calibrated on the 15 October 2026 Markdown/PDF pairs, including unflagged
# chart samples. The 60-object floor catches dense grids/heatmaps, while seven
# small round marks catch the sparse Neuberger sector grid and Franklin dials.
# 8% vector area catches substantial plots without selecting decorative icons.
# Raster charts need a caption so photographs do not select prose pages.
# 90% word coverage catches lossy chart OCR without chasing isolated footnotes.
MIN_VECTOR_OBJECTS = 60
MIN_FILLED_CURVES = 100  # Franklin p.12 has 765; the State Street cover logo has 81
MIN_BODY_LINES = 60  # NYL's unflagged p.5 forecast paths are lines, not curves
MIN_CHART_RECTS = 10  # unflagged L&G p.6 lost bar heights despite full label coverage
MIN_ROUND_MARKS = 7
MIN_VECTOR_AREA = 0.08
MIN_CHART_IMAGE_AREA = 0.05
MIN_TEXT_COVERAGE = 0.90
MIN_PAGE_WORDS = 40
BODY_TOP = 0.08
BODY_BOTTOM = 0.92
MAX_OBJECT_AREA = 0.40  # remove page fills and large decorative backgrounds
MIN_MARK_SIZE = 1.0  # PDF points
MAX_MARK_SIZE = 12.0
MIN_MARK_ASPECT = 0.90
MAX_MARK_ASPECT = 1.10

_WORD = re.compile(r"\w+")
# Captions can be interleaved with the other column by PDF text extraction.
_CAPTION = re.compile(r"\b(?:charts?\b|(?:fig(?:ure)?\.?|exhibit)\s*\d+)", re.IGNORECASE)
# A reference to next page's figure beside a portrait is not a raster chart.
_RASTER_CAPTION = re.compile(r"(?im)^\s*(?:charts?\b|(?:fig(?:ure)?\.?|exhibit)\s*\d+)")
# Only the opening text is checked: disclosures below a chart must not clear it.
_BOILERPLATE = re.compile(
    r"^(?:contact\b|disclaimer\b|disclosures?\b|important (?:legal )?(?:information|disclosures)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PageReport:
    page: int
    words: int
    matched_words: int
    text_coverage: float
    images: int
    curves: int
    rects: int
    lines: int
    body_lines: int
    body_curves: int
    body_unfilled_curves: int
    body_rects: int
    round_marks: int
    chart_caption: bool
    image_area: float
    vector_area: float
    excluded: str
    graphics_heavy: bool
    low_coverage: bool
    flagged: bool


def _words(text: str) -> list[str]:
    return _WORD.findall(normalize_quote_text(text).casefold())


def _area_fraction(obj: dict, page) -> float:
    width = max(0.0, min(page.width, obj["x1"]) - max(0.0, obj["x0"]))
    height = max(0.0, min(page.height, obj["bottom"]) - max(0.0, obj["top"]))
    return width * height / (page.width * page.height)


def _body_objects(objects: list[dict], page) -> list[dict]:
    return [
        obj for obj in objects
        if obj["top"] >= page.height * BODY_TOP
        and obj["bottom"] <= page.height * BODY_BOTTOM
        and _area_fraction(obj, page) <= MAX_OBJECT_AREA
    ]


def _page_report(page, md_words: set[str]) -> PageReport:
    text = page.extract_text() or ""
    words = _words(text)
    matched = sum(word in md_words for word in words)
    # A textless page has no text coverage to assess; graphics still select it.
    coverage = matched / len(words) if words else 1.0
    curves = _body_objects(page.curves, page)
    unfilled_curves = [obj for obj in curves if not obj.get("fill")]
    rects = _body_objects(page.rects, page)
    lines = _body_objects(page.lines, page)
    # Repeated sizes distinguish allocation dots/dial ticks from logo letters.
    mark_sizes: dict[tuple[int, int], int] = {}
    for obj in curves:
        if (MIN_MARK_SIZE <= obj["width"] <= MAX_MARK_SIZE
                and MIN_MARK_SIZE <= obj["height"] <= MAX_MARK_SIZE
                and MIN_MARK_ASPECT <= obj["width"] / obj["height"] <= MAX_MARK_ASPECT):
            size = (round(obj["width"]), round(obj["height"]))
            if size[0] == size[1]:
                mark_sizes[size] = mark_sizes.get(size, 0) + 1
    round_marks = max(mark_sizes.values(), default=0)
    image_area = min(1.0, sum(_area_fraction(obj, page) for obj in page.images))
    chart_caption = bool(_CAPTION.search(text))
    # Filled panels and rectangles behind prose are decoration. Dense filled
    # charts still select through the independent object-count/mark measures.
    vector_area = min(1.0, sum(_area_fraction(obj, page) for obj in unfilled_curves))
    opening_lines = [" ".join(_words(line)) for line in text.splitlines() if line.strip()][:3]
    # Boilerplate deliberately overrides graphics (unlike the sparse rule):
    # back-matter pages carry QR codes, icon grids and decorative dot patterns
    # that clear the graphics floors (Columbia Threadneedle p.32: 321 rects;
    # Tikehau p.22: 96 round marks) but hold no allocation content.
    excluded ="boilerplate" if any(_BOILERPLATE.match(line) for line in opening_lines) else ""
    graphics_heavy = (
        len(unfilled_curves) >= MIN_VECTOR_OBJECTS
        or len(lines) >= MIN_BODY_LINES
        or len(curves) - len(unfilled_curves) >= MIN_FILLED_CURVES
        or len(rects) >= MIN_VECTOR_OBJECTS
        or round_marks >= MIN_ROUND_MARKS
        or vector_area >= MIN_VECTOR_AREA
        or (chart_caption and len(rects) >= MIN_CHART_RECTS)
        or (image_area >= MIN_CHART_IMAGE_AREA and bool(_RASTER_CAPTION.search(text)))
        or (not words and bool(page.images))
    )
    # Do not let a word floor clear sparse allocation grids or raster scans.
    if len(words) < MIN_PAGE_WORDS and not graphics_heavy:
        excluded = excluded or "sparse"
    low_coverage = bool(words) and coverage < MIN_TEXT_COVERAGE
    return PageReport(
        page=page.page_number, words=len(words), matched_words=matched,
        text_coverage=coverage, images=len(page.images), curves=len(page.curves),
        rects=len(page.rects), body_curves=len(curves), body_rects=len(rects),
        lines=len(page.lines), body_lines=len(lines),
        body_unfilled_curves=len(unfilled_curves),
        round_marks=round_marks, chart_caption=chart_caption,
        image_area=image_area, vector_area=vector_area,
        excluded=excluded, graphics_heavy=graphics_heavy, low_coverage=low_coverage,
        flagged=not excluded and (graphics_heavy or low_coverage),
    )


def page_reports(md_path: Path, pdf_path: Path) -> tuple[PageReport, ...]:
    """Measure every physical PDF page against the whole rendered Markdown.

    Coverage counts PDF token occurrences whose normalized word occurs anywhere
    in the Markdown. It ignores order, punctuation and repetition in Markdown;
    it is a shortlist signal, never a verbatim evidence validation check. Area
    measures are capped sums of bounding boxes, not a geometric union.
    """
    md_words = set(_words(markdown_to_text(md_path.read_text(encoding="utf-8-sig"))))
    with pdfplumber.open(pdf_path) as pdf:
        reports = []
        for page in pdf.pages:
            reports.append(_page_report(page, md_words))
            page.close()
    return tuple(reports)


def flagged_pages(md_path: Path, pdf_path: Path) -> tuple[int, ...]:
    """Sorted, 1-indexed physical PDF pages shortlisted for a visual read."""
    return tuple(report.page for report in page_reports(md_path, pdf_path) if report.flagged)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.pdfinventory", description=__doc__)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    sources = load_pilot_sources(args.sources)
    for source in sources:
        if source.local_path is None or source.local_path.suffix.lower() not in MARKDOWN_SUFFIXES:
            parser.error(f"{source.source}: inventory requires a Markdown local_file")
        if source.pdf_path is None:
            parser.error(f"{source.source}: inventory requires a paired pdf_file")
    rows = []
    lines = [
        "# PDF inventory summary", "",
        "A deterministic shortlist for visual reading, not proof of missing content.",
        "Page numbers are physical PDF pages (1-indexed).", "",
        "| Firm | Document | Flagged pages | Flagged / total |",
        "| --- | --- | --- | ---: |",
    ]
    total_flagged = 0
    for source in sources:
        reports = page_reports(source.local_path, source.pdf_path)
        selected = [report.page for report in reports if report.flagged]
        total_flagged += len(selected)
        for report in reports:
            rows.append({
                "document": source.source, "firm": source.firm,
                "md_path": str(source.local_path), "pdf_path": str(source.pdf_path),
                **asdict(report),
            })
        title = source.source.replace("|", r"\|")
        firm = source.firm.replace("|", r"\|")
        pages = ", ".join(map(str, selected)) or "none"
        lines.append(f"| {firm} | {title} | {pages} | {len(selected)} / {len(reports)} |")
    lines.extend([
        "", f"Total: {total_flagged} flagged pages / {len(rows)} pages in {len(sources)} documents.",
        "", "Thresholds (calibrated on the 15 October 2026 pairs):",
        f"- Unfilled body curves or body rectangles >= {MIN_VECTOR_OBJECTS}, or repeated small round marks of one rounded square size >= {MIN_ROUND_MARKS}.",
        f"- Filled body curves >= {MIN_FILLED_CURVES} (dense visual symbols, with a higher floor to skip logos).",
        f"- Body line segments >= {MIN_BODY_LINES} (NYL forecast paths use lines, not curves).",
        f"- Captioned charts with >= {MIN_CHART_RECTS} body rectangles (bars/cells).",
        f"- Unfilled body curve bounding-box area >= {MIN_VECTOR_AREA:.0%}, or captioned raster area >= {MIN_CHART_IMAGE_AREA:.0%}.",
        f"- Normalized PDF word coverage in rendered Markdown < {MIN_TEXT_COVERAGE:.0%}.",
        f"- Body: {BODY_TOP:.0%} to {BODY_BOTTOM:.0%} of page height; objects > {MAX_OBJECT_AREA:.0%} of page area excluded as backgrounds.",
        f"- Round marks: {MIN_MARK_SIZE:g} to {MAX_MARK_SIZE:g} pt per side, aspect {MIN_MARK_ASPECT:g} to {MAX_MARK_ASPECT:g}.",
        f"- Opening boilerplate headings excluded; sparse pages (< {MIN_PAGE_WORDS} words) excluded only when graphics are below threshold.",
        "- Graphics and low coverage are independent reasons; high coverage never clears graphics.", "",
    ])
    args.out.mkdir(parents=True, exist_ok=True)
    columns = ("document", "firm", "md_path", "pdf_path", *PageReport.__dataclass_fields__)
    write_csv(args.out / "pdf-inventory.csv", columns, rows)
    summary = "\n".join(lines).replace("\u2014", "-")
    (args.out / "pdf-inventory-summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
