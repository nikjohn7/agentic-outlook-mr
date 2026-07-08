from __future__ import annotations

import unittest

from src.confidence import (
    CALL_LANGUAGE_POINTS,
    CHECKER_ADEQUATE_DEDUCTION,
    CHECKER_THIN_CAP,
    CHECKER_UNCONFIRMED_CAP,
    FORECAST_DELTA_CAP,
    HARD_FAILURE_MATERIALITY,
    INFERRED_CAP,
    MATERIALITY_FLOOR_BP,
    MATERIALITY_FLOOR_PCT,
    MIN_HTML_SNAPSHOT_CHARS,
    MIN_PDF_CHARS_PER_PAGE,
    SCRAMBLED_PROSE_CAP,
    SUBSEQUENCE_MAX_NOISE_TOKENS_PER_GAP,
    SUBSEQUENCE_MAX_WINDOW_MULTIPLE,
    VISUAL_UNVERIFIED_BY_TEXT,
    evidence_passes,
    normalize_quote_text,
    score_candidate,
    snapshot_read_quality,
)
from src.schemas import CandidateCall, CheckVerdict
from src.taxonomy import Taxonomy


class ConfidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()

    def test_quote_normalization_handles_hyphenation_and_quotes(self) -> None:
        source = "The manager said \u201cEmerging mar-\n kets equities look attractive.\u201d"
        quote = '"Emerging markets equities look attractive."'

        self.assertIn(normalize_quote_text(quote), normalize_quote_text(source))

    def test_prose_quote_failure_is_hard_failure(self) -> None:
        candidate = _candidate(evidence_quote="fabricated quote")

        check = evidence_passes(candidate, "real source text")

        self.assertFalse(check.passed)
        self.assertEqual("quote_not_found", check.reason_code)

    def test_prose_quote_matches_across_linebreak_hyphen_join(self) -> None:
        # The PDF line-broke "AI-related", so the snapshot normalizes to
        # "AIrelated"; a quote of the rendered page keeps the hyphen. Match.
        candidate = _candidate(evidence_quote="AI-related spending is set to move up a gear")
        snapshot = "We think that as AI-\nrelated spending is set to move up a gear this year."

        self.assertTrue(evidence_passes(candidate, snapshot).passed)

    def test_prose_quote_matches_when_snapshot_keeps_intra_word_hyphen(self) -> None:
        # Reverse direction: snapshot keeps the hyphen, the quote omits it.
        candidate = _candidate(evidence_quote="AIrelated spending is set to move up a gear")
        snapshot = "We think that AI-related spending is set to move up a gear this year."

        self.assertTrue(evidence_passes(candidate, snapshot).passed)

    def test_prose_quote_matches_across_dash_variants(self) -> None:
        # En dash and em dash fold to a plain hyphen (then to nothing intra-word).
        en_dash = _candidate(evidence_quote="the risk-reward balance favors equities")
        em_dash = _candidate(evidence_quote="a long-term overweight stance")

        self.assertTrue(
            evidence_passes(en_dash, "In our view the risk–reward balance favors equities.").passed
        )
        self.assertTrue(
            evidence_passes(em_dash, "We hold a long—term overweight stance today.").passed
        )

    def test_prose_paraphrase_or_reorder_still_fails(self) -> None:
        # Only typography is folded: reordered/stitched wording must STILL fail.
        candidate = _candidate(evidence_quote="emerging market equities are overweight")

        check = evidence_passes(
            candidate, "We are overweight emerging market equities for the second half."
        )

        self.assertFalse(check.passed)
        self.assertEqual("quote_not_found", check.reason_code)

    def test_multi_span_prose_passes_when_each_span_matches_in_order(self) -> None:
        # An honest elision: two real passages verified verbatim on their own.
        candidate = _candidate(
            evidence_quote=[
                "We could start a rate-hiking cycle from June",
                "the bank would deliver rate cuts back to neutral later",
            ]
        )
        snapshot = (
            "Growth is firm. We could start a rate-hiking cycle from June. "
            "Later, as inflation normalizes, the bank would deliver rate cuts "
            "back to neutral later in the horizon."
        )

        self.assertTrue(evidence_passes(candidate, snapshot).passed)

    def test_multi_span_prose_out_of_order_fails(self) -> None:
        # Same two spans, but stitched in the reverse of their document order.
        candidate = _candidate(
            evidence_quote=[
                "the bank would deliver rate cuts back to neutral later",
                "We could start a rate-hiking cycle from June",
            ]
        )
        snapshot = (
            "Growth is firm. We could start a rate-hiking cycle from June. "
            "Later, as inflation normalizes, the bank would deliver rate cuts "
            "back to neutral later in the horizon."
        )

        check = evidence_passes(candidate, snapshot)

        self.assertFalse(check.passed)
        self.assertEqual("quote_not_found", check.reason_code)
        self.assertIn("order", check.message)

    def test_multi_span_prose_tiny_span_fails(self) -> None:
        # The second span has too few meaningful tokens to verify as a stitch.
        candidate = _candidate(
            evidence_quote=[
                "We could start a rate-hiking cycle from June",
                "back to neutral",
            ]
        )
        snapshot = (
            "We could start a rate-hiking cycle from June, then move rates "
            "back to neutral by year end."
        )

        check = evidence_passes(candidate, snapshot)

        self.assertFalse(check.passed)
        self.assertEqual("quote_not_found", check.reason_code)
        self.assertIn("too short", check.message)

    def test_more_than_three_spans_fails(self) -> None:
        candidate = _candidate(
            evidence_quote=[
                "the first meaningful passage of evidence",
                "the second meaningful passage of evidence",
                "the third meaningful passage of evidence",
                "the fourth meaningful passage of evidence",
            ]
        )
        snapshot = (
            "the first meaningful passage of evidence and the second meaningful "
            "passage of evidence and the third meaningful passage of evidence "
            "and the fourth meaningful passage of evidence."
        )

        check = evidence_passes(candidate, snapshot)

        self.assertFalse(check.passed)
        self.assertEqual("quote_not_found", check.reason_code)
        self.assertIn("more than 3 spans", check.message)

    def test_multi_span_prose_paraphrase_still_fails(self) -> None:
        # One span is a paraphrase absent from the source: the stitch must fail.
        candidate = _candidate(
            evidence_quote=[
                "We could start a rate-hiking cycle from June",
                "policymakers plan to slash rates aggressively next year",
            ]
        )
        snapshot = (
            "Growth is firm. We could start a rate-hiking cycle from June. "
            "Later, as inflation normalizes, the bank would deliver rate cuts."
        )

        check = evidence_passes(candidate, snapshot)

        self.assertFalse(check.passed)
        self.assertEqual("quote_not_found", check.reason_code)

    def test_single_string_quote_is_treated_as_one_span_backcompat(self) -> None:
        # A short single string (below the multi-span token floor) still passes:
        # the floor applies only to stitched multi-span evidence.
        candidate = _candidate(evidence_quote="a long-term overweight stance")

        self.assertTrue(
            evidence_passes(candidate, "We hold a long-term overweight stance today.").passed
        )

    def test_exact_quote_match_tier_is_recorded(self) -> None:
        check = evidence_passes(
            _candidate(evidence_quote="a long-term overweight stance"),
            "We hold a long-term overweight stance today.",
        )

        self.assertTrue(check.passed)
        self.assertEqual("exact", check.quote_match)

    def test_normalized_quote_match_tier_is_recorded(self) -> None:
        check = evidence_passes(
            _candidate(evidence_quote="AI-related spending is set to move up a gear"),
            "We think that as AI-\nrelated spending is set to move up a gear this year.",
        )

        self.assertTrue(check.passed)
        self.assertEqual("normalized", check.quote_match)


class JanusHendersonQuoteGateFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()

    # Actual snapshot excerpts from
    # client-runs/runs-07072026-98rows/work/98b-split1/
    # janus-henderson-investors-market-gps-investment-outlook-mid-year-2026/snapshot.txt.
    SNAPSHOT_EXCERPTS = """
We also continue to believe inflation could be a more sizable risk than the acceleration
current consensus suggests. This implies fixed income portfolios may
Ex-U.S.: Balanced opportunity
>
benefit from a focus on shorter-duration instruments and superior
set, attractive valuations
income, rather than overall total return.
Enhanced cash, securitized strategies, and opportunistic fixed FIXED INCOME
income strategies seem most relevant for a potentially changing Securitized credit:
>
inflation paradigm. Income with resilience

Investor considerations
Infrastructure – the US$106 trillion opportunity: The AI buildout has extended into power, utilities, materials,
>
and energy. A cumulative US$106 trillion will be needed for new and updated infrastructure through 2040.3
Demand is already exceeding supply across semiconductors, memory, cooling systems, optical, and power
equipment. Companies operating at these bottlenecks, where capital expenditure shows no sign of slowing,
merit consideration.

Investor considerations
Catalysts across biopharma: Through Q1, all biopharma transactions have been below US$10 billion, creating
>
potential for a record-breaking year of M&A if larger deals come to market.6 The sector has an expected 1,200
potential "events" this year7 - from anticipated FDA decisions to trial enrollments and data readouts - that discerning
investors may be able to capitalize on.
Beneficiaries of structural change: Smaller companies have a relatively large presence in the industrials and
>
materials sectors, providing valuable exposure to both the AI buildout and nearshoring trends. Innovative solutions
may present attractive investment opportunities.
"""

    PREVIOUSLY_DROPPED = (
        (
            "Duration - Short",
            "This implies fixed income portfolios may benefit from a focus on shorter-duration instruments and superior income, rather than overall total return.",
            "subsequence",
        ),
        (
            "Cash/Money Markets",
            "Enhanced cash, securitized strategies, and opportunistic fixed income strategies seem most relevant for a potentially changing inflation paradigm.",
            "subsequence",
        ),
        (
            "Securitized/Structured (ABS/MBS/CDS/Swaps/CLOs)",
            "Enhanced cash, securitized strategies, and opportunistic fixed income strategies seem most relevant for a potentially changing inflation paradigm.",
            "subsequence",
        ),
        (
            "Infrastructure Theme",
            "Infrastructure – the US$106 trillion opportunity: The AI buildout has extended into power, utilities, materials, and energy.",
            "normalized",
        ),
        (
            "Energy Sector",
            "Infrastructure – the US$106 trillion opportunity: The AI buildout has extended into power, utilities, materials, and energy.",
            "normalized",
        ),
        (
            "Healthcare/Pharma",
            "Catalysts across biopharma: Through Q1, all biopharma transactions have been below US$10 billion, creating potential for a record-breaking year of M&A if larger deals come to market.",
            "normalized",
        ),
        (
            "Industrials",
            "Smaller companies have a relatively large presence in the industrials and materials sectors, providing valuable exposure to both the AI buildout and nearshoring trends. Innovative solutions may present attractive investment opportunities.",
            "normalized",
        ),
        (
            "Materials",
            "Smaller companies have a relatively large presence in the industrials and materials sectors, providing valuable exposure to both the AI buildout and nearshoring trends. Innovative solutions may present attractive investment opportunities.",
            "normalized",
        ),
    )

    def test_all_eight_split1_janus_quote_drops_verify_deterministically(self) -> None:
        for label, quote, expected_tier in self.PREVIOUSLY_DROPPED:
            with self.subTest(label=label):
                check = evidence_passes(
                    _candidate(evidence_quote=quote),
                    self.SNAPSHOT_EXCERPTS,
                )

                self.assertTrue(check.passed)
                self.assertEqual(expected_tier, check.quote_match)

    def test_subsequence_match_is_capped_and_review_flagged(self) -> None:
        quote = self.PREVIOUSLY_DROPPED[0][1]
        result = score_candidate(
            _candidate(
                evidence_quote=quote,
                taxonomy_match="semantic",
                call_language="directional",
            ),
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot(self.SNAPSHOT_EXCERPTS),
        )

        self.assertEqual(SCRAMBLED_PROSE_CAP, result.confidence)
        self.assertEqual("Medium", result.band)
        self.assertEqual("review", result.review_flag)
        self.assertEqual("subsequence", result.evidence_check.quote_match)
        self.assertIn(
            f"max {SUBSEQUENCE_MAX_NOISE_TOKENS_PER_GAP} injected tokens",
            result.evidence_check.message,
        )
        self.assertIn(
            f"window <= {SUBSEQUENCE_MAX_WINDOW_MULTIPLE}x",
            result.evidence_check.message,
        )

    def test_fabricated_quote_still_fails(self) -> None:
        check = evidence_passes(
            _candidate(evidence_quote="Platinum rhodium palladium outperform together."),
            self.SNAPSHOT_EXCERPTS,
        )

        self.assertFalse(check.passed)
        self.assertEqual("quote_not_found", check.reason_code)

    def test_reordered_quote_still_fails(self) -> None:
        check = evidence_passes(
            _candidate(
                evidence_quote=(
                    "Benefit may portfolios income fixed implies this from instruments shorter-duration."
                )
            ),
            self.SNAPSHOT_EXCERPTS,
        )

        self.assertFalse(check.passed)
        self.assertEqual("quote_not_found", check.reason_code)

    def test_semantic_implied_call_scores_medium_under_v2(self) -> None:
        candidate = _candidate(taxonomy_match="semantic", call_language="implied")

        result = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot("EM equities are favored in the outlook."),
        )

        self.assertEqual(72, result.confidence)
        self.assertEqual("Medium", result.band)
        self.assertEqual("review", result.review_flag)

    def test_thin_snapshot_drops_read_quality_points(self) -> None:
        candidate = _candidate(taxonomy_match="semantic", call_language="implied")

        result = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text="EM equities are favored in the outlook.",
        )

        self.assertEqual(62, result.confidence)
        self.assertEqual("Medium", result.band)
        self.assertEqual("review", result.review_flag)

    def test_snapshot_read_quality_pdf_per_page_floor(self) -> None:
        thin_scanned = "a" * (MIN_PDF_CHARS_PER_PAGE * 2)  # 10 pages' worth missing

        self.assertFalse(snapshot_read_quality(thin_scanned, page_count=10))
        self.assertTrue(snapshot_read_quality("a" * (MIN_PDF_CHARS_PER_PAGE * 10), page_count=10))
        self.assertFalse(snapshot_read_quality("anything", page_count=0))

    def test_snapshot_read_quality_html_total_floor(self) -> None:
        self.assertFalse(snapshot_read_quality("a" * (MIN_HTML_SNAPSHOT_CHARS - 1)))
        self.assertTrue(snapshot_read_quality("a" * MIN_HTML_SNAPSHOT_CHARS))

    def test_table_visual_requires_specific_locator(self) -> None:
        candidate = _candidate(
            evidence_kind="table",
            evidence_quote="Taiwan overweight",
            locator="p.5",
        )

        check = evidence_passes(candidate, "Taiwan overweight table")

        self.assertFalse(check.passed)
        self.assertEqual("visual_locator_missing", check.reason_code)

    def test_table_visual_accepts_compact_specific_reference(self) -> None:
        candidate = _candidate(
            evidence_kind="visual",
            evidence_quote="Taiwan overweight",
            locator="p.5 - Regional views grid",
        )

        check = evidence_passes(candidate, "Regional views grid Taiwan overweight")

        self.assertTrue(check.passed)

    def test_table_visual_accepts_em_dash_artifact_reference(self) -> None:
        candidate = _candidate(
            evidence_kind="visual",
            evidence_quote="Taiwan overweight",
            locator="p.5 \u2014 Regional views grid",
        )

        check = evidence_passes(candidate, "Regional views grid Taiwan overweight")

        self.assertTrue(check.passed)


class CallLanguageScoringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()

    def test_each_call_language_tier_scores_its_constant(self) -> None:
        base_without_language = 25 + 20 + 15 + 10
        for call_language, points in CALL_LANGUAGE_POINTS.items():
            with self.subTest(call_language=call_language):
                candidate = _candidate(
                    call_language=call_language,
                    evidence_kind="visual" if call_language == "explicit_dial" else "prose",
                    evidence_quote=(
                        "EM overweight dial"
                        if call_language == "explicit_dial"
                        else "EM equities are favored in the outlook."
                    ),
                    locator=(
                        "p.3 - Regional allocation dial"
                        if call_language == "explicit_dial"
                        else "p.3"
                    ),
                )
                snapshot = (
                    _healthy_snapshot("EM overweight dial")
                    if call_language == "explicit_dial"
                    else _healthy_snapshot(candidate.evidence_quote)
                )

                result = score_candidate(candidate, taxonomy=self.taxonomy, snapshot_text=snapshot)

                self.assertEqual(base_without_language + points, result.confidence)

    def test_legacy_explicit_and_implied_rescore_to_v2_tiers(self) -> None:
        explicit = _candidate(call_language="explicit")
        implied = _candidate(call_language="implied")

        explicit_result = score_candidate(
            explicit,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot(explicit.evidence_quote),
        )
        implied_result = score_candidate(
            implied,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot(implied.evidence_quote),
        )

        self.assertEqual("explicit_stance", explicit.call_language)
        self.assertEqual(96, explicit_result.confidence)
        self.assertEqual(82, implied_result.confidence)

    def test_explicit_dial_on_prose_downgrades_to_explicit_stance(self) -> None:
        candidate = _candidate(call_language="explicit_dial")

        result = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot(candidate.evidence_quote),
        )

        self.assertEqual(96, result.confidence)
        self.assertIn("scored as explicit_stance", result.call_language_note)
        self.assertEqual("none", result.review_flag)
        # The persisted field is the EFFECTIVE (downgraded) value, not the raw
        # candidate bucket.
        self.assertEqual("explicit_dial", candidate.call_language)
        self.assertEqual("explicit_stance", result.call_language)

    def test_result_persists_effective_call_language_unchanged_for_prose(self) -> None:
        candidate = _candidate(call_language="directional")

        result = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot(candidate.evidence_quote),
        )

        self.assertEqual("directional", result.call_language)
        self.assertEqual("", result.call_language_note)


class ScrambledPageProseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()

    # A correct contiguous quote of the rendered page whose words are reordered
    # in the scrambled snapshot: verbatim fails, key tokens are all present.
    QUOTE = "front end government bond markets offer opportunities"
    SCRAMBLED_SNAPSHOT = "markets government opportunities bond end front offer stance today"

    def test_scrambled_page_prose_falls_back_to_key_tokens(self) -> None:
        candidate = _candidate(
            locator="p.2", evidence_quote=self.QUOTE, taxonomy_match="semantic",
            call_language="directional",
        )

        check = evidence_passes(
            candidate, _healthy_snapshot(self.SCRAMBLED_SNAPSHOT),
            scrambled_pages=frozenset({2}),
        )

        self.assertTrue(check.passed)
        self.assertTrue(check.degraded)

    def test_scrambled_page_prose_pass_is_capped_and_flagged(self) -> None:
        candidate = _candidate(
            locator="p.2", evidence_quote=self.QUOTE, taxonomy_match="semantic",
            call_language="directional",
        )

        result = score_candidate(
            candidate, taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot(self.SCRAMBLED_SNAPSHOT),
            scrambled_pages=frozenset({2}),
        )

        self.assertEqual(SCRAMBLED_PROSE_CAP, result.confidence)
        self.assertEqual("Medium", result.band)
        self.assertEqual("review", result.review_flag)
        self.assertTrue(result.evidence_check.degraded)

    def test_scrambled_page_prose_still_fails_when_key_tokens_absent(self) -> None:
        candidate = _candidate(
            locator="p.2", evidence_quote="tungsten palladium rhodium platinum"
        )

        check = evidence_passes(
            candidate, _healthy_snapshot("unrelated commentary about domestic equities"),
            scrambled_pages=frozenset({2}),
        )

        self.assertFalse(check.passed)
        self.assertEqual("quote_not_found", check.reason_code)
        self.assertTrue(check.degraded)
        self.assertIn("scrambled", check.message)

    def test_scrambled_page_prose_failure_is_a_hard_failure(self) -> None:
        candidate = _candidate(
            locator="p.2", evidence_quote="tungsten palladium rhodium platinum"
        )

        with self.assertRaises(ValueError) as caught:
            score_candidate(
                candidate, taxonomy=self.taxonomy,
                snapshot_text=_healthy_snapshot("unrelated commentary about equities"),
                scrambled_pages=frozenset({2}),
            )

        self.assertEqual("quote_not_found", str(caught.exception))

    def test_clean_page_prose_keeps_verbatim_check(self) -> None:
        # Same reordered snapshot, but the cited page is NOT scrambled: the
        # verbatim guarantee must still reject the out-of-order wording.
        candidate = _candidate(locator="p.2", evidence_quote=self.QUOTE)

        check = evidence_passes(candidate, _healthy_snapshot(self.SCRAMBLED_SNAPSHOT))

        self.assertFalse(check.passed)
        self.assertFalse(check.degraded)
        self.assertEqual("quote_not_found", check.reason_code)

    def test_scramble_only_affects_the_cited_page(self) -> None:
        # The source has a scrambled page, but this call cites a different,
        # clean page, so verbatim still applies.
        candidate = _candidate(locator="p.5", evidence_quote=self.QUOTE)

        check = evidence_passes(
            candidate, _healthy_snapshot(self.SCRAMBLED_SNAPSHOT),
            scrambled_pages=frozenset({2}),
        )

        self.assertFalse(check.passed)
        self.assertFalse(check.degraded)


class OcrPageProseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()

    def test_ocr_page_prose_verbatim_pass_is_capped_and_flagged(self) -> None:
        quote = "global equities remain attractive"
        candidate = _candidate(locator="p.3", evidence_quote=quote)

        result = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot(f"We think {quote} in the current cycle."),
            ocr_pages=frozenset({3}),
        )

        self.assertEqual(SCRAMBLED_PROSE_CAP, result.confidence)
        self.assertEqual("review", result.review_flag)
        self.assertTrue(result.evidence_check.degraded)
        self.assertIn("OCR", result.evidence_check.message)

    def test_ocr_page_prose_falls_back_to_key_tokens(self) -> None:
        candidate = _candidate(
            locator="p.3",
            evidence_quote="global equities remain attractive",
            taxonomy_match="semantic",
            call_language="directional",
        )

        check = evidence_passes(
            candidate,
            _healthy_snapshot("attractive global equities remain a stated stance"),
            ocr_pages=frozenset({3}),
        )

        self.assertTrue(check.passed)
        self.assertTrue(check.degraded)
        self.assertIn("OCR", check.message)

    def test_ocr_page_prose_failure_has_ocr_specific_message(self) -> None:
        candidate = _candidate(locator="p.3", evidence_quote="platinum rhodium palladium")

        check = evidence_passes(
            candidate,
            _healthy_snapshot("unrelated source text about rates"),
            ocr_pages=frozenset({3}),
        )

        self.assertFalse(check.passed)
        self.assertTrue(check.degraded)
        self.assertIn("OCR", check.message)


class VisualUnverifiedByTextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()

    def test_visual_token_miss_on_print_captured_page_routes_to_checker(self) -> None:
        candidate = _candidate(
            evidence_kind="visual",
            evidence_quote="Japan equities dial sits in the Neutral box",
            locator="p.4 - Regional equity dials",
            call_language="explicit_dial",
            view="N",
            sub_asset_class="Japan Equities",
        )

        check = evidence_passes(
            candidate,
            _healthy_snapshot("page text omits the rendered dial labels"),
            visual_pages=frozenset({4}),
        )

        self.assertTrue(check.passed)
        self.assertEqual(VISUAL_UNVERIFIED_BY_TEXT, check.reason_code)
        self.assertTrue(check.visual_unverified_by_text)
        self.assertFalse(check.degraded)

    def test_visual_token_miss_on_clean_text_source_still_hard_fails(self) -> None:
        candidate = _candidate(
            evidence_kind="visual",
            evidence_quote="Japan equities dial sits in the Neutral box",
            locator="p.4 - Regional equity dials",
            call_language="explicit_dial",
            view="N",
            sub_asset_class="Japan Equities",
        )

        check = evidence_passes(
            candidate,
            _healthy_snapshot("page text omits the rendered dial labels"),
        )

        self.assertFalse(check.passed)
        self.assertEqual("evidence_check_failed", check.reason_code)
        self.assertFalse(check.visual_unverified_by_text)

    def test_visual_route_triggers_only_for_table_or_visual_evidence(self) -> None:
        candidate = _candidate(
            evidence_kind="prose",
            evidence_quote="Japan equities dial sits in the Neutral box",
            locator="p.4",
            view="N",
            sub_asset_class="Japan Equities",
        )

        check = evidence_passes(
            candidate,
            _healthy_snapshot("page text omits the rendered dial labels"),
            visual_pages=frozenset({4}),
        )

        self.assertFalse(check.passed)
        self.assertEqual("quote_not_found", check.reason_code)
        self.assertFalse(check.visual_unverified_by_text)

    def test_visual_route_triggers_only_when_cited_page_is_visual(self) -> None:
        candidate = _candidate(
            evidence_kind="visual",
            evidence_quote="Japan equities dial sits in the Neutral box",
            locator="p.4 - Regional equity dials",
            call_language="explicit_dial",
            view="N",
            sub_asset_class="Japan Equities",
        )

        check = evidence_passes(
            candidate,
            _healthy_snapshot("page text omits the rendered dial labels"),
            visual_pages=frozenset({2}),
        )

        self.assertFalse(check.passed)
        self.assertEqual("evidence_check_failed", check.reason_code)

    def test_visual_route_scoring_uses_checker_strength_mapping(self) -> None:
        candidate = _candidate(
            evidence_kind="visual",
            evidence_quote="Japan equities dial sits in the Neutral box",
            locator="p.4 - Regional equity dials",
            call_language="explicit_dial",
            view="N",
            sub_asset_class="Japan Equities",
        )
        snapshot = _healthy_snapshot("page text omits the rendered dial labels")

        decisive = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=snapshot,
            visual_pages=frozenset({4}),
            verdict=_verdict(evidence_strength="decisive"),
            checker_enabled=True,
        )
        adequate = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=snapshot,
            visual_pages=frozenset({4}),
            verdict=_verdict(evidence_strength="adequate"),
            checker_enabled=True,
        )
        thin = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=snapshot,
            visual_pages=frozenset({4}),
            verdict=_verdict(evidence_strength="thin"),
            checker_enabled=True,
        )

        self.assertEqual("High", decisive.band)
        self.assertEqual("none", decisive.review_flag)
        self.assertEqual(decisive.confidence - CHECKER_ADEQUATE_DEDUCTION, adequate.confidence)
        self.assertEqual("High", adequate.band)
        self.assertEqual(CHECKER_THIN_CAP, thin.confidence)
        self.assertEqual("review", thin.review_flag)

    def test_visual_route_checker_fail_is_diagnosable(self) -> None:
        candidate = _candidate(
            evidence_kind="visual",
            evidence_quote="Japan equities dial sits in the Neutral box",
            locator="p.4 - Regional equity dials",
            call_language="explicit_dial",
            view="N",
            sub_asset_class="Japan Equities",
        )

        with self.assertRaises(ValueError) as caught:
            score_candidate(
                candidate,
                taxonomy=self.taxonomy,
                snapshot_text=_healthy_snapshot("page text omits the rendered dial labels"),
                visual_pages=frozenset({4}),
                verdict=_verdict(supports_view="fail", note="dial shows underweight"),
                checker_enabled=True,
            )

        self.assertEqual("checker_sign_mismatch", str(caught.exception))
        self.assertIn("checker visual review failed", caught.exception.message)


class CheckerScoringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()

    def _score(self, verdict: CheckVerdict | None, *, checker_enabled: bool = True):
        return score_candidate(
            _candidate(),
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot("EM equities are favored in the outlook."),
            verdict=verdict,
            checker_enabled=checker_enabled,
        )

    def test_fail_verdict_is_a_hard_failure_with_specific_reason(self) -> None:
        verdict = _verdict(supports_view="fail")

        with self.assertRaises(ValueError) as caught:
            self._score(verdict)

        self.assertEqual("checker_sign_mismatch", str(caught.exception))

    def test_confirmed_verdict_leaves_score_uncapped(self) -> None:
        result = self._score(_verdict())

        self.assertEqual(96, result.confidence)
        self.assertEqual("confirmed", result.checker_status)
        self.assertEqual("decisive", result.checker_strength)
        self.assertEqual("none", result.review_flag)

    def test_adequate_verdict_deducts_but_can_remain_high(self) -> None:
        result = self._score(_verdict(evidence_strength="adequate"))

        self.assertEqual(96 - CHECKER_ADEQUATE_DEDUCTION, result.confidence)
        self.assertEqual("High", result.band)
        self.assertEqual("none", result.review_flag)
        self.assertEqual("adequate", result.checker_strength)

    def test_thin_verdict_caps_below_high_and_flags_review(self) -> None:
        result = self._score(_verdict(evidence_strength="thin"))

        self.assertEqual(CHECKER_THIN_CAP, result.confidence)
        self.assertEqual("Medium", result.band)
        self.assertEqual("review", result.review_flag)
        self.assertEqual("thin", result.checker_strength)
        self.assertIn("evidence_strength thin", result.cap_reason)

    def test_missing_evidence_strength_preserves_legacy_all_pass_semantics(self) -> None:
        result = self._score(
            CheckVerdict.from_mapping(
                {
                    "index": 0,
                    "supports_view": "pass",
                    "forward_looking": "pass",
                    "asset_match": "pass",
                }
            )
        )

        self.assertEqual(96, result.confidence)
        self.assertEqual("decisive", result.checker_strength)
        self.assertIn("legacy all-pass", result.checker_note)

    def test_unclear_verdict_caps_below_high_and_flags_review(self) -> None:
        result = self._score(_verdict(forward_looking="unclear", note="mixed recap"))

        self.assertEqual(CHECKER_UNCONFIRMED_CAP, result.confidence)
        self.assertEqual("Medium", result.band)
        self.assertEqual("review", result.review_flag)
        self.assertEqual("unclear", result.checker_status)
        self.assertEqual("mixed recap", result.checker_note)

    def test_missing_verdict_caps_when_checker_enabled(self) -> None:
        result = self._score(None)

        self.assertEqual(CHECKER_UNCONFIRMED_CAP, result.confidence)
        self.assertEqual("missing", result.checker_status)
        self.assertEqual("review", result.review_flag)

    def test_checker_off_keeps_legacy_scoring(self) -> None:
        result = self._score(None, checker_enabled=False)

        self.assertEqual(96, result.confidence)
        self.assertEqual("off", result.checker_status)

    def test_strength_deduction_happens_before_basis_cap(self) -> None:
        candidate = _forecast_candidate()
        result = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot(candidate.evidence_quote),
            verdict=_verdict(evidence_strength="adequate"),
            checker_enabled=True,
        )

        self.assertEqual(FORECAST_DELTA_CAP, result.confidence)
        self.assertEqual("adequate", result.checker_strength)

    def test_basis_and_thin_caps_compose_once(self) -> None:
        candidate = _candidate(basis="inferred")
        result = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot(candidate.evidence_quote),
            verdict=_verdict(evidence_strength="thin"),
            checker_enabled=True,
        )

        self.assertEqual(CHECKER_THIN_CAP, result.confidence)
        self.assertEqual("review", result.review_flag)
        self.assertIn("Basis: inferred", result.cap_reason)
        self.assertIn("evidence_strength thin", result.cap_reason)


class MaterialityGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()

    def _score(self, **overrides: object):
        candidate = _forecast_candidate(**overrides)
        return score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot(candidate.evidence_quote),
        )

    def test_sub_floor_bp_delta_hard_fails(self) -> None:
        for magnitude in (4, 10, 14, MATERIALITY_FLOOR_BP - 1):
            with self.subTest(bp=magnitude), self.assertRaises(ValueError) as caught:
                self._score(delta_value=magnitude, delta_unit="bp")
            self.assertEqual(HARD_FAILURE_MATERIALITY, str(caught.exception))
            self.assertIn("materiality floor", caught.exception.message)

    def test_sub_floor_pct_delta_hard_fails(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self._score(delta_value=0.86, delta_unit="pct")
        self.assertEqual(HARD_FAILURE_MATERIALITY, str(caught.exception))

    def test_at_floor_bp_delta_is_capped_and_flagged(self) -> None:
        # Exactly at the floor is material (>=), so it proceeds — but capped.
        result = self._score(delta_value=MATERIALITY_FLOOR_BP, delta_unit="bp")

        self.assertEqual(FORECAST_DELTA_CAP, result.confidence)
        self.assertEqual("Medium", result.band)
        self.assertEqual("review", result.review_flag)
        self.assertIn("forecast_delta", result.cap_reason)

    def test_material_bp_delta_is_capped_below_high(self) -> None:
        result = self._score(delta_value=40, delta_unit="bp")

        self.assertEqual(FORECAST_DELTA_CAP, result.confidence)
        self.assertEqual("review", result.review_flag)

    def test_material_pct_delta_is_capped(self) -> None:
        result = self._score(delta_value=MATERIALITY_FLOOR_PCT, delta_unit="pct")

        self.assertEqual(FORECAST_DELTA_CAP, result.confidence)
        self.assertEqual("review", result.review_flag)

    def test_negative_delta_is_sized_by_magnitude(self) -> None:
        # A -40bp move is as material as +40bp (gate is sign-agnostic).
        result = self._score(delta_value=-40, delta_unit="bp")
        self.assertEqual(FORECAST_DELTA_CAP, result.confidence)


class InferredTierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()

    def test_inferred_call_is_capped_one_band_below_and_flagged(self) -> None:
        candidate = _candidate(
            basis="inferred", taxonomy_match="exact", call_language="explicit"
        )
        result = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot(candidate.evidence_quote),
        )

        # Stated calls reach High (>=75); an inference lands one full band down.
        self.assertEqual(INFERRED_CAP, result.confidence)
        self.assertEqual("Medium", result.band)
        self.assertEqual("review", result.review_flag)
        self.assertIn("inferred", result.cap_reason)

    def test_stated_call_is_not_capped_by_basis(self) -> None:
        candidate = _candidate(basis="stated")
        result = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot(candidate.evidence_quote),
        )
        self.assertEqual(96, result.confidence)
        self.assertEqual("", result.cap_reason)


class Pilot05RescoreTest(unittest.TestCase):
    """Deterministic re-score (no LLM) reconstructing frozen pilot-05 rows: the
    AB forecast-delta overreach rows named in runs/pilot-05/gt-comparison.md now
    gate or cap, while JPM GAA stated dials are untouched. Fixtures are built
    from the frozen runs/pilot-05/output.csv values; nothing under runs/ is
    modified."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()

    def _score_forecast(self, leaf: str, delta_value: float, delta_unit: str):
        candidate = _forecast_candidate(
            sub_asset_class=leaf, delta_value=delta_value, delta_unit=delta_unit
        )
        return score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot(candidate.evidence_quote),
        )

    def test_ab_subfloor_forecast_rows_now_gate(self) -> None:
        # (leaf, delta, unit) read off the frozen AB forecast-table rows.
        sub_floor = [
            ("Asia Fixed Income", 4, "bp"),            # 5.71 -> 5.67
            ("Global Govt Bonds/SSAs", 13, "bp"),      # 4.15 -> 4.02
            ("LatAm Fixed Income", 14, "bp"),          # 9.90 -> 9.76
            ("Developed Markets - Sovereigns", 20, "bp"),  # 3.63 -> 3.43
            ("EUR", 0.86, "pct"),                      # 1.16 -> 1.17
        ]
        for leaf, delta, unit in sub_floor:
            with self.subTest(leaf=leaf), self.assertRaises(ValueError) as caught:
                self._score_forecast(leaf, delta, unit)
            self.assertEqual(HARD_FAILURE_MATERIALITY, str(caught.exception))

    def test_ab_material_forecast_row_caps_below_high(self) -> None:
        # EM sovereigns 8.85 -> 8.51 = 34bp: material, but still only provisional.
        result = self._score_forecast("Emerging Markets - Sovereigns", 34, "bp")
        self.assertEqual(FORECAST_DELTA_CAP, result.confidence)
        self.assertEqual("review", result.review_flag)

    def test_jpm_gaa_stated_dial_is_untouched(self) -> None:
        # Frozen pilot-05 used the legacy `explicit` bucket, so this dial row
        # re-scores to the v2 explicit_stance tier (96). A newly extracted
        # dial row with `explicit_dial` will regain the 30-point tier.
        candidate = _candidate(
            sub_asset_class="Emerging Markets Equities",
            basis="stated",
            evidence_kind="visual",
            evidence_quote="EM overweight dial",
            locator="p.6 — 'Global Asset Allocation' views table",
            call_language="explicit",
        )
        result = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot("EM overweight dial views table"),
        )
        self.assertEqual(96, result.confidence)
        self.assertEqual("High", result.band)
        self.assertEqual("", result.cap_reason)


def _forecast_candidate(**overrides: object) -> CandidateCall:
    """A forecast_delta candidate: table evidence with a specific locator so the
    evidence check passes, leaving the materiality gate as the decisive test."""
    values: dict[str, object] = {
        "evidence_kind": "table",
        "evidence_quote": "Global row Long Rates forecast endpoint move",
        "locator": "p.10 - 'Forecast Table'",
        "basis": "forecast_delta",
        "delta_value": 40,
        "delta_unit": "bp",
        "call_language": "implied",
        "taxonomy_match": "exact",
    }
    values.update(overrides)
    return _candidate(**values)


def _verdict(**overrides: object) -> CheckVerdict:
    values = {
        "index": 0,
        "supports_view": "pass",
        "forward_looking": "pass",
        "asset_match": "pass",
        "evidence_strength": "decisive",
        "note": "",
    }
    values.update(overrides)
    return CheckVerdict.from_mapping(values)


def _healthy_snapshot(quote: str) -> str:
    """Snapshot text containing the quote and passing the HTML read-quality floor."""
    filler = "Broader market commentary continues across the outlook document. "
    return quote + " " + filler * (MIN_HTML_SNAPSHOT_CHARS // len(filler) + 1)


def _candidate(**overrides: object) -> CandidateCall:
    values = {
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
    return CandidateCall.from_mapping(values)


if __name__ == "__main__":
    unittest.main()
