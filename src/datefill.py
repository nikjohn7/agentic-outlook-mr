"""Post-run document-date backfill.

`datefill.py` is a standalone tool, run AFTER a run's `output.csv` is frozen. It
NEVER modifies a run output in place: the default mode writes a `datefill.csv`
patch + a summary, and a separate `--apply` step writes a NEW output.csv with the
`Date` column filled. It mirrors `src/crosscheck.py`'s shape (report/patch
generator + separate apply; injectable LLM runner; mock-runner tests).

    .venv/bin/python -m src.datefill \\
        --output <path/to/output.csv> --sources <master source csv> \\
        --out-dir <dir> [--engine codex --model gpt-5.6-luna --effort high]

    .venv/bin/python -m src.datefill --apply \\
        --output <path/to/output.csv> --patch <dir>/datefill.csv \\
        --write <path/to/new-output.csv>

The in-run date extraction (tier 1: htmldate for HTML, a full worded date on PDF
page 1) is unchanged. This tool backfills the sources tier 1 left blank, in five
deterministic steps:

1. Find every distinct undated (firm, title) source in `output.csv` and join it
   to the master source CSV (for the URL and any `local_file`).
2. One agent call per source (`prompts/find_date.md`): the model reads the
   pre-extracted document text (and the file, for a date printed only in a cover
   image) and reports any STATED publication date it sees, or hunts the landing
   page. It returns categorical claims only — never a metadata date, never an
   invented one.
3. Deterministic, fail-closed verification of every claim: the stated quote must
   re-appear in the freshly extracted document text; a landing-page date must
   appear on a page that actually references this document; the date string is
   parsed here (never trusting the agent's formatting) and its year must fall in
   the batch window.
4. A deterministic metadata fallback (no LLM): htmldate for HTML, the PDF's
   CreationDate for publisher-original PDFs only (browser print-to-PDF captures
   stamp the save time, so they are excluded by producer/creator signature).
5. Precedence: stated full date > metadata > landing-page full date >
   month-year partial rendered `01/MM/YYYY`; quarter/season partials never fill.

The engine step is a cascade (model revamp 2026-07-10): codex/gpt-5.6-luna/high
sweeps first, then claude/sonnet/medium runs ONLY on sources still blank
afterward. Both engines' flags are overridable.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pdfplumber
import trafilatura

from src import ingest, llm
from src.confidence import normalize_quote_text
from src.eval import normalize_firm

FIND_DATE_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "find_date.md"

# The 98-row deliverable is a mid-2026 outlook batch: every real publication date
# falls in this window. A parsed candidate or metadata date whose year sits
# outside it is a trap ("data as of 2024"), a garbage epoch stamp
# (D:19700101 from a broken PDF writer), or a mis-parse, and is rejected. Widen
# this only when a batch legitimately spans other years.
YEAR_MIN = 2025
YEAR_MAX = 2026

# One agent call can web-search and read a file, so give it room; a hang past
# this is treated as a per-source agent failure (logged, sweep continues).
AGENT_TIMEOUT_SECONDS = 420

# How much extracted text to hand the agent: the head (cover/masthead) plus the
# tail (back page / footer colophon) where publication dates live.
DOC_TEXT_HEAD_CHARS = 6000
DOC_TEXT_TAIL_CHARS = 3000

# Browser "Save/Print as PDF" writers stamp the SAVE time into CreationDate, not
# the publication date — exactly the failure the spec's print-capture guard
# names (our own Playwright captures are Skia/PDF; manual Firefox/Safari "Save as
# PDF" on macOS goes through Quartz). Their metadata is a capture time, so it is
# never used as a publication date. Publisher-original PDFs (Adobe InDesign,
# Word, PowerPoint, Acrobat, Aspose, ...) are unaffected.
_PRINT_CAPTURE_MARKERS = ("skia", "quartz", "chromium", "chrome", "firefox", "safari", "mozilla")

# find_date.md categorical enums (verified against on parse, like crosscheck).
WHERE_STATED = "stated_in_document"
WHERE_LANDING = "landing_page"
_WHERE_VALUES = frozenset({WHERE_STATED, WHERE_LANDING})

GRAN_FULL = "full"
GRAN_MONTH_YEAR = "month_year"
GRAN_QUARTER = "quarter_or_season"
_GRANULARITY_VALUES = frozenset({GRAN_FULL, GRAN_MONTH_YEAR, GRAN_QUARTER})

# Fill provenance codes written to datefill.csv `date_from`.
FROM_STATED = "stated_document"
FROM_PDF_META = "pdf_metadata"
FROM_HTML_META = "html_metadata"
FROM_LANDING = "landing_page"
FROM_PARTIAL = "partial_month"

# Engine cascade defaults (model revamp 2026-07-10). Primary sweep is
# codex/gpt-5.6-luna/high (was codex/gpt-5.5/low); the cascade over still-blank
# sources is claude/sonnet/medium (was claude/sonnet/low).
DEFAULT_PRIMARY_ENGINE = "codex"
DEFAULT_PRIMARY_MODEL = "gpt-5.6-luna"
DEFAULT_PRIMARY_EFFORT = "high"
DEFAULT_CASCADE_ENGINE = "claude"
DEFAULT_CASCADE_MODEL = "sonnet"
DEFAULT_CASCADE_EFFORT = "medium"

Runner = Callable[[list[str], str | None], subprocess.CompletedProcess[str]]


class DatefillError(RuntimeError):
    """A fatal problem loading inputs."""


# --------------------------------------------------------------------------- #
# Normalization + small parsing helpers
# --------------------------------------------------------------------------- #


def normalize_title(title: str) -> str:
    """Fold a document title to a join key: NFKC, lowercased, whitespace
    collapsed. Paired with `src.eval.normalize_firm` for the (firm, title) join,
    it tolerates casing/spacing differences between the output and master CSVs."""
    text = unicodedata.normalize("NFKC", title or "").lower()
    return re.sub(r"\s+", " ", text).strip()


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
# Longest names first so "september" wins over "sep" in the alternation.
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))


@dataclass(frozen=True, slots=True)
class ParsedDate:
    day: int | None
    month: int
    year: int

    def render(self) -> str:
        """DD/MM/YYYY. A month-year partial (day is None) renders with a
        synthetic day-of-month 01; callers flag that separately."""
        return f"{(self.day or 1):02d}/{self.month:02d}/{self.year}"


def _make_date(day: int | None, month: int, year: int) -> ParsedDate | None:
    """Validate components and the year window; return None if anything is off
    (never guess a repair)."""
    if not (YEAR_MIN <= year <= YEAR_MAX):
        return None
    if not (1 <= month <= 12):
        return None
    if day is not None and not (1 <= day <= 31):
        return None
    return ParsedDate(day, month, year)


def _month_num(word: str) -> int | None:
    return _MONTHS.get(word.strip(". ").lower())


def parse_verbatim_date(date_verbatim: str, granularity: str) -> ParsedDate | None:
    """Parse the agent's verbatim date string into a ParsedDate, deterministically.

    The agent's own formatting is never trusted — only what the regexes recover
    from the literal string, and only within the year window. Quarter/season
    granularity never yields a fillable date (returns None). Extends the worded-
    date parsing in `src/ingest.py` with abbreviations, ISO, and disambiguated
    numeric forms.
    """
    if granularity == GRAN_QUARTER:
        return None
    text = re.sub(r"\s+", " ", (date_verbatim or "").strip())
    if not text:
        return None
    if granularity == GRAN_MONTH_YEAR:
        return _parse_month_year(text)
    if granularity == GRAN_FULL:
        return _parse_full(text)
    return None


def _parse_full(text: str) -> ParsedDate | None:
    low = text.lower()
    # 17 June 2026 / 17th Jun 2026
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALT})\.?\s+(\d{{4}})\b", low)
    if m:
        return _make_date(int(m.group(1)), _month_num(m.group(2)), int(m.group(3)))
    # June 17, 2026 / Jun 17 2026
    m = re.search(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", low)
    if m:
        return _make_date(int(m.group(2)), _month_num(m.group(1)), int(m.group(3)))
    # ISO 2026-06-17
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", low)
    if m:
        return _make_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    # numeric d/m/y or m/d/y — only when the day component disambiguates (>12);
    # a fully ambiguous pair (both <= 12) is rejected rather than guessed.
    m = re.search(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b", low)
    if m:
        day, month = _disambiguate_numeric(int(m.group(1)), int(m.group(2)))
        if day is None:
            return None
        return _make_date(day, month, int(m.group(3)))
    return None


def _disambiguate_numeric(a: int, b: int) -> tuple[int | None, int | None]:
    """Resolve a numeric D/M pair only when one component exceeds 12; otherwise
    it is genuinely ambiguous (DD/MM vs MM/DD) and returns (None, None)."""
    if a > 12 and 1 <= b <= 12:
        return a, b
    if b > 12 and 1 <= a <= 12:
        return b, a
    return None, None


def _parse_month_year(text: str) -> ParsedDate | None:
    low = text.lower()
    m = re.search(rf"\b({_MONTH_ALT})\.?\s+(\d{{4}})\b", low)
    if m:
        return _make_date(None, _month_num(m.group(1)), int(m.group(2)))
    m = re.search(rf"\b(\d{{4}})\s+({_MONTH_ALT})\b", low)
    if m:
        return _make_date(None, _month_num(m.group(2)), int(m.group(1)))
    m = re.search(r"\b(\d{1,2})[/.](\d{4})\b", low)  # 06/2026
    if m:
        return _make_date(None, int(m.group(1)), int(m.group(2)))
    m = re.search(r"\b(\d{4})-(\d{1,2})\b", low)  # 2026-06
    if m:
        return _make_date(None, int(m.group(2)), int(m.group(1)))
    return None


def _parse_ddmmyyyy(value: str) -> ParsedDate | None:
    """Parse a `DD/MM/YYYY` string produced by ingest.extract_html_date back into
    a windowed ParsedDate (htmldate already normalized it, so this is exact)."""
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", value or "")
    if not m:
        return None
    return _make_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


# --------------------------------------------------------------------------- #
# PDF / HTML metadata (deterministic, no LLM)
# --------------------------------------------------------------------------- #


def _is_browser_print(producer: str, creator: str) -> bool:
    blob = f"{producer} {creator}".lower()
    return any(marker in blob for marker in _PRINT_CAPTURE_MARKERS)


def pdf_metadata_date_from_meta(meta: dict | None) -> ParsedDate | None:
    """A PDF's CreationDate as a windowed ParsedDate, or None.

    Returns None for browser print-to-PDF captures (their CreationDate is the
    save time, not a publication date) — the spec's print-capture guard, applied
    by producer/creator signature so it also catches manual Firefox/Safari "Save
    as PDF" files, not just our own Playwright captures.
    """
    meta = meta or {}
    if _is_browser_print(str(meta.get("Producer") or ""), str(meta.get("Creator") or "")):
        return None
    return _parse_pdf_date_string(str(meta.get("CreationDate") or ""))


def _parse_pdf_date_string(raw: str) -> ParsedDate | None:
    # PDF date syntax: D:YYYYMMDDHHmmSS... ; some XMP writers use YYYY-MM-DD.
    m = re.search(r"D:(\d{4})(\d{2})(\d{2})", raw) or re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", raw)
    if not m:
        return None
    return _make_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))


def pdf_metadata_date(path: Path) -> ParsedDate | None:
    """Open a PDF and read its CreationDate (guarded). Any read error → None."""
    try:
        with pdfplumber.open(path) as pdf:
            meta = dict(pdf.metadata or {})
    except Exception:  # noqa: BLE001 — an unreadable PDF simply yields no metadata date
        return None
    return pdf_metadata_date_from_meta(meta)


# --------------------------------------------------------------------------- #
# Undated-source collection
# --------------------------------------------------------------------------- #


def _read_output_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        raise DatefillError(f"output not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise DatefillError(f"CSV is empty: {path}")
        for required in ("Firm", "Source", "Date"):
            if required not in reader.fieldnames:
                raise DatefillError(f"{path} missing required column: {required}")
        return list(reader), list(reader.fieldnames)


def collect_undated_sources(
    output_rows: list[dict[str, str]], master: list[ingest.SourceRecord]
) -> tuple[list[ingest.SourceRecord], list[tuple[str, str]]]:
    """Distinct undated (firm, title) sources joined to their master records.

    A blank-`Date` row's `Source` may pipe-join several titles (grouped rows);
    each member is split out and joined to the master on normalized firm+title.
    Returns (matched master records, unmatched (firm, title) pairs) — an
    unmatched source is logged, never a crash (the master carries a known title
    typo, "Reisnsurance", and a firm variant, "Aon's")."""
    index = {(normalize_firm(r.firm), normalize_title(r.source)): r for r in master}
    seen: set[tuple[str, str]] = set()
    sources: list[ingest.SourceRecord] = []
    unmatched: list[tuple[str, str]] = []
    for row in output_rows:
        if (row.get("Date") or "").strip():
            continue
        firm = (row.get("Firm") or "").strip()
        for title in _split_pipe(row.get("Source") or ""):
            key = (normalize_firm(firm), normalize_title(title))
            if key in seen:
                continue
            seen.add(key)
            record = index.get(key)
            if record is None:
                unmatched.append((firm, title))
            else:
                sources.append(record)
    return sources, unmatched


def _split_pipe(value: str) -> list[str]:
    return [part.strip() for part in value.split("|") if part.strip()]


# --------------------------------------------------------------------------- #
# Document extraction (reuses ingest helpers)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ExtractedDoc:
    text: str
    html: str | None
    native_path: Path | None
    source_type: str


def _clip_for_prompt(text: str) -> str:
    """Head + tail slice of the document text for the agent (covers/back pages)."""
    if len(text) <= DOC_TEXT_HEAD_CHARS + DOC_TEXT_TAIL_CHARS:
        return text
    return (
        text[:DOC_TEXT_HEAD_CHARS]
        + "\n\n[... middle of document omitted ...]\n\n"
        + text[-DOC_TEXT_TAIL_CHARS:]
    )


# --------------------------------------------------------------------------- #
# Agent call (find_date.md) — one per source, cascade of two engines
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Candidate:
    where: str
    date_verbatim: str
    locator: str
    evidence_quote: str
    granularity: str


def _agent_command(
    engine: str, model: str | None, effort: str | None, prompt: str
) -> tuple[list[str], str | None]:
    """Build the CLI command for a tools-enabled date-hunt agent.

    Returns (command, stdin_text). codex takes the prompt as an argv positional
    (stdin closed); claude takes it via stdin because `--allowed-tools` is
    variadic and would otherwise swallow a trailing prompt argument (client note,
    2026-07-10). codex needs `--search` for web_search; claude is given the read
    and fetch tools but NOT Bash, so it cannot stall shelling out to read files.
    The codex model is a flag (default DEFAULT_CODEX_MODEL); an off-list model
    raises via resolve_codex_model.
    """
    if engine == "codex":
        # `codex exec` enables the native web_search tool via config, not a
        # `--search` flag (that is a top-level `codex` flag). Effort must stay
        # >= low (minimal cannot web-search: API 400).
        command = ["codex", "exec", "-c", "tools.web_search=true", "-m", llm.resolve_codex_model(model)]
        if effort is not None:
            command += ["-c", f'model_reasoning_effort="{effort}"']
        command.append(prompt)
        return command, None
    if engine == "claude":
        command = ["claude", "-p", "--allowed-tools", "WebSearch", "WebFetch", "Read"]
        if model is not None:
            command += ["--model", model]
        if effort is not None:
            command += ["--effort", effort]
        return command, prompt
    raise ValueError(f"unknown engine {engine!r}; expected 'codex' or 'claude'")


def _default_agent_runner(command: list[str], stdin_text: str | None) -> subprocess.CompletedProcess[str]:
    if stdin_text is None:
        return subprocess.run(
            command, text=True, capture_output=True, check=False,
            stdin=subprocess.DEVNULL, timeout=AGENT_TIMEOUT_SECONDS,
        )
    return subprocess.run(
        command, text=True, capture_output=True, check=False,
        input=stdin_text, timeout=AGENT_TIMEOUT_SECONDS,
    )


def _compose_prompt(source: ingest.SourceRecord, doc_text: str) -> str:
    base = FIND_DATE_PROMPT.read_text(encoding="utf-8").rstrip()
    inputs = {
        "firm": source.firm,
        "title": source.source,
        "url": source.url or source.resolved_url,
        "local_file_path": str(source.local_path) if source.local_path else None,
        "document_text": _clip_for_prompt(doc_text),
    }
    return (
        f"{base}\n\n## Machine-readable inputs\n"
        f"{json.dumps(inputs, ensure_ascii=False, indent=2, sort_keys=True)}\n"
    )


def find_date_for_source(
    source: ingest.SourceRecord,
    doc_text: str,
    *,
    engine: str,
    model: str | None,
    effort: str | None,
    runner: Runner | None = None,
) -> tuple[list[Candidate], str]:
    """One agent call. Returns (candidates, error). Any failure (non-zero exit,
    timeout, unparseable output) yields ([], message) so the sweep continues."""
    runner = runner or _default_agent_runner
    prompt = _compose_prompt(source, doc_text)
    command, stdin_text = _agent_command(engine, model, effort, prompt)
    try:
        completed = runner(command, stdin_text)
    except subprocess.TimeoutExpired:
        return [], f"{engine} timed out after {AGENT_TIMEOUT_SECONDS}s"
    except Exception as exc:  # noqa: BLE001 — subprocess/launch failure must not kill the sweep
        return [], f"{engine} launch failed: {exc}"
    if completed.returncode != 0:
        return [], (completed.stderr or "").strip()[:300] or f"{engine} exited non-zero"
    try:
        return parse_find_date_response(completed.stdout), ""
    except Exception as exc:  # noqa: BLE001 — bad JSON is logged, not fatal
        return [], f"unparseable agent output: {exc}"


def parse_find_date_response(raw_response: str) -> list[Candidate]:
    """Parse `{"candidates": [...]}` from the agent's (possibly narrated) output.

    Strict on the contract so bad output becomes a logged per-source failure, not
    a silently accepted junk date."""
    payload = _extract_candidates_object(raw_response)
    candidates_raw = payload.get("candidates")
    if not isinstance(candidates_raw, list):
        raise ValueError("response must include a candidates list")
    candidates: list[Candidate] = []
    for item in candidates_raw:
        if not isinstance(item, dict):
            raise ValueError("each candidate must be a JSON object")
        where = item.get("where")
        if where not in _WHERE_VALUES:
            raise ValueError(f"where must be one of {sorted(_WHERE_VALUES)}; got {where!r}")
        granularity = item.get("granularity")
        if granularity not in _GRANULARITY_VALUES:
            raise ValueError(
                f"granularity must be one of {sorted(_GRANULARITY_VALUES)}; got {granularity!r}"
            )
        date_verbatim = item.get("date_verbatim")
        evidence_quote = item.get("evidence_quote")
        locator = item.get("locator")
        if isinstance(locator, (int, float)) and not isinstance(locator, bool):
            locator = str(locator)
        for name, value in (("date_verbatim", date_verbatim), ("evidence_quote", evidence_quote), ("locator", locator)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"candidate {name} must be a non-empty string")
        candidates.append(
            Candidate(where, date_verbatim.strip(), locator.strip(), evidence_quote.strip(), granularity)
        )
    return candidates


def _extract_candidates_object(raw_response: str) -> dict:
    """Find the JSON object carrying `candidates`. Tries a clean parse first (the
    claude path returns just the final message), then scans for the last balanced
    `{...}` block (the codex --search path can precede the JSON with narration)."""
    try:
        payload = json.loads(llm._extract_json(raw_response))
        if isinstance(payload, dict) and "candidates" in payload:
            return payload
    except (json.JSONDecodeError, ValueError):
        pass
    for block in reversed(_balanced_json_objects(raw_response)):
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "candidates" in payload:
            return payload
    raise ValueError("no JSON object with a candidates list found in agent output")


def _balanced_json_objects(text: str) -> list[str]:
    """Every top-level balanced `{...}` substring, ignoring braces inside strings."""
    objects: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start : i + 1])
                start = None
    return objects


# --------------------------------------------------------------------------- #
# Deterministic verification (fail-closed)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class VerifiedCandidate:
    candidate: Candidate
    parsed: ParsedDate

    @property
    def date_str(self) -> str:
        return self.parsed.render()

    @property
    def synthetic_day(self) -> bool:
        return self.candidate.granularity == GRAN_MONTH_YEAR


@dataclass(frozen=True, slots=True)
class Discard:
    candidate: Candidate
    reason: str


PageFetcher = Callable[[str], str | None]


def verify_candidates(
    candidates: list[Candidate],
    source: ingest.SourceRecord,
    doc_text: str,
    fetch_page: PageFetcher,
) -> tuple[list[VerifiedCandidate], list[Discard]]:
    """Verify each agent claim; an unverifiable claim is discarded, never used.

    - stated_in_document: the evidence quote must re-appear in the freshly
      extracted document text (exact, then the normalized match from
      confidence.py). A quote only on an image-only page the text tiers cannot
      see fails closed.
    - landing_page: the locator page must be fetchable, the evidence quote must
      appear on it, AND the page must reference this document (links the source
      URL/PDF filename, or contains the title) — killing "grabbed a sibling
      outlook's date".
    - the date string is parsed here (never the agent's formatting) and the year
      must fall in the batch window.
    """
    verified: list[VerifiedCandidate] = []
    discards: list[Discard] = []
    for candidate in candidates:
        if candidate.granularity == GRAN_QUARTER:
            discards.append(Discard(candidate, "quarter/season partial never fills"))
            continue
        parsed = parse_verbatim_date(candidate.date_verbatim, candidate.granularity)
        if parsed is None:
            discards.append(
                Discard(candidate, f"unparseable or out-of-window date {candidate.date_verbatim!r}")
            )
            continue
        if candidate.where == WHERE_STATED:
            if not _quote_in_text(candidate.evidence_quote, doc_text):
                discards.append(
                    Discard(candidate, "evidence quote not found in document text (image-only page or not verbatim)")
                )
                continue
            verified.append(VerifiedCandidate(candidate, parsed))
        else:  # landing_page
            page = fetch_page(candidate.locator)
            if not page:
                discards.append(Discard(candidate, f"landing page not fetchable: {candidate.locator}"))
                continue
            if not _page_contains_quote(page, candidate.evidence_quote):
                discards.append(Discard(candidate, "evidence quote not found on landing page"))
                continue
            if not _page_references_document(page, source):
                discards.append(
                    Discard(candidate, "landing page does not reference this document (possible sibling page)")
                )
                continue
            verified.append(VerifiedCandidate(candidate, parsed))
    return verified, discards


def _quote_in_text(quote: str, text: str) -> bool:
    if not (quote or "").strip() or not text:
        return False
    if quote in text:
        return True
    normalized_quote = normalize_quote_text(quote)
    return bool(normalized_quote) and normalized_quote in normalize_quote_text(text)


def _page_contains_quote(html: str, quote: str) -> bool:
    text = trafilatura.extract(html) or ""
    if _quote_in_text(quote, text):
        return True
    stripped = re.sub(r"<[^>]+>", " ", html)
    return _quote_in_text(quote, stripped)


def _page_references_document(html: str, source: ingest.SourceRecord) -> bool:
    lowered = html.lower()
    for url in {source.url, source.resolved_url}:
        url = (url or "").strip().lower()
        if not url:
            continue
        if url in lowered:
            return True
        filename = url.rsplit("/", 1)[-1]
        if filename and len(filename) > 4 and filename in lowered:
            return True
    title = normalize_title(source.source)
    if title:
        page_text = normalize_title(re.sub(r"<[^>]+>", " ", html))
        if title in page_text:
            return True
    return False


# --------------------------------------------------------------------------- #
# Precedence
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Fill:
    date: str = ""
    date_from: str = ""
    synthetic_day: bool = False
    locator: str = ""
    evidence: str = ""


def choose_fill(
    verified: list[VerifiedCandidate],
    metadata_date: ParsedDate | None,
    metadata_from: str,
) -> Fill:
    """Apply the fill precedence (first hit wins):
    1. verified full date stated in the document
    2. deterministic metadata date
    3. verified full date from the landing page
    4. verified month-year partial (stated preferred over landing) → 01/MM/YYYY
    5. blank.
    """

    def pick(where: str, granularity: str) -> VerifiedCandidate | None:
        return next(
            (v for v in verified if v.candidate.where == where and v.candidate.granularity == granularity),
            None,
        )

    stated_full = pick(WHERE_STATED, GRAN_FULL)
    if stated_full is not None:
        return Fill(stated_full.date_str, FROM_STATED, False, stated_full.candidate.locator, stated_full.candidate.evidence_quote)

    if metadata_date is not None:
        return Fill(metadata_date.render(), metadata_from, False, "document metadata", "(deterministic PDF/HTML metadata date)")

    landing_full = pick(WHERE_LANDING, GRAN_FULL)
    if landing_full is not None:
        return Fill(landing_full.date_str, FROM_LANDING, False, landing_full.candidate.locator, landing_full.candidate.evidence_quote)

    partial = pick(WHERE_STATED, GRAN_MONTH_YEAR) or pick(WHERE_LANDING, GRAN_MONTH_YEAR)
    if partial is not None:
        return Fill(partial.date_str, FROM_PARTIAL, True, partial.candidate.locator, partial.candidate.evidence_quote)

    return Fill()


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


@dataclass
class SourceFill:
    source: ingest.SourceRecord
    fill: Fill
    discards: list[Discard] = field(default_factory=list)
    engine: str = ""
    agent_error: str = ""


@dataclass
class DatefillResult:
    fills: list[SourceFill]
    unmatched: list[tuple[str, str]]


class _Extractor:
    """Lazily extracts document text, caching page fetches and PDF downloads so a
    landing-page verification never re-fetches and a URL-PDF downloads once."""

    def __init__(self, tmp_dir: Path) -> None:
        self._tmp_dir = tmp_dir
        self._page_cache: dict[str, str | None] = {}
        self._pdf_cache: dict[str, Path | None] = {}

    def fetch_page(self, url: str) -> str | None:
        if url not in self._page_cache:
            try:
                result = ingest._fetch_html(url)
                self._page_cache[url] = result.html if isinstance(result, ingest.HtmlFetchResult) else result
            except Exception:  # noqa: BLE001 — an unfetchable page just yields no verification
                self._page_cache[url] = None
        return self._page_cache[url]

    def _download_pdf(self, url: str) -> Path | None:
        if url not in self._pdf_cache:
            try:
                self._pdf_cache[url] = ingest._download_pdf(url, self._tmp_dir)
            except Exception:  # noqa: BLE001
                self._pdf_cache[url] = None
        return self._pdf_cache[url]

    def extract(self, source: ingest.SourceRecord) -> ExtractedDoc:
        if source.source_type == "txt" and source.local_path:
            text = Path(source.local_path).read_text(encoding="utf-8", errors="replace")
            return ExtractedDoc(text, None, source.local_path, "txt")
        if source.source_type == "pdf":
            path = source.local_path or self._download_pdf(source.resolved_url)
            text = ""
            if path is not None:
                try:
                    text = ingest._extract_pdf_text(path)[0]
                except Exception:  # noqa: BLE001
                    text = ""
            return ExtractedDoc(text, None, path, "pdf")
        html = self.fetch_page(source.resolved_url) or ""
        text = (trafilatura.extract(html) or "") if html else ""
        return ExtractedDoc(text, html or None, None, "html")


def _compute_metadata(source: ingest.SourceRecord, doc: ExtractedDoc) -> tuple[ParsedDate | None, str]:
    if doc.source_type == "pdf" and doc.native_path is not None:
        parsed = pdf_metadata_date(doc.native_path)
        return (parsed, FROM_PDF_META) if parsed is not None else (None, "")
    if doc.source_type == "html" and doc.html:
        parsed = _parse_ddmmyyyy(ingest.extract_html_date(doc.html))
        return (parsed, FROM_HTML_META) if parsed is not None else (None, "")
    return (None, "")


def run_datefill(
    output_csv: Path,
    master_csv: Path,
    *,
    primary_engine: str = DEFAULT_PRIMARY_ENGINE,
    primary_model: str | None = DEFAULT_PRIMARY_MODEL,
    primary_effort: str | None = DEFAULT_PRIMARY_EFFORT,
    cascade_engine: str | None = DEFAULT_CASCADE_ENGINE,
    cascade_model: str | None = DEFAULT_CASCADE_MODEL,
    cascade_effort: str | None = DEFAULT_CASCADE_EFFORT,
    runner: Runner | None = None,
    use_llm: bool = True,
    limit: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> DatefillResult:
    """Load -> per-source extract, agent cascade, verify, metadata fallback,
    precedence. Writes nothing (see write_outputs / apply_patch)."""
    output_rows, _ = _read_output_rows(output_csv)
    master = ingest.load_pilot_sources(master_csv)
    sources, unmatched = collect_undated_sources(output_rows, master)
    if limit is not None:
        sources = sources[:limit]

    say = progress or (lambda _msg: None)
    fills: list[SourceFill] = []
    with tempfile.TemporaryDirectory(prefix="datefill-") as tmp:
        extractor = _Extractor(Path(tmp))
        for position, source in enumerate(sources, start=1):
            say(f"[{position}/{len(sources)}] {source.firm} — {source.source[:60]}")
            doc = extractor.extract(source)
            metadata_date, metadata_from = _compute_metadata(source, doc)

            verified: list[VerifiedCandidate] = []
            discards: list[Discard] = []
            engine_used = ""
            agent_error = ""
            if use_llm:
                candidates, agent_error = find_date_for_source(
                    source, doc.text, engine=primary_engine, model=primary_model,
                    effort=primary_effort, runner=runner,
                )
                verified_p, discards_p = verify_candidates(candidates, source, doc.text, extractor.fetch_page)
                verified += verified_p
                discards += discards_p
                engine_used = primary_engine

            fill = choose_fill(verified, metadata_date, metadata_from)

            # Cascade: a second engine only for sources still blank after the
            # primary sweep AND the deterministic metadata fallback.
            if use_llm and not fill.date and cascade_engine:
                candidates_c, error_c = find_date_for_source(
                    source, doc.text, engine=cascade_engine, model=cascade_model,
                    effort=cascade_effort, runner=runner,
                )
                verified_c, discards_c = verify_candidates(candidates_c, source, doc.text, extractor.fetch_page)
                verified += verified_c
                discards += discards_c
                engine_used = f"{primary_engine}+{cascade_engine}"
                if error_c and not agent_error:
                    agent_error = error_c
                fill = choose_fill(verified, metadata_date, metadata_from)

            fills.append(SourceFill(source, fill, discards, engine_used, agent_error))
    return DatefillResult(fills, unmatched)


# --------------------------------------------------------------------------- #
# Report outputs
# --------------------------------------------------------------------------- #


DATEFILL_COLUMNS = (
    "Firm", "Title", "URL", "Date", "date_from", "synthetic_day",
    "locator", "evidence_quote", "discarded", "engine", "agent_error",
)


def _datefill_row(source_fill: SourceFill) -> dict[str, str]:
    fill = source_fill.fill
    discarded = " || ".join(
        f"[{d.candidate.where}/{d.candidate.granularity}] {d.reason}" for d in source_fill.discards
    )
    return {
        "Firm": source_fill.source.firm,
        "Title": source_fill.source.source,
        "URL": source_fill.source.url,
        "Date": fill.date,
        "date_from": fill.date_from,
        "synthetic_day": "true" if fill.synthetic_day else "false",
        "locator": fill.locator,
        "evidence_quote": fill.evidence,
        "discarded": discarded,
        "engine": source_fill.engine,
        "agent_error": source_fill.agent_error,
    }


def write_outputs(result: DatefillResult, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "datefill.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DATEFILL_COLUMNS)
        writer.writeheader()
        writer.writerows(_datefill_row(sf) for sf in result.fills)
    summary_path = out_dir / "datefill-summary.md"
    summary_path.write_text(render_summary(result), encoding="utf-8")
    return {"datefill": csv_path, "summary": summary_path}


_TIER_LABELS = {
    FROM_STATED: "Stated in document (full date)",
    FROM_PDF_META: "PDF metadata (CreationDate)",
    FROM_HTML_META: "HTML metadata (htmldate)",
    FROM_LANDING: "Landing page (full date)",
    FROM_PARTIAL: "Month-year partial (synthetic day 01)",
}


def render_summary(result: DatefillResult) -> str:
    filled = [sf for sf in result.fills if sf.fill.date]
    blank = [sf for sf in result.fills if not sf.fill.date]
    tier_counts = Counter(sf.fill.date_from for sf in filled)

    lines: list[str] = []
    lines.append("# Date backfill summary")
    lines.append("")
    lines.append(
        "Post-run document-date backfill (`src/datefill.py`). The frozen run "
        "output was NOT modified. Review the `datefill.csv` patch, then run "
        "`--apply` to write a NEW file (a sibling such as `output.dated.csv`) — "
        "replace the frozen `output.csv` only after that review."
    )
    lines.append("")
    lines.append("## Totals")
    lines.append("")
    lines.append(f"- undated sources processed: {len(result.fills)}")
    lines.append(f"- filled: {len(filled)}")
    lines.append(f"- still blank: {len(blank)}")
    lines.append("")
    lines.append("## Filled by tier")
    lines.append("")
    if filled:
        for code, label in _TIER_LABELS.items():
            if tier_counts.get(code):
                lines.append(f"- {label}: {tier_counts[code]}")
    else:
        lines.append("_None filled._")
    lines.append("")
    lines.append("## Fills per firm")
    lines.append("")
    per_firm: dict[str, list[SourceFill]] = {}
    for sf in filled:
        per_firm.setdefault(sf.source.firm, []).append(sf)
    if per_firm:
        for firm in sorted(per_firm):
            entries = per_firm[firm]
            dates = ", ".join(f"{sf.fill.date} ({sf.fill.date_from})" for sf in entries)
            lines.append(f"- {firm}: {len(entries)} — {dates}")
    else:
        lines.append("_None._")
    lines.append("")
    lines.append("## Still blank")
    lines.append("")
    if blank:
        for sf in blank:
            reason = sf.agent_error or (
                f"{len(sf.discards)} candidate(s) discarded in verification"
                if sf.discards
                else "no dated candidate found and no usable metadata"
            )
            lines.append(f"- {sf.source.firm} — {sf.source.source[:70]}: {reason}")
    else:
        lines.append("_None — every processed source was filled._")
    lines.append("")
    lines.append("## Sources not matched to the master CSV (not processed)")
    lines.append("")
    if result.unmatched:
        for firm, title in result.unmatched:
            lines.append(f"- {firm} — {title[:70]} (fix the master firm/title to include it)")
    else:
        lines.append("_None._")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Apply — write a NEW output.csv with Date filled + grouped dates rebuilt
# --------------------------------------------------------------------------- #


def load_patch_fills(patch_csv: Path) -> dict[tuple[str, str], str]:
    """(normalized firm, title) -> filled date, for rows the patch actually
    filled (blank fills are excluded so they never overwrite anything)."""
    if not patch_csv.is_file():
        raise DatefillError(f"patch not found: {patch_csv}")
    fills: dict[tuple[str, str], str] = {}
    with patch_csv.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            date = (row.get("Date") or "").strip()
            if not date:
                continue
            key = (normalize_firm(row.get("Firm") or ""), normalize_title(row.get("Title") or ""))
            fills[key] = date
    return fills


def rebuild_date_cell(row: dict[str, str], fills: dict[tuple[str, str], str]) -> str:
    """Rebuild a row's `Date` from its member titles.

    Each member's date is its patch fill when present (undated sources we
    backfilled), else its aligned original date entry (decision 9: member dates
    are pipe-joined in Source order). Blanks are skipped, and exact duplicates
    are collapsed order-preserving — fixing the cosmetic `15/06/2026 | ×4`
    grouped-row repetition flagged in STATE.md.
    """
    firm = (row.get("Firm") or "").strip()
    titles = _split_pipe(row.get("Source") or "")
    date_entries = _split_pipe(row.get("Date") or "")
    member_dates: list[str] = []
    for position, title in enumerate(titles):
        key = (normalize_firm(firm), normalize_title(title))
        if key in fills:
            member_dates.append(fills[key])
        elif position < len(date_entries):
            member_dates.append(date_entries[position])
        else:
            member_dates.append("")
    seen: set[str] = set()
    distinct: list[str] = []
    for date in member_dates:
        if date and date not in seen:
            seen.add(date)
            distinct.append(date)
    return " | ".join(distinct)


def apply_patch(output_csv: Path, patch_csv: Path, write_path: Path) -> int:
    """Write a NEW output.csv with `Date` filled from the patch and grouped date
    cells rebuilt/deduped. The column set is unchanged (provenance stays in the
    patch). Returns the number of rows whose Date changed."""
    rows, fieldnames = _read_output_rows(output_csv)
    fills = load_patch_fills(patch_csv)
    changed = 0
    for row in rows:
        rebuilt = rebuild_date_cell(row, fills)
        if rebuilt != (row.get("Date") or "").strip():
            changed += 1
        row["Date"] = rebuilt
    write_path.parent.mkdir(parents=True, exist_ok=True)
    with write_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return changed


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _resolve_model(engine: str, model: str | None, *, claude_default: str | None) -> str | None:
    """claude needs an explicit model (supply a default). codex passes the model
    through unchanged — None lets the adapter fill DEFAULT_CODEX_MODEL, a named
    model is validated downstream (never silently dropped)."""
    if engine == "claude" and model is None:
        return claude_default
    return model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.datefill",
        description="Post-run document-date backfill (report/patch + separate --apply).",
    )
    parser.add_argument("--output", required=True, type=Path, help="frozen run output.csv (read-only)")
    parser.add_argument("--apply", action="store_true", help="apply mode: write a new output.csv from a patch")

    # report/patch mode
    parser.add_argument("--sources", type=Path, help="master source CSV (report mode)")
    parser.add_argument("--out-dir", type=Path, help="directory for datefill.csv + summary (report mode)")
    parser.add_argument("--engine", default=DEFAULT_PRIMARY_ENGINE, help=f"primary engine (default: {DEFAULT_PRIMARY_ENGINE})")
    parser.add_argument("--model", default=DEFAULT_PRIMARY_MODEL, help=f"primary model (codex allowlist member, default {DEFAULT_PRIMARY_MODEL})")
    parser.add_argument("--effort", default=DEFAULT_PRIMARY_EFFORT, help=f"primary effort (default: {DEFAULT_PRIMARY_EFFORT})")
    parser.add_argument("--cascade-engine", default=DEFAULT_CASCADE_ENGINE, help=f"cascade engine (default: {DEFAULT_CASCADE_ENGINE})")
    parser.add_argument("--cascade-model", default=DEFAULT_CASCADE_MODEL, help=f"cascade model (default: {DEFAULT_CASCADE_MODEL})")
    parser.add_argument("--cascade-effort", default=DEFAULT_CASCADE_EFFORT, help=f"cascade effort (default: {DEFAULT_CASCADE_EFFORT})")
    parser.add_argument("--no-cascade", action="store_true", help="run the primary engine only")
    parser.add_argument("--no-llm", action="store_true", help="metadata + precedence only; no agent calls")
    parser.add_argument("--limit", type=int, default=None, help="process only the first N undated sources (smoke)")

    # apply mode
    parser.add_argument("--patch", type=Path, help="datefill.csv patch (apply mode)")
    parser.add_argument("--write", type=Path, help="destination for the new output.csv (apply mode)")

    args = parser.parse_args(argv)

    if args.apply:
        if not args.patch or not args.write:
            parser.error("--apply requires --patch and --write")
        changed = apply_patch(args.output, args.patch, args.write)
        print(f"applied: {changed} rows' Date changed -> {args.write}")
        return 0

    if not args.sources or not args.out_dir:
        parser.error("report mode requires --sources and --out-dir")

    primary_model = _resolve_model(args.engine, args.model, claude_default=DEFAULT_CASCADE_MODEL)
    cascade_engine = None if args.no_cascade else args.cascade_engine
    cascade_model = _resolve_model(args.cascade_engine, args.cascade_model, claude_default=DEFAULT_CASCADE_MODEL)

    result = run_datefill(
        args.output,
        args.sources,
        primary_engine=args.engine,
        primary_model=primary_model,
        primary_effort=args.effort,
        cascade_engine=cascade_engine,
        cascade_model=cascade_model,
        cascade_effort=args.cascade_effort,
        use_llm=not args.no_llm,
        limit=args.limit,
        progress=lambda message: print(message, flush=True),
    )
    written = write_outputs(result, args.out_dir)

    filled = sum(1 for sf in result.fills if sf.fill.date)
    print(
        f"datefill: {len(result.fills)} undated sources, {filled} filled, "
        f"{len(result.fills) - filled} still blank, {len(result.unmatched)} unmatched-to-master"
    )
    for label, path in written.items():
        print(f"  {label}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
