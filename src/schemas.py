"""Shared data contract types for the Markets Recon POC pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


VALID_TAXONOMY_MATCHES = ("exact", "semantic", "none")
VALID_VIEWS = ("O", "N", "U", "UNCERTAIN")
VALID_CALL_LANGUAGES = ("explicit", "implied", "none")
VALID_EVIDENCE_KINDS = ("prose", "table", "visual")
VALID_CHECK_VERDICTS = ("pass", "unclear", "fail")
CHECK_QUESTIONS = ("supports_view", "forward_looking", "asset_match")

# How the call was derived — drives the deterministic materiality gate and the
# analyst-inference confidence tier (src/confidence.py):
#   stated         — an explicit dial/score/tier position or explicit OW/N/UW
#                    prose ("we are overweight X"). First-class, uncapped.
#   forecast_delta — a house forecast endpoint vs. the current level (a yield,
#                    FX, or price-target table). Requires delta_value/delta_unit
#                    so the materiality gate can size the move.
#   inferred       — a single-step analyst inference from macro/thematic prose
#                    that never states a position. Capped one band below stated.
VALID_BASIS = ("stated", "forecast_delta", "inferred")
VALID_DELTA_UNITS = ("bp", "pct")
DEFAULT_BASIS = "stated"


class SchemaError(ValueError):
    """Raised when pipeline data does not satisfy the shared contract."""


@dataclass(frozen=True, slots=True)
class CandidateCall:
    """One model-proposed allocation call before deterministic validation."""

    source_id: str
    chunk_id: str
    sub_asset_raw: str
    sub_asset_class: str
    taxonomy_match: str
    view: str
    call_language: str
    evidence_kind: str
    # One or more verbatim spans of supporting evidence. A single contiguous
    # quote is one span; an honest elision (two real passages the analyst joins
    # with "...") is emitted as an explicit list of spans, so each fragment can
    # be verified verbatim on its own without the join sinking the whole quote.
    evidence_spans: tuple[str, ...]
    locator: str
    reasoning: str
    conflict: bool = False
    # How this call was derived (VALID_BASIS). Backward-compatible: candidates
    # loaded from frozen runs written before this field existed default to
    # `stated`. New analyzer output always carries it (the prompt requires it).
    basis: str = DEFAULT_BASIS
    # Only for basis == "forecast_delta": the magnitude of the forecast move and
    # its unit ("bp" for yields/rates, "pct" for FX/price moves). Required for
    # forecast_delta candidates, absent (None) otherwise.
    delta_value: float | None = None
    delta_unit: str | None = None

    @property
    def evidence_quote(self) -> str:
        """Human-readable evidence: spans joined with an explicit ellipsis so
        analysts read an elided quote naturally, and so every existing consumer
        (commentary, checker/arbiter inputs, failure rows) keeps a single
        string."""
        return " ... ".join(self.evidence_spans)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "CandidateCall":
        missing = [field for field in REQUIRED_CANDIDATE_FIELDS if field not in value]
        if missing:
            raise SchemaError(f"candidate missing required fields: {', '.join(missing)}")

        basis = _optional_basis(value)
        delta_value, delta_unit = _require_delta_fields(value, basis)
        candidate = cls(
            source_id=_require_text(value, "source_id"),
            chunk_id=_require_text(value, "chunk_id"),
            sub_asset_raw=str(value.get("sub_asset_raw", "")),
            sub_asset_class=_require_text(value, "sub_asset_class"),
            taxonomy_match=_require_choice(value, "taxonomy_match", VALID_TAXONOMY_MATCHES),
            view=_require_choice(value, "view", VALID_VIEWS),
            call_language=_require_choice(value, "call_language", VALID_CALL_LANGUAGES),
            evidence_kind=_require_choice(value, "evidence_kind", VALID_EVIDENCE_KINDS),
            evidence_spans=_require_spans(value, "evidence_quote"),
            locator=_require_text(value, "locator"),
            reasoning=_require_text(value, "reasoning"),
            conflict=_require_bool(value, "conflict", default=False),
            basis=basis,
            delta_value=delta_value,
            delta_unit=delta_unit,
        )
        return candidate

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "chunk_id": self.chunk_id,
            "sub_asset_raw": self.sub_asset_raw,
            "sub_asset_class": self.sub_asset_class,
            "taxonomy_match": self.taxonomy_match,
            "view": self.view,
            "call_language": self.call_language,
            "evidence_kind": self.evidence_kind,
            # Round-trips through from_mapping: a lone span serializes as a plain
            # string (back-compat), multiple spans as a list.
            "evidence_quote": (
                list(self.evidence_spans)
                if len(self.evidence_spans) > 1
                else self.evidence_spans[0]
            ),
            "locator": self.locator,
            "reasoning": self.reasoning,
            "conflict": self.conflict,
            "basis": self.basis,
            # Only round-trip the delta fields when they carry a value, so a
            # stated/inferred candidate serializes without null delta keys.
            **(
                {"delta_value": self.delta_value, "delta_unit": self.delta_unit}
                if self.basis == "forecast_delta"
                else {}
            ),
        }


@dataclass(frozen=True, slots=True)
class CheckVerdict:
    """A second-reader model's categorical verdicts on one candidate.

    Verdicts are facts fed into the deterministic rubric — never a
    self-confidence number. `index` echoes the candidate's position in the
    checked batch so alignment is exact.
    """

    index: int
    supports_view: str
    forward_looking: str
    asset_match: str
    note: str = ""

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "CheckVerdict":
        index = value.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise SchemaError("verdict index must be a non-negative integer")
        return cls(
            index=index,
            supports_view=_require_choice(value, "supports_view", VALID_CHECK_VERDICTS),
            forward_looking=_require_choice(value, "forward_looking", VALID_CHECK_VERDICTS),
            asset_match=_require_choice(value, "asset_match", VALID_CHECK_VERDICTS),
            note=str(value.get("note", "")),
        )

    def answers(self) -> dict[str, str]:
        return {
            "supports_view": self.supports_view,
            "forward_looking": self.forward_looking,
            "asset_match": self.asset_match,
        }

    @property
    def all_pass(self) -> bool:
        return all(answer == "pass" for answer in self.answers().values())

    def failed_questions(self) -> list[str]:
        return [question for question, answer in self.answers().items() if answer == "fail"]


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """Source metadata needed to write workbook-shaped output rows."""

    source_id: str
    firm: str
    date: str
    source: str
    url: str


REQUIRED_CANDIDATE_FIELDS = (
    "source_id",
    "chunk_id",
    "sub_asset_class",
    "taxonomy_match",
    "view",
    "call_language",
    "evidence_kind",
    "evidence_quote",
    "locator",
    "reasoning",
)


def _require_text(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or item.strip() == "":
        raise SchemaError(f"{field} must be a non-empty string")
    return item


def _require_spans(value: dict[str, Any], field: str) -> tuple[str, ...]:
    """Accept evidence as one verbatim span (a string) or an explicit list of
    verbatim spans. Structure only: every span must be a non-empty string. The
    span-count/order/token guardrails are deterministic gates in
    src/confidence.py, not schema rules — and ellipses are never parsed out of a
    single string here (only an explicit list produces multiple spans)."""
    item = value.get(field)
    if isinstance(item, str):
        if item.strip() == "":
            raise SchemaError(f"{field} must be a non-empty string")
        return (item,)
    if isinstance(item, list):
        if not item:
            raise SchemaError(f"{field} must not be an empty list")
        spans: list[str] = []
        for span in item:
            if not isinstance(span, str) or span.strip() == "":
                raise SchemaError(f"{field} spans must each be a non-empty string")
            spans.append(span)
        return tuple(spans)
    raise SchemaError(f"{field} must be a string or a list of strings")


def _optional_basis(value: dict[str, Any]) -> str:
    """Parse the optional `basis` field. Absent => `stated` (backward-compat for
    frozen candidates written before the field existed). Present => must be a
    valid basis choice."""
    item = value.get("basis")
    if item is None:
        return DEFAULT_BASIS
    if not isinstance(item, str) or item not in VALID_BASIS:
        raise SchemaError(f"basis must be one of {', '.join(VALID_BASIS)}")
    return item


def _require_delta_fields(
    value: dict[str, Any], basis: str
) -> tuple[float | None, str | None]:
    """A forecast_delta candidate MUST carry a numeric `delta_value` and a
    `delta_unit` so the deterministic materiality gate can size the move; any
    other basis carries neither."""
    if basis != "forecast_delta":
        return None, None
    for field in ("delta_value", "delta_unit"):
        if field not in value:
            raise SchemaError(f"forecast_delta candidate requires {field}")
    raw = value.get("delta_value")
    # bool is an int subclass; reject it explicitly so True/False can't pose as a
    # magnitude.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise SchemaError("delta_value must be a number")
    unit = _require_choice(value, "delta_unit", VALID_DELTA_UNITS)
    return float(raw), unit


def _require_choice(value: dict[str, Any], field: str, choices: tuple[str, ...]) -> str:
    item = _require_text(value, field)
    if item not in choices:
        raise SchemaError(f"{field} must be one of {', '.join(choices)}")
    return item


def _require_bool(value: dict[str, Any], field: str, *, default: bool) -> bool:
    item = value.get(field, default)
    if not isinstance(item, bool):
        raise SchemaError(f"{field} must be a boolean")
    return item
