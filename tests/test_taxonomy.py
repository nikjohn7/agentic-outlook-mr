from __future__ import annotations

import unittest

from src.taxonomy import EXPECTED_LEAF_COUNT, Taxonomy, UnknownTaxonomyLabel


class TaxonomyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()

    def test_locked_taxonomy_has_396_distinct_leaves(self) -> None:
        labels = self.taxonomy.labels()

        self.assertEqual(EXPECTED_LEAF_COUNT, len(self.taxonomy))
        self.assertEqual(EXPECTED_LEAF_COUNT, len(labels))
        self.assertEqual(EXPECTED_LEAF_COUNT, len(set(labels)))

    def test_every_locked_leaf_round_trips_to_lookup_fields(self) -> None:
        for entry in self.taxonomy:
            with self.subTest(sub_asset_class=entry.sub_asset_class):
                self.assertTrue(self.taxonomy.is_valid_label(entry.sub_asset_class))
                self.assertEqual(entry, self.taxonomy.require_label(entry.sub_asset_class))
                self.assertEqual(
                    {
                        "Sub-Asset Class": entry.sub_asset_class,
                        "Asset Class Category": entry.asset_class_category,
                        "Canva Groupings": entry.canva_groupings,
                        "Asset Class": entry.asset_class,
                    },
                    self.taxonomy.output_fields_for(entry.sub_asset_class),
                )

    def test_known_leaf_lookup_uses_workbook_columns(self) -> None:
        self.assertEqual(
            {
                "Sub-Asset Class": "Taiwan Equities",
                "Asset Class Category": "Equities - EMs",
                "Canva Groupings": "Equities - Geography",
                "Asset Class": "Equities",
            },
            self.taxonomy.output_fields_for("Taiwan Equities"),
        )

    def test_unknown_or_non_exact_labels_are_rejected(self) -> None:
        invalid_labels = [
            "EM equities",
            "Taiwan equities",
            " Taiwan Equities ",
            "Taiwan Equity",
            "",
        ]

        for label in invalid_labels:
            with self.subTest(label=label):
                self.assertFalse(self.taxonomy.is_valid_label(label))
                with self.assertRaises(UnknownTaxonomyLabel):
                    self.taxonomy.require_label(label)


if __name__ == "__main__":
    unittest.main()
