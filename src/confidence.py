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


@dataclass(frozen=True, slots=True)
class EvidenceCheck:
    passed: bool
    reason_code: str = ""
    message: str = ""


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


def evidence_passes(candidate: CandidateCall, snapshot_text: str) -> EvidenceCheck:
    """Validate evidence according to its kind without using model judgment."""
    if not snapshot_text or not snapshot_text.strip():
        return EvidenceCheck(False, HARD_FAILURE_EVIDENCE, "snapshot text is empty")
    if candidate.evidence_kind == "prose":
        return _prose_evidence_passes(candidate, snapshot_text)
    return _table_or_visual_evidence_passes(candidate, snapshot_text)


def score_candidate(
    candidate: CandidateCall,
    *,
    taxonomy: Taxonomy,
    snapshot_text: str,
    page_count: int | None = None,
    verdict: CheckVerdict | None = None,
    checker_enabled: bool = False,
) -> ConfidenceResult:
    """Return a deterministic score, or raise ValueError for hard failures.

    page_count is the source's PDF page count (None for HTML); it feeds the
    read-quality signal. When checker_enabled, verdict is the second reader's
    categorical answers: any `fail` is a hard failure, and anything short of
    all-pass caps the score below the High band.
    """
    if candidate.taxonomy_match == "none" or not taxonomy.is_valid_label(candidate.sub_asset_class):
        raise ValueError(HARD_FAILURE_TAXONOMY)

    evidence_check = evidence_passes(candidate, snapshot_text)
    if not evidence_check.passed:
        raise ValueError(evidence_check.reason_code)

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
    return ConfidenceResult(
        candidate=candidate,
        confidence=score,
        band=band,
        review_flag=review_flag,
        evidence_check=evidence_check,
        checker_status=checker_status,
        checker_note=checker_note,
    )


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
    quote = normalize_quote_text(candidate.evidence_quote)
    source = normalize_quote_text(snapshot_text)
    if not quote:
        return EvidenceCheck(False, HARD_FAILURE_QUOTE, "prose quote is empty")
    if quote not in source:
        return EvidenceCheck(False, HARD_FAILURE_QUOTE, "prose quote was not found")
    return EvidenceCheck(True)


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

    quote_tokens = _meaningful_tokens(candidate.evidence_quote)
    source_tokens = set(_meaningful_tokens(snapshot_text))
    if not quote_tokens:
        return EvidenceCheck(False, HARD_FAILURE_EVIDENCE, "table/visual evidence is empty")

    required_overlap = min(2, len(set(quote_tokens)))
    overlap = len(set(quote_tokens) & source_tokens)
    if overlap < required_overlap:
        return EvidenceCheck(
            False,
            HARD_FAILURE_EVIDENCE,
            "table/visual evidence tokens were not found in snapshot text",
        )
    return EvidenceCheck(True)


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
