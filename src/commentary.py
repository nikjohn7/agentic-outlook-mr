"""Structured commentary: the four reviewer-facing columns and their rendering.

Client feedback on the July deliverable (2026-10): `Full Commentary` was one
long blob (reasoning + quote + locator + every internal note, repeated per
source for merged rows). The client asked for it split into the verbatim
evidence, the locator, and the second-reader notes. This module owns that split
so every stage that writes or merges a row works on the SAME four fields:

- `Reasoning`         - why the view was read that way (one segment per source)
- `Verbatim Evidence` - the exact supporting text, nothing else
- `Locator`           - page (PDF) or blank (HTML / transcript: the quote is
                        the locator, client decision 10)
- `Reviewer Notes`    - second-reader, arbiter, evidence-check and reconcile
                        notes; everything that is about the call rather than
                        from the document

A row carries one or more *segments* (one per supporting source passage);
merged rows keep every segment, labeled by source title, joined by
`SEGMENT_SEP` in each column. `Full Commentary` is still written for internal
consumers (reconcile scope gate, digests, eval) and is RENDERED from these
fields, never edited on its own. `parse_full_commentary` recovers the fields
from a legacy row (the July master) so an old file can be exported in the new
shape without re-running any model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

# The segment separator for merged rows. Defined here, re-exported by
# src.assemble as COMMENTARY_MERGE_SEP so the two never drift.
SEGMENT_SEP = "  ||||  "
# Appended to the locator of a passage read from a Markdown source's paired
# PDF (a `pdf:` chunk). It records where the evidence was read; it does not
# mean the Markdown lacked it.
PDF_READ_MARKER = "(read from PDF)"
# How a separator is recognised when a row is read back: the CSV writer
# collapses space runs (src.csvio.clean_cell), so on disk it is " |||| ".
_SEGMENT_SPLIT = re.compile(r"\s+\|\|\|\|\s+")

STRUCTURED_COLUMNS = ("Reasoning", "Verbatim Evidence", "Locator", "Reviewer Notes")

# Locators of the form `char:<start>-<end>` are chunk offsets into an HTML or
# transcript snapshot: meaningless to a reader. Client decision 10: HTML/web
# sources are located by exact phrase only, so these render blank.
_CHAR_LOCATOR = re.compile(r"^\s*char:\d+-\d+\s*(?:[—–-]\s*)?(?P<rest>.*)$", re.IGNORECASE)


def display_locator(locator: str) -> str:
    """The locator as a reader should see it: page references pass through;
    `char:` offsets (HTML / transcript chunks) are dropped, keeping any
    figure/table name that followed them (`char:0-8000 - Figure 1: …` →
    `Figure 1: …`)."""
    text = (locator or "").strip()
    match = _CHAR_LOCATOR.match(text)
    return match.group("rest").strip() if match else text


@dataclass(frozen=True, slots=True)
class Segment:
    """One supporting passage: its source title (for labeling in merged rows),
    the reader-facing locator, the model's reasoning and the verbatim quote."""

    title: str
    locator: str
    reasoning: str
    evidence: str

    def body(self) -> str:
        """`{reasoning} Evidence: {quote}.` - the legacy body format, kept so the
        rendered Full Commentary stays parseable by every existing consumer."""
        return f"{self.reasoning} Evidence: {self.evidence}."


@dataclass(frozen=True, slots=True)
class Commentary:
    segments: tuple[Segment, ...]
    notes: str = ""

    def with_note(self, note: str) -> "Commentary":
        note = (note or "").strip()
        if not note:
            return self
        return replace(self, notes=f"{self.notes} {note}".strip() if self.notes else note)


def _label(segment: Segment) -> str:
    return f"{segment.title} ({segment.locator})" if segment.locator else segment.title


def render_full_commentary(commentary: Commentary) -> str:
    """The legacy single-string form, unchanged in format: one segment renders
    as `reasoning Evidence: quote. Locator: X.`; several render as labeled
    segments joined by SEGMENT_SEP (the kept/primary one first); notes follow."""
    segments = commentary.segments
    if not segments:
        return commentary.notes
    if len(segments) == 1:
        seg = segments[0]
        text = seg.body()
        if seg.locator:
            text += f" Locator: {seg.locator}."
    else:
        text = SEGMENT_SEP.join(f"{_label(seg)}: {seg.body()}" for seg in segments)
    if commentary.notes:
        text += f" {commentary.notes}"
    return text


def structured_columns(commentary: Commentary) -> dict[str, str]:
    """The four reviewer-facing columns. A single segment is written bare; a
    merged row prefixes each segment with its source title so a reader can
    tell which document said what, in every column."""
    segments = commentary.segments
    if len(segments) <= 1:
        seg = segments[0] if segments else Segment("", "", "", "")
        return {
            "Reasoning": seg.reasoning,
            "Verbatim Evidence": seg.evidence,
            "Locator": seg.locator,
            "Reviewer Notes": commentary.notes,
        }
    # A passage with no locator contributes nothing to the Locator cell (no
    # placeholder). Alignment with the other columns is recovered on re-read
    # from Full Commentary, whose segment labels carry every locator.
    return {
        "Reasoning": SEGMENT_SEP.join(f"{seg.title}: {seg.reasoning}" for seg in segments),
        "Verbatim Evidence": SEGMENT_SEP.join(f"{seg.title}: {seg.evidence}" for seg in segments),
        "Locator": SEGMENT_SEP.join(f"{seg.title}: {seg.locator}" for seg in segments if seg.locator),
        "Reviewer Notes": commentary.notes,
    }


def evidence_origin(commentary: Commentary) -> str:
    """Where a row's retained passages were read: `pdf` when every segment's
    locator carries PDF_READ_MARKER, `both` when only some do, `markdown`
    otherwise (also any source with no paired PDF). Derived, never stored on
    its own, so it follows every merge."""
    marked = [PDF_READ_MARKER in segment.locator for segment in commentary.segments]
    if marked and all(marked):
        return "pdf"
    return "both" if any(marked) else "markdown"


def row_columns(commentary: Commentary) -> dict[str, str]:
    """Full Commentary plus the four structured columns and the internal
    `evidence_origin`, from one source of truth."""
    columns = structured_columns(commentary)
    columns["Full Commentary"] = render_full_commentary(commentary)
    columns["evidence_origin"] = evidence_origin(commentary)
    return columns


def append_note(row: dict[str, str], note: str) -> None:
    """Append a reviewer note to an output row in place, keeping Reviewer Notes
    and the rendered Full Commentary in step. Works for rows that predate the
    structured columns (the note lands in Full Commentary only)."""
    note = (note or "").strip()
    if not note:
        return
    if "Reviewer Notes" in row:
        existing = (row.get("Reviewer Notes") or "").strip()
        row["Reviewer Notes"] = f"{existing} {note}".strip() if existing else note
    commentary = (row.get("Full Commentary") or "").strip()
    row["Full Commentary"] = f"{commentary} {note}".strip() if commentary else note


# --------------------------------------------------------------------------- #
# Merging (same-view dedup in assemble, firm-reconcile merges)
# --------------------------------------------------------------------------- #


def merge_commentaries(parts: list[Commentary]) -> Commentary:
    """Concatenate segments in the order given (primary first); notes are
    joined, deduplicated, in the same order."""
    segments: list[Segment] = []
    notes: list[str] = []
    for part in parts:
        segments.extend(part.segments)
        if part.notes and part.notes not in notes:
            notes.append(part.notes)
    return Commentary(tuple(segments), " ".join(notes))


# --------------------------------------------------------------------------- #
# Parsing legacy rows (Full Commentary only)
# --------------------------------------------------------------------------- #

# Every note the pipeline appends after a segment starts with one of these.
# Anything after the first marker is a reviewer note, never evidence.
_NOTE_MARKERS = (
    "Evidence check:",
    "Basis: inferred",
    "Basis: forecast_delta",
    "Checker:",
    "Arbiter:",
    "Sibling consistency:",
    "Implied-call challenge",
    "Call language:",
    "Shared evidence:",
    "Also held by this firm:",
    "Reconcile:",
    "Page check:",
)
_NOTE_SPLIT = re.compile(
    r"(?:(?<=\.)|(?<=\.\)))\s+(?=(?:" + "|".join(re.escape(m) for m in _NOTE_MARKERS) + "))"
)
_EVIDENCE_MARK = " Evidence: "
_LOCATOR_MARK = " Locator: "


def _split_notes(text: str) -> tuple[str, str]:
    """(body, notes): the body ends where the first note marker begins."""
    parts = _NOTE_SPLIT.split(text, maxsplit=1)
    if len(parts) == 2:
        return parts[0].rstrip(), parts[1].strip()
    return text.rstrip(), ""


def _strip_period(text: str) -> str:
    text = text.strip()
    return text[:-1].rstrip() if text.endswith(".") else text


def _strip_title_paren(locator: str, titles: list[str]) -> str:
    """`p.3 (Source Title)` - grouped rows suffix the member title; drop it,
    keeping a trailing PDF_READ_MARKER."""
    marker = ""
    if locator.endswith(PDF_READ_MARKER):
        locator, marker = locator[: -len(PDF_READ_MARKER)].rstrip(), f" {PDF_READ_MARKER}"
    for title in titles:
        suffix = f"({title})"
        if title and locator.endswith(suffix):
            return locator[: -len(suffix)].rstrip() + marker
    return locator + marker


def _take_label(segment: str, titles: list[str]) -> tuple[str, str, str]:
    """Split a merged segment's `Title (locator): body` label off, matching
    against the row's own source titles (longest first) so titles containing
    `: ` cannot confuse the split. Returns (title, locator, body); title and
    locator are blank when there is no label."""
    for title in sorted((t for t in titles if t), key=len, reverse=True):
        if not segment.startswith(title):
            continue
        rest = segment[len(title):]
        match = re.match(r"^\s*(?:\((?P<loc>.*?)\))?:\s", rest)
        if match:
            return title, (match.group("loc") or "").strip(), rest[match.end():]
    return "", "", segment


def _parse_segment(text: str, titles: list[str], fallback_title: str) -> tuple[Segment, str]:
    """One segment → (Segment, trailing notes). A segment without the
    `Evidence:` marker is treated as reasoning only."""
    title, label_locator, body = _take_label(text, titles)
    body, notes = _split_notes(body)
    locator = label_locator
    if _EVIDENCE_MARK in body:
        reasoning, rest = body.split(_EVIDENCE_MARK, 1)
        if _LOCATOR_MARK in rest:
            evidence, loc = rest.rsplit(_LOCATOR_MARK, 1)
            locator = locator or _strip_title_paren(_strip_period(loc), titles)
        else:
            evidence = rest
        evidence = _strip_period(evidence)
    else:
        reasoning, evidence = body, ""
    return (
        Segment(
            title=title or fallback_title,
            locator=display_locator(locator),
            reasoning=reasoning.strip(),
            evidence=evidence.strip(),
        ),
        notes,
    )


def parse_full_commentary(text: str, source_cell: str = "") -> Commentary:
    """Recover the structured fields from a legacy `Full Commentary` string.

    `source_cell` is the row's `Source` column (pipe-joined titles for merged
    rows); it lets segment labels be split off exactly. Notes found after any
    segment are collected into one Reviewer Notes string, in order."""
    titles = [t.strip() for t in (source_cell or "").split("|") if t.strip()]
    fallback = titles[0] if titles else ""
    segments: list[Segment] = []
    notes: list[str] = []
    for raw in _SEGMENT_SPLIT.split(text or ""):
        raw = raw.strip()
        if not raw:
            continue
        segment, trailing = _parse_segment(raw, titles, fallback)
        segments.append(segment)
        if trailing and trailing not in notes:
            notes.append(trailing)
    return Commentary(tuple(segments), " ".join(notes))


def commentary_from_row(row: dict[str, str]) -> Commentary:
    """A row's Commentary: from the structured columns when present and
    non-empty, otherwise parsed from Full Commentary (legacy rows)."""
    if (row.get("Verbatim Evidence") or "").strip() or (row.get("Reasoning") or "").strip():
        titles = [t.strip() for t in (row.get("Source") or "").split("|") if t.strip()]
        return _commentary_from_structured(row, titles)
    return parse_full_commentary(row.get("Full Commentary", ""), row.get("Source", ""))


def _split_labeled(cell: str, titles: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for part in _SEGMENT_SPLIT.split(cell or ""):
        title, _, body = _take_label(part, titles) if titles else ("", "", part)
        if not title:
            # Structured cells label as `Title: value` (no locator parenthetical).
            for candidate in sorted((t for t in titles if t), key=len, reverse=True):
                if part.startswith(candidate + ": "):
                    title, body = candidate, part[len(candidate) + 2:]
                    break
        out.append((title, body.strip()))
    return out


def _align_locators_by_title(
    locators: list[tuple[str, str]],
    reasonings: list[tuple[str, str]],
    evidences: list[tuple[str, str]],
    count: int,
) -> list[tuple[str, str]]:
    """Place a short Locator cell's entries by title when no Full Commentary
    can align them (client files have none). A locator goes to the one passage
    with its title; when a title labels several passages, or a locator has no
    title, the locators stay blank rather than land on the wrong passage."""
    seg_titles = [
        (reasonings[i][0] if i < len(reasonings) else "")
        or (evidences[i][0] if i < len(evidences) else "")
        for i in range(count)
    ]
    aligned = [("", "")] * count
    for title, locator in locators:
        matches = [i for i, seg_title in enumerate(seg_titles) if title and seg_title == title]
        if len(matches) != 1:
            return [("", "")] * count
        aligned[matches[0]] = (title, locator)
    return aligned


def _commentary_from_structured(row: dict[str, str], titles: list[str]) -> Commentary:
    reasonings = _split_labeled(row.get("Reasoning", ""), titles)
    evidences = _split_labeled(row.get("Verbatim Evidence", ""), titles)
    locators = _split_labeled(row.get("Locator", ""), titles)
    count = max(len(reasonings), len(evidences), 1)
    if count > 1 and len(locators) != count:
        # Pageless passages are omitted from the Locator cell, so the cell
        # cannot be aligned by position; the Full Commentary labels can.
        rendered = parse_full_commentary(row.get("Full Commentary", ""), row.get("Source", ""))
        if len(rendered.segments) == count:
            locators = [(seg.title, seg.locator) for seg in rendered.segments]
        else:
            locators = _align_locators_by_title(locators, reasonings, evidences, count)
    segments: list[Segment] = []
    for index in range(count):
        title_r, reasoning = reasonings[index] if index < len(reasonings) else ("", "")
        title_e, evidence = evidences[index] if index < len(evidences) else ("", "")
        _, locator = locators[index] if index < len(locators) else ("", "")
        locator = "" if locator == "-" else locator  # legacy placeholder
        segments.append(
            Segment(
                title=title_r or title_e or (titles[0] if titles else ""),
                locator=display_locator(locator),
                reasoning=reasoning,
                evidence=evidence,
            )
        )
    return Commentary(tuple(segments), (row.get("Reviewer Notes") or "").strip())
