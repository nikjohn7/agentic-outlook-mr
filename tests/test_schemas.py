from __future__ import annotations

import unittest

from src.schemas import CandidateCall, SchemaError


def _candidate(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "source_id": "source-1",
        "chunk_id": "p1-5",
        "sub_asset_raw": "EM equities",
        "sub_asset_class": "Emerging Markets Equities",
        "taxonomy_match": "exact",
        "view": "O",
        "call_language": "explicit",
        "evidence_kind": "prose",
        "evidence_quote": "EM equities are favored in the outlook.",
        "locator": "p.3",
        "reasoning": "The manager favors the asset class.",
        "conflict": False,
    }
    values.update(overrides)
    return values


class BasisSchemaTest(unittest.TestCase):
    def test_absent_basis_defaults_to_stated_backcompat(self) -> None:
        # A frozen candidate written before the field existed still loads.
        candidate = CandidateCall.from_mapping(_candidate())

        self.assertEqual("stated", candidate.basis)
        self.assertIsNone(candidate.delta_value)
        self.assertIsNone(candidate.delta_unit)

    def test_explicit_stated_basis(self) -> None:
        candidate = CandidateCall.from_mapping(_candidate(basis="stated"))
        self.assertEqual("stated", candidate.basis)

    def test_inferred_basis_parses_without_delta_fields(self) -> None:
        candidate = CandidateCall.from_mapping(_candidate(basis="inferred"))

        self.assertEqual("inferred", candidate.basis)
        self.assertIsNone(candidate.delta_value)

    def test_forecast_delta_requires_delta_value_and_unit(self) -> None:
        with self.assertRaises(SchemaError):
            CandidateCall.from_mapping(_candidate(basis="forecast_delta"))
        with self.assertRaises(SchemaError):
            CandidateCall.from_mapping(_candidate(basis="forecast_delta", delta_unit="bp"))
        with self.assertRaises(SchemaError):
            CandidateCall.from_mapping(_candidate(basis="forecast_delta", delta_value=30))

    def test_forecast_delta_parses_with_fields(self) -> None:
        candidate = CandidateCall.from_mapping(
            _candidate(basis="forecast_delta", delta_value=30, delta_unit="bp")
        )

        self.assertEqual("forecast_delta", candidate.basis)
        self.assertEqual(30.0, candidate.delta_value)
        self.assertIsInstance(candidate.delta_value, float)
        self.assertEqual("bp", candidate.delta_unit)

    def test_forecast_delta_rejects_non_numeric_and_bool_delta(self) -> None:
        with self.assertRaises(SchemaError):
            CandidateCall.from_mapping(
                _candidate(basis="forecast_delta", delta_value="30", delta_unit="bp")
            )
        with self.assertRaises(SchemaError):
            # bool is an int subclass; it must not pose as a magnitude.
            CandidateCall.from_mapping(
                _candidate(basis="forecast_delta", delta_value=True, delta_unit="bp")
            )

    def test_forecast_delta_rejects_bad_unit(self) -> None:
        with self.assertRaises(SchemaError):
            CandidateCall.from_mapping(
                _candidate(basis="forecast_delta", delta_value=30, delta_unit="percent")
            )

    def test_invalid_basis_is_rejected(self) -> None:
        with self.assertRaises(SchemaError):
            CandidateCall.from_mapping(_candidate(basis="guessed"))

    def test_to_dict_round_trips_forecast_delta(self) -> None:
        original = CandidateCall.from_mapping(
            _candidate(basis="forecast_delta", delta_value=13.0, delta_unit="bp")
        )
        restored = CandidateCall.from_mapping(original.to_dict())

        self.assertEqual("forecast_delta", restored.basis)
        self.assertEqual(13.0, restored.delta_value)
        self.assertEqual("bp", restored.delta_unit)

    def test_to_dict_omits_delta_fields_for_non_forecast(self) -> None:
        payload = CandidateCall.from_mapping(_candidate(basis="inferred")).to_dict()

        self.assertEqual("inferred", payload["basis"])
        self.assertNotIn("delta_value", payload)
        self.assertNotIn("delta_unit", payload)


if __name__ == "__main__":
    unittest.main()
