"""Deterministic evidence checks and confidence scoring."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from src.schemas import CandidateCall, CheckVerdict
from src.taxonomy import Taxonomy


HARD_FAILURE_TAXONOMY = "taxonomy_no_match"
HARD_FAILURE_QUOTE = "quote_not_found"
HARD_FAILURE_VISUAL_LOCATOR = "visual_locator_missing"
HARD_FAILURE_EVIDENCE = "evidence_check_failed"
HARD_FAILURE_MATERIALITY = "delta_below_materiality"

# Materiality floor for forecast-delta evidence (a house forecast endpoint vs.
# the current level). PROVISIONAL, pending client confirmation — this maps to
# open client question 1 in runs/pilot-05/gt-comparison.md ("is a forecast
# delta a view at all, and if so what floor?"). Below the floor a forecast_delta
# candidate hard-fails to `delta_below_materiality` (reviewable and reversible —
# never silently dropped, and never converted to `N`: an immaterial move is not
# evidence of neutrality). At/above the floor it may proceed but is capped below
# High and flagged, because "delta-as-view" itself is not yet confirmed.
MATERIALITY_FLOOR_BP = 25
MATERIALITY_FLOOR_PCT = 2.0
FORECAST_DELTA_CAP = 74

# An analyst-style inference (basis: inferred) reads a positioning consequence
# out of macro/thematic prose that never states a position. It is encouraged but
# segregated one full band below stated calls: capped into the middle (Medium)
# band and always flagged for review. Stated calls reach High (>=75); this cap
# lands an inference at 74 = the top of Medium, one band down.
INFERRED_CAP = 74

# A checker `fail` verdict is fatal: the second reader found the evidence does
# not mean what the call claims. Reason codes are per failed question.
CHECKER_FAIL_REASONS = {
    "supports_view": "checker_sign_mismatch",
    "forward_looking": "checker_not_forward_looking",
    "asset_match": "checker_asset_mismatch",
}

# Without full checker confirmation a call cannot reach the High band: the
# rubric arithmetic is unchanged (scores stay comparable across runs), but the
# score is capped just under the High threshold, which also forces the
# review flag. "High" therefore means "a second model confirmed the evidence
# supports the call".
CHECKER_UNCONFIRMED_CAP = 74

# Read-quality floors: a PDF below MIN_PDF_CHARS_PER_PAGE is likely scanned or
# image-only (its text layer — the quote-check corpus — is unreliable); an HTML
# snapshot below MIN_HTML_SNAPSHOT_CHARS is likely bot-blocked or paywalled.
MIN_PDF_CHARS_PER_PAGE = 200
MIN_HTML_SNAPSHOT_CHARS = 1000

# Multi-span prose evidence (an honest elision joining two real passages) is
# gated deterministically: each span is verified verbatim on its own, but the
# join is bounded so it cannot smuggle a paraphrase or a reversed stitch past
# the check. A lone span keeps the original single-quote contract (no floor).
MAX_PROSE_SPANS = 3
MIN_SPAN_MEANINGFUL_TOKENS = 4

# When the cited page is flagged scrambled (its text layer is column-
# interleaved — see src/ingest.detect_scrambled_page), a correct contiguous
# prose quote of the RENDERED page cannot survive the verbatim check against
# the scrambled snapshot. On those pages only, prose falls back to the same
# key-token overlap used for table/visual evidence. That is a weaker guarantee
# (word order is no longer verified), so the score is capped just below the
# High band and the row is flagged for review, and the degradation is recorded.
SCRAMBLED_PROSE_CAP = 74


@dataclass(frozen=True, slots=True)
class EvidenceCheck:
    passed: bool
    reason_code: str = ""
    message: str = ""
    # True when the verbatim prose guarantee was relaxed to key-token overlap
    # because the cited page is scrambled. Both a degraded pass and a degraded
    # failure carry it, so the outcome is always visible to analysts.
    degraded: bool = False


class EvidenceFailure(ValueError):
    """A failed evidence check, carrying the human-readable message (not just
    the reason code) so failure rows can explain what happened — e.g. that a
    degraded key-token fallback on a scrambled page also came up empty."""

    def __init__(self, check: EvidenceCheck) -> None:
        super().__init__(check.reason_code)
        self.reason_code = check.reason_code
        self.message = check.message
        self.degraded = check.degraded


class MaterialityFailure(ValueError):
    """A forecast_delta candidate whose move is below the materiality floor.
    Carries the human-readable message (like EvidenceFailure) so the failure row
    states the delta and floor that gated it."""

    def __init__(self, message: str) -> None:
        super().__init__(HARD_FAILURE_MATERIALITY)
        self.reason_code = HARD_FAILURE_MATERIALITY
        self.message = message


@dataclass(frozen=True, slots=True)
class ConfidenceResult:
    candidate: CandidateCall
    confidence: int
    band: str
    review_flag: str
    evidence_check: EvidenceCheck
    # "off" = checker not in play; "confirmed" = all verdicts pass;
    # "unclear" = at least one verdict unclear; "missing" = checker ran for
    # the run but produced no verdict for this candidate (call failed/skipped).
    checker_status: str = "off"
    checker_note: str = ""
    # A basis-driven confidence cap (forecast_delta or inferred), recorded so the
    # output row can explain why the call is held below High. Empty for stated
    # calls.
    cap_reason: str = ""


# Dash-like characters (typographic hyphens, en/em/figure dash, horizontal bar,
# minus sign) are folded to a plain hyphen so line-break joining and intra-word
# removal treat them uniformly.
_DASH_VARIANTS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"


def normalize_quote_text(value: str) -> str:
    """Normalize source and quote text before deterministic quote matching.

    Folds typographic and PDF-extraction seams SYMMETRICALLY on both quote and
    snapshot so they cannot decide a match: curly quotes, dash variants, the
    soft/line-break hyphen, and intra-word hyphens (a hyphen at a line break is
    joined into the word, so a hyphen's presence in the snapshot is unreliable;
    "AI-related" and "AIrelated" must compare equal in either direction). Exotic
    spaces (NBSP etc.) are already handled by NFKC and the whitespace collapse
    below, so they are not repeated here. Word content and order are untouched,
    so stitched, reordered, or paraphrased quotes still fail.
    """
    text = unicodedata.normalize("NFKC", value)
    text = text.translate(
        str.maketrans(
            {
                "\u2018": "'",
                "\u2019": "'",
                "\u201c": '"',
                "\u201d": '"',
                "\u00ad": None,  # soft hyphen: a discretionary, invisible break
            }
        )
    )
    text = re.sub(f"[{_DASH_VARIANTS}]", "-", text)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)  # hyphen consumed by a line break
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?<=\w)-(?=\w)", "", text)  # intra-word hyphens are unreliable in extractions
    return text.strip()


def snapshot_read_quality(snapshot_text: str, *, page_count: int | None = None) -> bool:
    """Deterministic read-quality signal for the rubric's 10-point row.

    page_count identifies a PDF snapshot (judged per page); None means HTML
    (judged on total extracted length).
    """
    chars = len(snapshot_text.strip())
    if page_count is not None:
        return page_count > 0 and chars / page_count >= MIN_PDF_CHARS_PER_PAGE
    return chars >= MIN_HTML_SNAPSHOT_CHARS


def evidence_passes(
    candidate: CandidateCall,
    snapshot_text: str,
    *,
    scrambled_pages: frozenset[int] = frozenset(),
) -> EvidenceCheck:
    """Validate evidence according to its kind without using model judgment.

    scrambled_pages is the source's set of column-interleaved page numbers; a
    prose call citing one of them falls back to the key-token overlap check.
    """
    if not snapshot_text or not snapshot_text.strip():
        return EvidenceCheck(False, HARD_FAILURE_EVIDENCE, "snapshot text is empty")
    if candidate.evidence_kind == "prose":
        cited_page = _cited_page(candidate.locator)
        if cited_page is not None and cited_page in scrambled_pages:
            return _degraded_prose_evidence_passes(candidate, snapshot_text, cited_page)
        return _prose_evidence_passes(candidate, snapshot_text)
    return _table_or_visual_evidence_passes(candidate, snapshot_text)


def score_candidate(
    candidate: CandidateCall,
    *,
    taxonomy: Taxonomy,
    snapshot_text: str,
    page_count: int | None = None,
    scrambled_pages: frozenset[int] = frozenset(),
    verdict: CheckVerdict | None = None,
    checker_enabled: bool = False,
) -> ConfidenceResult:
    """Return a deterministic score, or raise ValueError for hard failures.

    page_count is the source's PDF page count (None for HTML); it feeds the
    read-quality signal. scrambled_pages are the source's column-interleaved
    pages; a prose call citing one uses the degraded key-token check and is
    capped below High. When checker_enabled, verdict is the second reader's
    categorical answers: any `fail` is a hard failure, and anything short of
    all-pass caps the score below the High band.
    """
    if candidate.taxonomy_match == "none" or not taxonomy.is_valid_label(candidate.sub_asset_class):
        raise ValueError(HARD_FAILURE_TAXONOMY)

    evidence_check = evidence_passes(candidate, snapshot_text, scrambled_pages=scrambled_pages)
    if not evidence_check.passed:
        raise EvidenceFailure(evidence_check)

    # Materiality gate for forecast-delta evidence: an immaterial move is not a
    # view. Below the floor is a hard failure (reviewable, reversible).
    if candidate.basis == "forecast_delta":
        floor, magnitude = _materiality_floor_and_magnitude(candidate)
        if magnitude < floor:
            raise MaterialityFailure(
                f"forecast delta {_fmt(magnitude)}{candidate.delta_unit} is below the "
                f"{_fmt(floor)}{candidate.delta_unit} materiality floor "
                "(provisional, pending client confirmation)"
            )

    if checker_enabled and verdict is not None:
        failed = verdict.failed_questions()
        if failed:
            raise ValueError(CHECKER_FAIL_REASONS[failed[0]])

    score = 0
    score += {"explicit": 30, "implied": 15, "none": 0}[candidate.call_language]
    score += 25
    score += {"exact": 20, "semantic": 10}[candidate.taxonomy_match]
    score += 5 if candidate.conflict else 15
    score += 10 if snapshot_read_quality(snapshot_text, page_count=page_count) else 0

    # The verbatim guarantee was weakened to key-token overlap: cap below High.
    if evidence_check.degraded:
        score = min(score, SCRAMBLED_PROSE_CAP)

    # Basis-driven caps: a forecast delta at/above the floor is still only a
    # provisional view, and an analyst inference is segregated one band below
    # stated calls. Both are held below High and forced to review.
    cap_reason = ""
    if candidate.basis == "forecast_delta":
        score = min(score, FORECAST_DELTA_CAP)
        cap_reason = (
            "Basis: forecast_delta — a house forecast endpoint "
            f"({_fmt(abs(candidate.delta_value or 0.0))}{candidate.delta_unit} move) is treated as a "
            "provisional view (delta-as-view pending client confirmation); "
            "confidence capped below High and review required."
        )
    elif candidate.basis == "inferred":
        score = min(score, INFERRED_CAP)
        cap_reason = (
            "Basis: inferred — single-step analyst inference from macro/thematic "
            "prose; segregated one band below stated calls, review required."
        )

    checker_status = "off"
    checker_note = ""
    if checker_enabled:
        if verdict is None:
            checker_status = "missing"
            checker_note = "no checker verdict for this candidate"
        elif verdict.all_pass:
            checker_status = "confirmed"
        else:
            checker_status = "unclear"
            checker_note = verdict.note
        if checker_status != "confirmed":
            score = min(score, CHECKER_UNCONFIRMED_CAP)

    band = score_band(score)
    review_flag = review_flag_for(score, candidate)
    if (evidence_check.degraded or cap_reason) and review_flag == "none":
        review_flag = "review"
    return ConfidenceResult(
        candidate=candidate,
        confidence=score,
        band=band,
        review_flag=review_flag,
        evidence_check=evidence_check,
        checker_status=checker_status,
        checker_note=checker_note,
        cap_reason=cap_reason,
    )


def _materiality_floor_and_magnitude(candidate: CandidateCall) -> tuple[float, float]:
    """Return the (floor, magnitude) pair for a forecast_delta candidate. The
    magnitude is taken as an absolute value so the gate is sign-agnostic (a move
    of a given size is material regardless of direction)."""
    floor = MATERIALITY_FLOOR_BP if candidate.delta_unit == "bp" else MATERIALITY_FLOOR_PCT
    magnitude = abs(candidate.delta_value) if candidate.delta_value is not None else 0.0
    return float(floor), magnitude


def _fmt(value: float) -> str:
    """Format a delta/floor without a trailing ``.0`` for whole numbers."""
    return f"{value:g}"


def score_band(score: int) -> str:
    if score >= 75:
        return "High"
    if score >= 50:
        return "Medium"
    return "Low"


def review_flag_for(score: int, candidate: CandidateCall) -> str:
    if score < 50:
        return "strong_review"
    if score < 75 or candidate.conflict or candidate.view == "UNCERTAIN":
        return "review"
    return "none"


def _prose_evidence_passes(candidate: CandidateCall, snapshot_text: str) -> EvidenceCheck:
    """Every span must match the snapshot verbatim (after normalize_quote_text),
    and the spans must appear in document order. A single span is the original
    contiguous-quote contract; multiple spans (an honest elision) additionally
    obey the join guardrails so a paraphrase or reversed stitch cannot slip by.
    """
    spans = [
        normalized
        for span in candidate.evidence_spans
        if (normalized := normalize_quote_text(span))
    ]
    if not spans:
        return EvidenceCheck(False, HARD_FAILURE_QUOTE, "prose quote is empty")
    if len(spans) > MAX_PROSE_SPANS:
        return EvidenceCheck(
            False, HARD_FAILURE_QUOTE, f"prose quote has more than {MAX_PROSE_SPANS} spans"
        )
    if len(spans) > 1:
        for span in candidate.evidence_spans:
            if len(_meaningful_tokens(span)) < MIN_SPAN_MEANINGFUL_TOKENS:
                return EvidenceCheck(
                    False, HARD_FAILURE_QUOTE, "a prose quote span is too short to verify"
                )

    source = normalize_quote_text(snapshot_text)
    cursor = 0
    for span in spans:
        index = source.find(span, cursor)
        if index == -1:
            # Present but only before the cursor => the spans are stitched out of
            # document order; genuinely absent => a paraphrase or fabrication.
            if span in source:
                return EvidenceCheck(
                    False, HARD_FAILURE_QUOTE, "prose quote spans are out of document order"
                )
            return EvidenceCheck(False, HARD_FAILURE_QUOTE, "prose quote was not found")
        cursor = index + len(span)
    return EvidenceCheck(True)


def _degraded_prose_evidence_passes(
    candidate: CandidateCall,
    snapshot_text: str,
    cited_page: int,
) -> EvidenceCheck:
    """Prose fallback for a scrambled (column-interleaved) page: the verbatim
    check cannot succeed because the snapshot reorders the columns, so require
    key-token overlap instead — the same weaker check table/visual evidence
    uses. Every returned check is marked degraded so the score is capped and
    the outcome is recorded for review."""
    if _key_tokens_overlap(candidate.evidence_quote, snapshot_text):
        return EvidenceCheck(True, degraded=True)
    return EvidenceCheck(
        False,
        HARD_FAILURE_QUOTE,
        f"page p.{cited_page} flagged scrambled (two-column interleave); "
        "prose verbatim check degraded to key-token overlap, which also failed",
        degraded=True,
    )


def _table_or_visual_evidence_passes(
    candidate: CandidateCall,
    snapshot_text: str,
) -> EvidenceCheck:
    if not _has_specific_visual_locator(candidate.locator):
        return EvidenceCheck(
            False,
            HARD_FAILURE_VISUAL_LOCATOR,
            "table/visual evidence needs page plus table, figure, grid, caption, or heading",
        )

    if not _meaningful_tokens(candidate.evidence_quote):
        return EvidenceCheck(False, HARD_FAILURE_EVIDENCE, "table/visual evidence is empty")
    if not _key_tokens_overlap(candidate.evidence_quote, snapshot_text):
        return EvidenceCheck(
            False,
            HARD_FAILURE_EVIDENCE,
            "table/visual evidence tokens were not found in snapshot text",
        )
    return EvidenceCheck(True)


def _key_tokens_overlap(evidence_quote: str, snapshot_text: str) -> bool:
    """At least two (or all, if fewer) of the evidence's meaningful tokens must
    appear somewhere in the snapshot. Order is not checked — this is the weaker
    guarantee used for evidence the snapshot cannot preserve verbatim (tables,
    visuals, and scrambled-page prose)."""
    quote_tokens = set(_meaningful_tokens(evidence_quote))
    if not quote_tokens:
        return False
    source_tokens = set(_meaningful_tokens(snapshot_text))
    return len(quote_tokens & source_tokens) >= min(2, len(quote_tokens))


def _cited_page(locator: str) -> int | None:
    """First page number in a locator like ``p.2`` / ``p. 2 — Regional grid``."""
    match = re.search(r"p\.?\s*(\d+)", locator, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _has_specific_visual_locator(locator: str) -> bool:
    lowered = locator.lower()
    if re.fullmatch(r"\s*p\.?\s*\d+\s*", lowered):
        return False
    if re.fullmatch(r"\s*char:\d+-\d+\s*", lowered):
        return False
    keywords = (
        "table",
        "figure",
        "fig.",
        "grid",
        "chart",
        "dashboard",
        "matrix",
        "caption",
        "heading",
        "panel",
        "view",
    )
    return (
        any(keyword in lowered for keyword in keywords)
        or " - " in lowered
        or "\u2014" in locator
        or "\u2013" in locator
    )


def _meaningful_tokens(value: str) -> list[str]:
    normalized = normalize_quote_text(value).lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9-]{2,}", normalized)
    stopwords = {"the", "and", "for", "from", "with", "that", "this", "are", "was", "were"}
    return [token for token in tokens if token not in stopwords]
