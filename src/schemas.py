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
    evidence_quote: str
    locator: str
    reasoning: str
    conflict: bool = False

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "CandidateCall":
        missing = [field for field in REQUIRED_CANDIDATE_FIELDS if field not in value]
        if missing:
            raise SchemaError(f"candidate missing required fields: {', '.join(missing)}")

        candidate = cls(
            source_id=_require_text(value, "source_id"),
            chunk_id=_require_text(value, "chunk_id"),
            sub_asset_raw=str(value.get("sub_asset_raw", "")),
            sub_asset_class=_require_text(value, "sub_asset_class"),
            taxonomy_match=_require_choice(value, "taxonomy_match", VALID_TAXONOMY_MATCHES),
            view=_require_choice(value, "view", VALID_VIEWS),
            call_language=_require_choice(value, "call_language", VALID_CALL_LANGUAGES),
            evidence_kind=_require_choice(value, "evidence_kind", VALID_EVIDENCE_KINDS),
            evidence_quote=_require_text(value, "evidence_quote"),
            locator=_require_text(value, "locator"),
            reasoning=_require_text(value, "reasoning"),
            conflict=_require_bool(value, "conflict", default=False),
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
            "evidence_quote": self.evidence_quote,
            "locator": self.locator,
            "reasoning": self.reasoning,
            "conflict": self.conflict,
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
