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
PREV_EXCEL_DIR = PROJECT_ROOT / "prev-excel"

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


def load_pilot_sources(path: str | Path = PILOT_CSV) -> list[SourceRecord]:
    rows = _read_csv(path)
    sources: list[SourceRecord] = []
    for row in rows:
        local_path = _pilot_local_pdf_for(row["Firm"])
        url = row["MR Link"]
        sources.append(
            SourceRecord(
                source_id=slugify(f"{row['Firm']} {row['Source']}"),
                firm=row["Firm"],
                date=row["Date"],
                source=row["Source"],
                url=url,
                resolved_url=resolve_url(url),
                source_type="pdf" if local_path else detect_source_type(url),
                local_path=local_path,
            )
        )
    return sources


def load_target_sources(path: str | Path = TARGET_SOURCES_CSV) -> list[SourceRecord]:
    rows = _read_csv(path)
    sources: list[SourceRecord] = []
    for index, row in enumerate(rows, start=1):
        raw_url = row["Source Link"]
        source_id = row["Id"] or slugify(f"{index} {row['Firm']} {row['Title']}")
        resolved_url = resolve_url(raw_url)
        sources.append(
            SourceRecord(
                source_id=str(source_id),
                firm=row["Firm"],
                date=row["Published At"],
                source=row["Title"],
                url=raw_url,
                resolved_url=resolved_url,
                source_type=detect_source_type(resolved_url),
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
    if source.source_type == "pdf":
        native_path = _copy_pdf(source, output_dir)
        snapshot_text, page_count = _extract_pdf_text(native_path)
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
            snapshot_text, page_count = _extract_pdf_text(native_path)
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


def _pilot_local_pdf_for(firm: str) -> Path | None:
    normalized = re.sub(r"[^a-z0-9]+", "", firm.lower())
    mapping = {
        "alliancebernstein": PREV_EXCEL_DIR / "alliance-bernstein.pdf",
        "jpmorganassetmanagement": PREV_EXCEL_DIR / "jp-morgan.pdf",
        "pimco": PREV_EXCEL_DIR / "PIMCO.pdf",
    }
    path = mapping.get(normalized)
    return path if path and path.exists() else None


def _copy_pdf(source: SourceRecord, output_dir: Path) -> Path:
    if source.local_path is None:
        raise ValueError("PDF source has no local path; remote PDF fetch is not implemented yet")
    target = output_dir / source.local_path.name
    shutil.copy2(source.local_path, target)
    return target


def _extract_pdf_text(path: Path) -> tuple[str, int]:
    with pdfplumber.open(path) as pdf:
        return "\n\n".join(page.extract_text() or "" for page in pdf.pages), len(pdf.pages)


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
