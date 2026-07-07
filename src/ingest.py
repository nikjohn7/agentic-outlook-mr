"""Thin deterministic ingestion helpers for source snapshots and chunks."""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pdfplumber
import requests
import trafilatura
from htmldate import find_date


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PILOT_CSV = PROJECT_ROOT / "prev-excel" / "pilot.csv"
TARGET_SOURCES_CSV = PROJECT_ROOT / "excel-file" / "Target Ingestion List.csv"

PDF_PAGE_CHUNK_SIZE = 5
HTML_CHAR_CHUNK_SIZE = 8000
MAX_SOURCES_PER_RUN = 20

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Some manager sites are slow or intermittently reset requests. Plain fetches
# get three 90s attempts, but 4xx anti-bot responses are not retried here.
FETCH_TIMEOUT_SECONDS = 90
FETCH_MAX_ATTEMPTS = 3
FETCH_BACKOFF_SECONDS = (2, 8)
FETCH_BROWSER_FALLBACK_STATUSES = frozenset({401, 403, 406, 429})

# Source-CSV header aliases. A pilot-family CSV names five canonical fields;
# each accepts any of these headers (case-insensitive, trimmed) so a real-world
# export using e.g. `Entity Name` / `Title` / `External link` loads without
# editing. `firm`, `source`, and `url` are required; `date` and `local_file`
# are optional (a missing date is "", a missing local_file fetches the URL).
_COLUMN_ALIASES = {
    "firm": ("firm", "entity name", "entity", "manager", "asset manager", "company", "provider"),
    "date": ("date", "published at", "published", "publish date"),
    "source": ("source", "title", "document title", "document"),
    "url": ("url", "mr link", "external link", "source link", "link"),
    "local_file": ("local_file", "local file", "local pdf", "file"),
}
_REQUIRED_SOURCE_FIELDS = ("firm", "source", "url")

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

# Document dates: the source CSV's date is UNRELIABLE (client instruction,
# 2026-07-07) and is never used for the run's outputs. The Date on every output
# row comes only from the document itself, as strict DD/MM/YYYY, or stays blank.
# For HTML the fetched markup goes through htmldate (meta tags, JSON-LD, visible
# text — publication date, not update date), which already normalizes to
# DD/MM/YYYY. For PDFs only the top of the first page's text is scanned —
# cover/masthead territory — and only a full worded date (day + month + year)
# is accepted: numeric forms (15/06/2026) are skipped as DD/MM-vs-MM/DD
# ambiguous, a bare month-year ("June 2026") yields blank rather than
# fabricating a day, and PDF metadata (CreationDate) is never used because a
# downloaded or print-captured file carries the capture date, not the
# publication date. Result is always DD/MM/YYYY or "".
PDF_DATE_SCAN_CHARS = 1500
_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_MONTH_RE = "|".join(_MONTH_NAMES)
_YEAR_RE = r"(?:19|20)\d{2}"
_DAY_FIRST_DATE_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RE})\s+({_YEAR_RE})\b"
)
_MONTH_FIRST_DATE_RE = re.compile(
    rf"\b({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+({_YEAR_RE})\b"
)

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

# OCR candidates use the same 200 chars/page floor as confidence.py's PDF read
# quality check. Pages below this are likely image-only, so their OCR text
# replaces only that page's snapshot text when local OCR tools are available.
OCR_MIN_CHARS_PER_PAGE = 200
OCR_RENDER_DPI = 200


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
    ocr_pages: tuple[int, ...] = ()  # 1-indexed pages detected as image-only / low text layer
    ocr_note: str = ""  # empty when no OCR issue; otherwise applied/skipped details


@dataclass(frozen=True, slots=True)
class HtmlFetchResult:
    html: str
    fetched_via: str


def load_pilot_sources(path: str | Path = PILOT_CSV) -> list[SourceRecord]:
    """Load a source CSV in the pilot column family.

    Canonical fields — firm, date, source (title), url, and optional local_file —
    each accept a few header aliases (see `_COLUMN_ALIASES`), so an export CSV
    using `Entity Name` / `Title` / `External link` loads with no editing.
    A row's `source_type` is `pdf` when it resolves to a local PDF (`local_file`)
    or its URL points at a `.pdf` (fetched remotely); `txt` when local_file is a
    plain-text transcript (video sources ship as transcripts); otherwise `html`.
    See `_resolve_local_file` for the local_file contract. Any second test set is
    a CSV of this family — pointing `--sources <path>` at it needs no code change.
    """
    rows = _read_csv(path)
    header = _map_source_headers(rows, path)
    sources: list[SourceRecord] = []
    for row in rows:
        firm = row[header["firm"]].strip()
        title = row[header["source"]].strip()
        url = row[header["url"]].strip()
        date = row[header["date"]].strip() if "date" in header else ""
        local_raw = row[header["local_file"]] if "local_file" in header else ""
        local_path = _resolve_local_file(local_raw, firm=firm, title=title)
        resolved_url = resolve_url(url)
        sources.append(
            SourceRecord(
                source_id=slugify(f"{firm} {title}"),
                firm=firm,
                date=date,
                source=title,
                url=url,
                resolved_url=resolved_url,
                source_type=(
                    _local_source_type(local_path)
                    if local_path
                    else detect_source_type(resolved_url)
                ),
                local_path=local_path,
            )
        )
    return sources


def _map_source_headers(rows: list[dict[str, str]], path: str | Path) -> dict[str, str]:
    """Map a source CSV's actual headers to canonical field names via aliases.

    Returns {canonical field -> actual header}. Raises if a required field
    (firm/source/url) has no matching header, naming what was seen."""
    fieldnames = list(rows[0].keys()) if rows else []
    mapping: dict[str, str] = {}
    for raw_header in fieldnames:
        canon = _match_header_alias(raw_header)
        if canon and canon not in mapping:
            mapping[canon] = raw_header
    missing = [field for field in _REQUIRED_SOURCE_FIELDS if field not in mapping]
    if missing:
        raise ValueError(
            f"source CSV {path} is missing required column(s) {missing}; "
            f"headers seen: {fieldnames}. Each canonical field accepts these "
            f"aliases (case-insensitive): {_COLUMN_ALIASES}"
        )
    return mapping


def _match_header_alias(header: str | None) -> str | None:
    if not header:
        return None
    key = header.strip().lower()
    for canon, aliases in _COLUMN_ALIASES.items():
        if key in aliases:
            return canon
    return None


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
        local_path = _resolve_local_file(row.get("local_file"), firm=firm, title=title)
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
                source_type=(
                    _local_source_type(local_path)
                    if local_path
                    else detect_source_type(resolved_url)
                ),
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


def _local_source_type(local_path: Path) -> str:
    return "txt" if local_path.suffix.lower() == ".txt" else "pdf"


def date_provenance(source_type: str) -> str:
    """Where a filled document date came from, keyed by source type:
    the PDF's first-page text, the transcript's text, or the HTML markup."""
    return {"pdf": "pdf_text", "txt": "txt_text"}.get(source_type, "html")


def extract_html_date(html: str) -> str:
    """Publication date the HTML states (meta tags / JSON-LD / visible text),
    as DD/MM/YYYY, or "" when none is found. `original_date=True` asks
    htmldate for the publication date rather than the last-update date."""
    try:
        found = find_date(html, original_date=True)
    except ValueError:
        return ""
    if not found:
        return ""
    year, month, day = found.split("-")
    return f"{day}/{month}/{year}"


def extract_pdf_text_date(snapshot_text: str) -> str:
    """Date stated near the top of a PDF's first page (see the constant block
    above for what is and is not accepted). Only a full worded date (day +
    month + year) is returned, as DD/MM/YYYY; a bare month-year yields "" rather
    than a fabricated day; otherwise ""."""
    window = snapshot_text[:PDF_DATE_SCAN_CHARS]
    day_first = _DAY_FIRST_DATE_RE.search(window)
    if day_first:
        day, month_name, year = day_first.groups()
        return f"{int(day):02d}/{_MONTH_NAMES.index(month_name) + 1:02d}/{year}"
    month_first = _MONTH_FIRST_DATE_RE.search(window)
    if month_first:
        month_name, day, year = month_first.groups()
        return f"{int(day):02d}/{_MONTH_NAMES.index(month_name) + 1:02d}/{year}"
    return ""


def create_snapshot(
    source: SourceRecord,
    work_dir: str | Path,
    *,
    printer=None,
    downloader=None,
    browser_fetcher=None,
    ocr_runner=None,
) -> IngestedSource:
    """printer overrides the headless-browser print-to-PDF step, and downloader
    overrides the remote-PDF fetch (both for tests)."""
    output_dir = Path(work_dir) / source.source_id
    output_dir.mkdir(parents=True, exist_ok=True)

    page_count: int | None = None
    visual_markup: dict[str, int] | None = None
    visual_heavy = False
    printed_pdf = False
    scrambled_pages: tuple[int, ...] = ()
    ocr_pages: tuple[int, ...] = ()
    ocr_note = ""
    fetched_via = ""
    document_date = ""
    if source.source_type == "txt":
        # A local plain-text transcript (video sources): the text IS the
        # document, so it doubles as the snapshot / quote-check corpus, and
        # chunking/locators follow the HTML char-range convention.
        native_path = output_dir / source.local_path.name
        shutil.copy2(source.local_path, native_path)
        snapshot_text = native_path.read_text(encoding="utf-8")
        snapshot_path = output_dir / "snapshot.txt"
        snapshot_path.write_text(snapshot_text, encoding="utf-8")
        chunks = _html_chunks(snapshot_path, len(snapshot_text))
        document_date = extract_pdf_text_date(snapshot_text)
    elif source.source_type == "pdf":
        native_path = _copy_pdf(source, output_dir, downloader=downloader)
        snapshot_text, page_count, scrambled_pages, ocr_pages, ocr_note = _extract_pdf_text(
            native_path, ocr_runner=ocr_runner
        )
        snapshot_path = output_dir / "snapshot.txt"
        snapshot_path.write_text(snapshot_text, encoding="utf-8")
        chunks = _pdf_chunks(native_path, page_count)
        document_date = extract_pdf_text_date(snapshot_text)
    else:
        html_path = output_dir / "snapshot.html"
        html_result = _fetch_html(source.resolved_url, browser_fetcher=browser_fetcher)
        if isinstance(html_result, str):  # Backcompat for tests that patch _fetch_html.
            html = html_result
            fetched_via = "requests"
        else:
            html = html_result.html
            fetched_via = html_result.fetched_via
        html_path.write_text(html, encoding="utf-8")
        visual_markup = count_visual_markup(html)
        visual_heavy = is_visual_heavy(visual_markup)
        document_date = extract_html_date(html)
        snapshot_path = output_dir / "snapshot.txt"
        if visual_heavy:
            native_path = output_dir / PRINTED_PDF_NAME
            (printer or print_url_to_pdf)(source.resolved_url, native_path)
            snapshot_text, page_count, scrambled_pages, ocr_pages, ocr_note = _extract_pdf_text(
                native_path, ocr_runner=ocr_runner
            )
            snapshot_path.write_text(snapshot_text, encoding="utf-8")
            chunks = _pdf_chunks(native_path, page_count)
            printed_pdf = True
        else:
            native_path = html_path
            text = trafilatura.extract(html, include_tables=True) or ""
            snapshot_path.write_text(text, encoding="utf-8")
            chunks = _html_chunks(snapshot_path, len(text))

    # The source CSV's date is unreliable (client instruction) and is discarded
    # here: the run's Date comes only from the document itself, or stays blank.
    date_from = date_provenance(source.source_type) if document_date else ""
    source = replace(source, date=document_date)

    (output_dir / "chunks.json").write_text(
        json.dumps([_chunk_to_dict(chunk) for chunk in chunks], indent=2),
        encoding="utf-8",
    )
    (output_dir / "ingest_meta.json").write_text(
        json.dumps(
            {
                "source_id": source.source_id,
                "source_type": source.source_type,
                "date": source.date,
                "date_from": date_from,
                "page_count": page_count,
                "visual_markup": visual_markup,
                "visual_heavy": visual_heavy,
                "printed_pdf": printed_pdf,
                "scrambled_pages": list(scrambled_pages),
                "ocr_pages": list(ocr_pages),
                "ocr_note": ocr_note,
                "fetched_via": fetched_via,
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
        ocr_pages=ocr_pages,
        ocr_note=ocr_note,
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
            dismiss_consent_dialogs(page, playwright_error=PlaywrightError)
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


def fetch_html_with_browser(url: str) -> str:
    """Fetch rendered HTML via headless chromium for anti-bot-blocked pages."""
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
            dismiss_consent_dialogs(page, playwright_error=PlaywrightError)
            return page.content()
        finally:
            browser.close()


def dismiss_consent_dialogs(page, *, playwright_error=Exception) -> None:
    """Best-effort shared dismissal for cookie/professional-investor overlays."""
    for _ in range(MAX_CONSENT_DIALOGS):
        try:
            page.get_by_role("button", name=CONSENT_BUTTON_PATTERN).first.click(timeout=2_000)
            page.wait_for_timeout(500)
        except playwright_error:
            break


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
    local_file: str | None, *, firm: str, title: str
) -> Path | None:
    """Resolve a source row's optional `local_file` value to a local PDF or
    plain-text transcript (`.txt`, for video sources).

    `local_file` is a repo-relative path (resolved against PROJECT_ROOT). Its
    three-way contract, shared by every source CSV:
      - present and the file exists  -> ingest that local file; the row's URL is
        kept only as metadata (the mapped-local-file behavior);
      - present but missing on disk  -> hard error naming the row (never a silent
        fall back to the URL — a typo would otherwise fetch the wrong thing);
      - absent or empty              -> None; fetch the URL (a `.pdf` URL is
        downloaded and read as a PDF, anything else takes the HTML path).
    A CSV without the column at all behaves as empty for every row (no local
    files), so an existing format keeps loading unchanged.
    """
    raw = (local_file or "").strip()
    if not raw:
        return None
    path = PROJECT_ROOT / raw
    if not path.exists():
        raise FileNotFoundError(
            f"local_file '{raw}' for source '{firm} — {title}' does not exist "
            f"(resolved to {path}); fix the path or clear the column to fetch the URL"
        )
    if path.suffix.lower() not in {".pdf", ".txt"}:
        raise ValueError(
            f"local_file '{raw}' for source '{firm} — {title}' must be a .pdf "
            f"or a .txt transcript; got '{path.suffix}'"
        )
    return path


def _copy_pdf(source: SourceRecord, output_dir: Path, *, downloader=None) -> Path:
    """Materialize the source's PDF into output_dir. A local_file is copied; a
    PDF URL (no local_file) is downloaded. downloader overrides the fetch in
    tests: (url, output_dir) -> written Path."""
    if source.local_path is not None:
        target = output_dir / source.local_path.name
        shutil.copy2(source.local_path, target)
        return target
    return (downloader or _download_pdf)(source.resolved_url, output_dir)


def _download_pdf(url: str, output_dir: Path) -> Path:
    """Fetch a remote PDF into output_dir, named from the URL path. Verifies the
    response is actually a PDF (`%PDF` magic) so an HTML error/consent page
    returned for a `.pdf` URL fails loudly instead of feeding pdfplumber junk."""
    response = _request_get_with_retries(url)
    response.raise_for_status()
    if not response.content[:4] == b"%PDF":
        raise ValueError(
            f"URL {url} did not return a PDF (body does not start with %PDF; "
            f"content-type {response.headers.get('Content-Type', '?')})"
        )
    target = output_dir / _pdf_filename_from_url(url)
    target.write_bytes(response.content)
    return target


def _pdf_filename_from_url(url: str) -> str:
    name = Path(urlparse(url).path).name or "download.pdf"
    return name if name.lower().endswith(".pdf") else f"{name}.pdf"


def _extract_pdf_text(
    path: Path,
    *,
    ocr_runner=None,
) -> tuple[str, int, tuple[int, ...], tuple[int, ...], str]:
    with pdfplumber.open(path) as pdf:
        text_parts: list[str] = []
        scrambled: list[int] = []
        ocr_candidates: list[int] = []
        for page_number, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
            if len(page_text.strip()) < OCR_MIN_CHARS_PER_PAGE:
                ocr_candidates.append(page_number)
            if detect_scrambled_page(
                page.extract_words(use_text_flow=False),
                float(page.width),
                float(page.height),
            ):
                scrambled.append(page_number)

    ocr_note = ""
    if ocr_candidates:
        ocr_text, ocr_note = _ocr_pdf_pages(path, ocr_candidates, runner=ocr_runner)
        for page_number, text in ocr_text.items():
            if text.strip():
                text_parts[page_number - 1] = text
    return (
        "\n\n".join(text_parts),
        len(text_parts),
        tuple(scrambled),
        tuple(ocr_candidates),
        ocr_note,
    )


def _ocr_pdf_pages(
    path: Path,
    pages: list[int],
    *,
    runner=None,
) -> tuple[dict[int, str], str]:
    """OCR selected PDF pages. Missing tools or page-level failures never crash.

    Uses Poppler `pdftoppm` for page rendering and Tesseract for OCR. The caller
    records the detected page numbers even when OCR cannot run.
    """
    missing = [tool for tool in ("pdftoppm", "tesseract") if shutil.which(tool) is None]
    if missing:
        return {}, f"ocr skipped: missing tool(s): {', '.join(missing)}"

    runner = runner or subprocess.run
    texts: dict[int, str] = {}
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        for page_number in pages:
            prefix = temp_path / f"page-{page_number}"
            render = runner(
                [
                    "pdftoppm",
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-r",
                    str(OCR_RENDER_DPI),
                    "-png",
                    "-singlefile",
                    str(path),
                    str(prefix),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if render.returncode != 0:
                failures.append(f"p.{page_number} render failed: {render.stderr.strip()[:120]}")
                continue
            image_path = prefix.with_suffix(".png")
            ocr = runner(
                ["tesseract", str(image_path), "stdout", "--psm", "6"],
                capture_output=True,
                text=True,
                check=False,
            )
            if ocr.returncode != 0:
                failures.append(f"p.{page_number} OCR failed: {ocr.stderr.strip()[:120]}")
                continue
            texts[page_number] = ocr.stdout
    note = f"ocr applied to pages: {', '.join(f'p.{page}' for page in sorted(texts))}"
    if failures:
        note = f"{note}; skipped: {'; '.join(failures)}" if texts else "ocr skipped: " + "; ".join(failures)
    return texts, note


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


def _request_get_with_retries(url: str, *, session=None):
    """GET with bounded retries for transient network and 5xx failures.

    4xx responses are returned immediately after raise_for_status() fails; they
    are not retried because auth/anti-bot blocks do not heal with repetition.
    """
    client = session or requests
    last_error: Exception | None = None
    for attempt in range(FETCH_MAX_ATTEMPTS):
        try:
            response = client.get(
                url,
                timeout=FETCH_TIMEOUT_SECONDS,
                headers={"User-Agent": _BROWSER_UA},
            )
            if 500 <= response.status_code < 600 and attempt < FETCH_MAX_ATTEMPTS - 1:
                _sleep_before_retry(attempt)
                continue
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError:
            raise
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_error = exc
            if attempt >= FETCH_MAX_ATTEMPTS - 1:
                raise
            _sleep_before_retry(attempt)
    if last_error is not None:
        raise last_error
    raise RuntimeError("request retry helper exhausted without a response")


def _sleep_before_retry(attempt: int) -> None:
    if attempt < len(FETCH_BACKOFF_SECONDS):
        time.sleep(FETCH_BACKOFF_SECONDS[attempt])


def _fetch_html(url: str, *, session=None, browser_fetcher=None) -> HtmlFetchResult:
    try:
        response = _request_get_with_retries(url, session=session)
        return HtmlFetchResult(response.text, "requests")
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status not in FETCH_BROWSER_FALLBACK_STATUSES:
            raise
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        pass
    html = (browser_fetcher or fetch_html_with_browser)(url)
    return HtmlFetchResult(html, "browser")


def _chunk_to_dict(chunk: Chunk) -> dict[str, str]:
    return {
        "chunk_id": chunk.chunk_id,
        "locator": chunk.locator,
        "source_path": str(chunk.source_path),
    }
