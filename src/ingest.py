"""Thin deterministic ingestion helpers for source snapshots and chunks."""

from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pdfplumber
import requests
import trafilatura


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PILOT_CSV = PROJECT_ROOT / "prev-excel" / "pilot.csv"
TARGET_SOURCES_CSV = PROJECT_ROOT / "excel-file" / "Target Ingestion List.csv"

PDF_PAGE_CHUNK_SIZE = 5
HTML_CHAR_CHUNK_SIZE = 8000
MAX_SOURCES_PER_RUN = 20

# Graphics in HTML are invisible to every text path we have (trafilatura text,
# raw markup, markdown fetch), so a page that carries its views in charts or
# infographics can silently under-report calls. Content graphics (<img>,
# <canvas>, <figure>) at or above this count flag the source `visual_heavy`;
# <svg> is counted but excluded from the flag because it is overwhelmingly
# icons/logos. A visual_heavy source is printed to PDF with a headless browser
# and then flows through the PDF path (rendered pages, page locators, per-page
# text snapshot) so its graphics are readable, not just its extracted text.
VISUAL_MARKUP_TAGS = ("img", "svg", "canvas", "figure")
VISUAL_HEAVY_IMAGE_THRESHOLD = 5

PRINTED_PDF_NAME = "printed.pdf"
PRINT_NAVIGATION_TIMEOUT_MS = 60_000
PRINT_NETWORK_IDLE_TIMEOUT_MS = 15_000

# Cookie banners and "professional investor" gates are position:fixed, so
# chromium repeats them over the content of every printed page. Before
# printing, buttons matching this pattern are clicked (best-effort, stacked
# dialogs handled by looping) to clear such overlays.
CONSENT_BUTTON_PATTERN = re.compile(
    r"^\s*(accept( all)?( cookies)?|i agree|agree( and continue)?|"
    r"i accept|got it|continue|confirm|ok)\s*$",
    re.IGNORECASE,
)
MAX_CONSENT_DIALOGS = 3

# Scrambled-page detection. pdfplumber reads a page row by row, so a
# multi-column layout is emitted with the columns interleaved line-by-line
# ("left-clause right-clause left-clause right-clause..."). No contiguous quote
# of the rendered page then survives a verbatim check against the snapshot,
# even though the model (which reads the rendered page) quoted it correctly.
# We flag such pages deterministically by their defining physical property: a
# persistent, near-empty vertical gutter that runs the full body height with
# substantial text on both sides. Line length alone does NOT work — a wide
# single-column page has long lines too (AB pilot pages run longer than the JPM
# two-column page), so only the gutter distinguishes them.
SCRAMBLE_BODY_TOP_FRAC = 0.10  # ignore header band when measuring the gutter
SCRAMBLE_BODY_BOTTOM_FRAC = 0.92  # ignore footer band
SCRAMBLE_MIN_BODY_WORDS = 20  # too little text to judge column structure
SCRAMBLE_Y_ROWS = 40  # vertical resolution of the gutter coverage probe
SCRAMBLE_X_BINS = 100  # horizontal resolution of the interior gutter scan
SCRAMBLE_INTERIOR_MIN = 0.30  # a column gutter sits in the page interior,
SCRAMBLE_INTERIOR_MAX = 0.70  # not in the outer margins
SCRAMBLE_MIN_SIDE_WORDS = 15  # each column must carry real text, not a sliver
SCRAMBLE_COVERAGE_MAX = 0.12  # gutter is empty across ~all body rows


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    firm: str
    date: str
    source: str
    url: str
    resolved_url: str
    source_type: str
    local_path: Path | None = None


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_id: str
    locator: str
    source_path: Path


@dataclass(frozen=True, slots=True)
class IngestedSource:
    source: SourceRecord
    snapshot_text_path: Path
    native_source_path: Path
    chunks: list[Chunk]
    page_count: int | None = None  # PDF page count; None for text-path HTML
    visual_markup: dict[str, int] | None = None  # HTML tag counts; None for PDF
    visual_heavy: bool = False  # HTML likely carries views in graphics the text paths cannot see
    printed_pdf: bool = False  # visual_heavy HTML captured as print-to-PDF and analyzed as a PDF
    scrambled_pages: tuple[int, ...] = ()  # 1-indexed pages whose text layer is column-interleaved


def load_pilot_sources(path: str | Path = PILOT_CSV) -> list[SourceRecord]:
    """Load a pilot-format source CSV.

    Columns: `Firm`, `Date`, `Source` (title), `MR Link` (URL), and the optional
    `local_file` (see `_resolve_local_file` for its semantics). Any second test
    set is a CSV of this exact shape — pointing `--sources <path>` at it needs no
    code change.
    """
    rows = _read_csv(path)
    sources: list[SourceRecord] = []
    for row in rows:
        firm, title = row["Firm"], row["Source"]
        local_path = _resolve_local_file(row, firm=firm, title=title)
        url = row["MR Link"]
        sources.append(
            SourceRecord(
                source_id=slugify(f"{firm} {title}"),
                firm=firm,
                date=row["Date"],
                source=title,
                url=url,
                resolved_url=resolve_url(url),
                source_type="pdf" if local_path else detect_source_type(url),
                local_path=local_path,
            )
        )
    return sources


def load_target_sources(path: str | Path = TARGET_SOURCES_CSV) -> list[SourceRecord]:
    """Load the target-batch CSV (`Id`, `Firm`, `Title`, `Published At`,
    `Source Link`). It carries no `local_file` column, which resolves as empty
    for every row (all URLs fetched) — loading is unchanged; the optional column
    is honoured if a future target CSV adds it."""
    rows = _read_csv(path)
    sources: list[SourceRecord] = []
    for index, row in enumerate(rows, start=1):
        raw_url = row["Source Link"]
        firm, title = row["Firm"], row["Title"]
        local_path = _resolve_local_file(row, firm=firm, title=title)
        source_id = row["Id"] or slugify(f"{index} {firm} {title}")
        resolved_url = resolve_url(raw_url)
        sources.append(
            SourceRecord(
                source_id=str(source_id),
                firm=firm,
                date=row["Published At"],
                source=title,
                url=raw_url,
                resolved_url=resolved_url,
                source_type="pdf" if local_path else detect_source_type(resolved_url),
                local_path=local_path,
            )
        )
    return sources


def enforce_source_limit(sources: list[SourceRecord], *, limit: int = MAX_SOURCES_PER_RUN) -> None:
    if len(sources) > limit:
        raise ValueError(f"run has {len(sources)} sources; max is {limit}")


def resolve_url(raw_url: str) -> str:
    url = raw_url.strip()
    if url.startswith("read://"):
        parsed = urlparse(url.replace("read://", "https://", 1))
        target = parse_qs(parsed.query).get("url", [""])[0]
        if target:
            return strip_tracking_params(target)
    if "seismic.com" in url:
        url = url.replace("PLUSSIGN", "+").replace("___", "/")
    return strip_tracking_params(url)


def strip_tracking_params(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    query = parse_qs(parsed.query, keep_blank_values=True)
    kept = {
        key: values
        for key, values in query.items()
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    }
    return urlunparse(parsed._replace(query=urlencode(kept, doseq=True)))


def detect_source_type(locator: str) -> str:
    path = urlparse(locator).path if "://" in locator else locator
    return "pdf" if path.lower().endswith(".pdf") else "html"


def create_snapshot(
    source: SourceRecord,
    work_dir: str | Path,
    *,
    printer=None,
) -> IngestedSource:
    """printer overrides the headless-browser print-to-PDF step (tests)."""
    output_dir = Path(work_dir) / source.source_id
    output_dir.mkdir(parents=True, exist_ok=True)

    page_count: int | None = None
    visual_markup: dict[str, int] | None = None
    visual_heavy = False
    printed_pdf = False
    scrambled_pages: tuple[int, ...] = ()
    if source.source_type == "pdf":
        native_path = _copy_pdf(source, output_dir)
        snapshot_text, page_count, scrambled_pages = _extract_pdf_text(native_path)
        snapshot_path = output_dir / "snapshot.txt"
        snapshot_path.write_text(snapshot_text, encoding="utf-8")
        chunks = _pdf_chunks(native_path, page_count)
    else:
        html_path = output_dir / "snapshot.html"
        html = _fetch_html(source.resolved_url)
        html_path.write_text(html, encoding="utf-8")
        visual_markup = count_visual_markup(html)
        visual_heavy = is_visual_heavy(visual_markup)
        snapshot_path = output_dir / "snapshot.txt"
        if visual_heavy:
            native_path = output_dir / PRINTED_PDF_NAME
            (printer or print_url_to_pdf)(source.resolved_url, native_path)
            snapshot_text, page_count, scrambled_pages = _extract_pdf_text(native_path)
            snapshot_path.write_text(snapshot_text, encoding="utf-8")
            chunks = _pdf_chunks(native_path, page_count)
            printed_pdf = True
        else:
            native_path = html_path
            text = trafilatura.extract(html, include_tables=True) or ""
            snapshot_path.write_text(text, encoding="utf-8")
            chunks = _html_chunks(snapshot_path, len(text))

    (output_dir / "chunks.json").write_text(
        json.dumps([_chunk_to_dict(chunk) for chunk in chunks], indent=2),
        encoding="utf-8",
    )
    (output_dir / "ingest_meta.json").write_text(
        json.dumps(
            {
                "source_id": source.source_id,
                "source_type": source.source_type,
                "page_count": page_count,
                "visual_markup": visual_markup,
                "visual_heavy": visual_heavy,
                "printed_pdf": printed_pdf,
                "scrambled_pages": list(scrambled_pages),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return IngestedSource(
        source,
        snapshot_path,
        native_path,
        chunks,
        page_count=page_count,
        visual_markup=visual_markup,
        visual_heavy=visual_heavy,
        printed_pdf=printed_pdf,
        scrambled_pages=scrambled_pages,
    )


def print_url_to_pdf(url: str, output_path: Path) -> None:
    """Capture a rendered web page as a paginated PDF via headless chromium.

    Screen CSS is emulated (print stylesheets often hide the page's graphics),
    consent/investor-gate overlays are dismissed so they don't mask every
    printed page, the page is scrolled once so lazy-loaded images actually
    load, and the network-idle wait is best-effort (analytics beacons keep
    some pages from ever going idle).
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(url, wait_until="load", timeout=PRINT_NAVIGATION_TIMEOUT_MS)
            try:
                page.wait_for_load_state(
                    "networkidle", timeout=PRINT_NETWORK_IDLE_TIMEOUT_MS
                )
            except PlaywrightTimeoutError:
                pass
            for _ in range(MAX_CONSENT_DIALOGS):
                try:
                    page.get_by_role("button", name=CONSENT_BUTTON_PATTERN).first.click(
                        timeout=2_000
                    )
                    page.wait_for_timeout(500)
                except PlaywrightError:
                    break
            # Scroll slowly enough for lazy images AND scroll-triggered JS
            # charts to render, then let chart animations finish before
            # printing (a fast pass leaves chart bodies blank in the PDF).
            page.evaluate(
                """async () => {
                    for (let y = 0; y < document.body.scrollHeight; y += 600) {
                        window.scrollTo(0, y);
                        await new Promise((resolve) => setTimeout(resolve, 250));
                    }
                    window.scrollTo(0, 0);
                }"""
            )
            page.wait_for_timeout(2_000)
            page.emulate_media(media="screen")
            page.pdf(path=str(output_path), format="A4", print_background=True)
        finally:
            browser.close()


def count_visual_markup(html: str) -> dict[str, int]:
    return {
        tag: len(re.findall(rf"<{tag}\b", html, flags=re.IGNORECASE))
        for tag in VISUAL_MARKUP_TAGS
    }


def is_visual_heavy(visual_markup: dict[str, int]) -> bool:
    content_graphics = (
        visual_markup.get("img", 0)
        + visual_markup.get("canvas", 0)
        + visual_markup.get("figure", 0)
    )
    return content_graphics >= VISUAL_HEAVY_IMAGE_THRESHOLD


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "source"


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _resolve_local_file(
    row: dict[str, str], *, firm: str, title: str
) -> Path | None:
    """Resolve a source row's optional `local_file` column to a local PDF.

    `local_file` is a repo-relative path (resolved against PROJECT_ROOT). Its
    three-way contract, shared by every source CSV:
      - present and the file exists  -> ingest that local PDF; the row's URL is
        kept only as metadata (the mapped-local-PDF behavior);
      - present but missing on disk  -> hard error naming the row (never a silent
        fall back to the URL — a typo would otherwise fetch the wrong thing);
      - absent or empty              -> None; fetch the URL.
    A CSV without the column at all behaves as empty for every row (no local
    files), so an existing format keeps loading unchanged.
    """
    raw = (row.get("local_file") or "").strip()
    if not raw:
        return None
    path = PROJECT_ROOT / raw
    if not path.exists():
        raise FileNotFoundError(
            f"local_file '{raw}' for source '{firm} — {title}' does not exist "
            f"(resolved to {path}); fix the path or clear the column to fetch the URL"
        )
    return path


def _copy_pdf(source: SourceRecord, output_dir: Path) -> Path:
    if source.local_path is None:
        raise ValueError("PDF source has no local path; remote PDF fetch is not implemented yet")
    target = output_dir / source.local_path.name
    shutil.copy2(source.local_path, target)
    return target


def _extract_pdf_text(path: Path) -> tuple[str, int, tuple[int, ...]]:
    with pdfplumber.open(path) as pdf:
        text_parts: list[str] = []
        scrambled: list[int] = []
        for page_number, page in enumerate(pdf.pages, start=1):
            text_parts.append(page.extract_text() or "")
            if detect_scrambled_page(
                page.extract_words(use_text_flow=False),
                float(page.width),
                float(page.height),
            ):
                scrambled.append(page_number)
        return "\n\n".join(text_parts), len(pdf.pages), tuple(scrambled)


def detect_scrambled_page(
    words: list[dict[str, object]],
    page_width: float,
    page_height: float,
) -> bool:
    """Return True when a PDF page's extracted text is column-interleaved.

    Deterministic and model-free. `words` is a pdfplumber ``extract_words``
    list (each a mapping with ``x0``/``x1``/``top``/``bottom``). We look only
    for the physical signature of a multi-column page: a near-empty vertical
    band in the page interior that separates two well-populated columns across
    the full body height. A single-column page — however wide its lines — has
    text crossing every interior x, so it never trips this.
    """
    body = [
        word
        for word in words
        if SCRAMBLE_BODY_TOP_FRAC * page_height
        < float(word["top"])
        < SCRAMBLE_BODY_BOTTOM_FRAC * page_height
    ]
    if len(body) < SCRAMBLE_MIN_BODY_WORDS or page_width <= 0:
        return False
    y_top = min(float(word["top"]) for word in body)
    y_bottom = max(float(word["bottom"]) for word in body)
    y_span = y_bottom - y_top
    if y_span <= 0:
        return False

    for column_bin in range(SCRAMBLE_X_BINS):
        x = (column_bin + 0.5) / SCRAMBLE_X_BINS * page_width
        if not (SCRAMBLE_INTERIOR_MIN * page_width < x < SCRAMBLE_INTERIOR_MAX * page_width):
            continue
        left = right = 0
        covered_rows: set[int] = set()
        for word in body:
            if float(word["x1"]) < x:
                left += 1
            elif float(word["x0"]) > x:
                right += 1
            else:  # this word straddles x, so the gutter is not empty at this row
                row = int((float(word["top"]) - y_top) / y_span * SCRAMBLE_Y_ROWS)
                covered_rows.add(min(SCRAMBLE_Y_ROWS - 1, row))
        if (
            left >= SCRAMBLE_MIN_SIDE_WORDS
            and right >= SCRAMBLE_MIN_SIDE_WORDS
            and len(covered_rows) / SCRAMBLE_Y_ROWS <= SCRAMBLE_COVERAGE_MAX
        ):
            return True
    return False


def _pdf_chunks(
    path: Path,
    page_count: int,
    *,
    page_chunk_size: int = PDF_PAGE_CHUNK_SIZE,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for start in range(1, page_count + 1, page_chunk_size):
        end = min(start + page_chunk_size - 1, page_count)
        chunk_id = f"p{start}-{end}"
        chunks.append(Chunk(chunk_id=chunk_id, locator=f"p.{start}-{end}", source_path=path))
    return chunks


def _html_chunks(
    snapshot_path: Path,
    text_length: int,
    *,
    chunk_size: int = HTML_CHAR_CHUNK_SIZE,
) -> list[Chunk]:
    if text_length == 0:
        return [Chunk(chunk_id="char:0-0", locator="char:0-0", source_path=snapshot_path)]
    chunks: list[Chunk] = []
    for start in range(0, text_length, chunk_size):
        end = min(start + chunk_size, text_length)
        locator = f"char:{start}-{end}"
        chunks.append(Chunk(chunk_id=locator, locator=locator, source_path=snapshot_path))
    return chunks


def _fetch_html(url: str) -> str:
    response = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36"
            )
        },
    )
    response.raise_for_status()
    return response.text


def _chunk_to_dict(chunk: Chunk) -> dict[str, str]:
    return {
        "chunk_id": chunk.chunk_id,
        "locator": chunk.locator,
        "source_path": str(chunk.source_path),
    }
