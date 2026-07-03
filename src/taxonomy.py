"""Locked Allocator Pro taxonomy loading and exact-label validation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TAXONOMY_CSV = PROJECT_ROOT / "excel-file" / "Asset Class List - Locked.csv"
EXPECTED_LEAF_COUNT = 396

REQUIRED_COLUMNS = (
    "Number",
    "Sub-Asset Class",
    "Asset Class Category",
    "Asset Class",
    "Canva Groupings",
)


class TaxonomyError(ValueError):
    """Base error for invalid locked-taxonomy data or labels."""


class UnknownTaxonomyLabel(TaxonomyError):
    """Raised when a candidate label is not an exact locked taxonomy leaf."""


@dataclass(frozen=True, slots=True)
class TaxonomyEntry:
    """One locked sub-asset-class leaf and its deterministic lookup fields."""

    number: int
    sub_asset_class: str
    asset_class_category: str
    asset_class: str
    canva_groupings: str

    def output_fields(self) -> dict[str, str]:
        """Return the workbook output columns filled by deterministic lookup."""
        return {
            "Sub-Asset Class": self.sub_asset_class,
            "Asset Class Category": self.asset_class_category,
            "Canva Groupings": self.canva_groupings,
            "Asset Class": self.asset_class,
        }


class Taxonomy:
    """Exact-match validator for the locked 396-leaf taxonomy."""

    def __init__(self, entries: Iterable[TaxonomyEntry]) -> None:
        ordered_entries = tuple(sorted(entries, key=lambda entry: entry.number))
        by_label: dict[str, TaxonomyEntry] = {}
        by_number: dict[int, TaxonomyEntry] = {}

        for entry in ordered_entries:
            if not entry.sub_asset_class:
                raise TaxonomyError(f"taxonomy row {entry.number} has no Sub-Asset Class")
            if entry.sub_asset_class in by_label:
                raise TaxonomyError(f"duplicate Sub-Asset Class: {entry.sub_asset_class!r}")
            if entry.number in by_number:
                raise TaxonomyError(f"duplicate Number: {entry.number}")
            by_label[entry.sub_asset_class] = entry
            by_number[entry.number] = entry

        self._entries = ordered_entries
        self._by_label = by_label
        self._by_number = by_number

    @classmethod
    def from_csv(
        cls,
        path: str | Path = DEFAULT_TAXONOMY_CSV,
        *,
        expected_leaf_count: int | None = EXPECTED_LEAF_COUNT,
    ) -> "Taxonomy":
        """Load the locked taxonomy CSV and validate its structural contract."""
        csv_path = Path(path)
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise TaxonomyError(f"taxonomy CSV is empty: {csv_path}")

            missing_columns = [column for column in REQUIRED_COLUMNS if column not in reader.fieldnames]
            if missing_columns:
                missing = ", ".join(missing_columns)
                raise TaxonomyError(f"taxonomy CSV missing required columns: {missing}")

            entries = [_entry_from_row(row, row_number=index + 2) for index, row in enumerate(reader)]

        taxonomy = cls(entries)
        if expected_leaf_count is not None and len(taxonomy) != expected_leaf_count:
            raise TaxonomyError(
                f"expected {expected_leaf_count} taxonomy leaves, found {len(taxonomy)}"
            )
        return taxonomy

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterable[TaxonomyEntry]:
        return iter(self._entries)

    def labels(self) -> tuple[str, ...]:
        """Return locked leaf labels in workbook row order."""
        return tuple(entry.sub_asset_class for entry in self._entries)

    def is_valid_label(self, label: str) -> bool:
        """Return true only for exact locked `Sub-Asset Class` labels."""
        return label in self._by_label

    def require_label(self, label: str) -> TaxonomyEntry:
        """Return lookup metadata or raise for an unmappable candidate label."""
        try:
            return self._by_label[label]
        except KeyError as exc:
            raise UnknownTaxonomyLabel(
                f"{label!r} is not an exact locked Sub-Asset Class label"
            ) from exc

    def output_fields_for(self, label: str) -> dict[str, str]:
        """Return deterministic output fields for an exact locked leaf label."""
        return self.require_label(label).output_fields()


def load_taxonomy(
    path: str | Path = DEFAULT_TAXONOMY_CSV,
    *,
    expected_leaf_count: int | None = EXPECTED_LEAF_COUNT,
) -> Taxonomy:
    """Load the project taxonomy with the default locked-leaf count check."""
    return Taxonomy.from_csv(path, expected_leaf_count=expected_leaf_count)


def _entry_from_row(row: dict[str, str], *, row_number: int) -> TaxonomyEntry:
    raw_number = row["Number"]
    try:
        number = int(raw_number)
    except ValueError as exc:
        raise TaxonomyError(f"row {row_number} has invalid Number: {raw_number!r}") from exc

    values = {column: row[column] for column in REQUIRED_COLUMNS[1:]}
    empty_columns = [column for column, value in values.items() if value == ""]
    if empty_columns:
        missing = ", ".join(empty_columns)
        raise TaxonomyError(f"row {row_number} has empty required fields: {missing}")

    return TaxonomyEntry(
        number=number,
        sub_asset_class=values["Sub-Asset Class"],
        asset_class_category=values["Asset Class Category"],
        asset_class=values["Asset Class"],
        canva_groupings=values["Canva Groupings"],
    )
