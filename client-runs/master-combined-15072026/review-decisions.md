# Human review of reconcile supersessions and needs-human keys — 2026-07-15

Reviewed: all 7 exact-pass cross-view supersessions, all 15 near-leaf cross-view
supersessions (12 clusters), and the 1 needs-human key, each against the full
commentary of every member row; two decisions were additionally verified against
the live source documents (PGIM, State Street). Result: 15 supersessions
accepted as reconciled, 4 overridden, needs-human key resolved. The pre-review
files are preserved as `output.pre-human-review.csv` /
`failures-client.pre-human-review.csv`; the machine audit CSVs are untouched
(they record what the automated stage did — this file records the human
overlay). Overridden rows keep the reconcile merge semantics: confidence stays
the cluster max, Source/URL/Date joins unchanged, commentary reordered so the
new primary segment leads, per-row fields (band/basis/checker_strength/
call_language/quote_match) taken from the new primary, review flag kept.

## Overridden (4)

1. **Carmignac / Europe Equities: O → UNCERTAIN kept.** Both rows are the same
   document ingested in both batches, and both quotes are the same conditional
   optionality ("could reignite optimism ... if geopolitics calm down"). The O
   row's own checker left it unconfirmed ("conditional optionality rather than a
   committed overweight"); the firm's stated European preferences are
   sector-level (aeronautics, banks — kept as their own rows), not a broad
   Europe overweight. Per the no-forced-call rule, UNCERTAIN is the faithful
   view. Side benefit: the kept row carries the document date (12/06/2026) the
   O row lacked.

2. **AllianceBernstein / Duration: O → U (Multi-Asset primary).** Genuine
   intra-firm divergence: Multi-Asset Midyear Outlook says "Underweight
   duration but tap income" (visual-verified verbatim, checker `decisive`,
   `explicit_stance`); the Fixed-Income Midyear Outlook argues for "keeping
   bonds anchored ... holding duration" (checker `adequate`). The one-day
   recency edge is immaterial. "Holding duration" is a maintain stance —
   mapping it to O overreaches — while the multi-asset outlook is the
   allocation-authoritative document and its directive is explicit. (Consistent
   with keeping the Multi-Asset O as primary on AB / US Equities, accepted
   below.)

3. **Lion Global Investors / Duration - Short O → Duration U.** Both rows quote
   the identical sentence; the headline stance is "remain cautious on duration
   ... favouring shorter maturities". The Duration U encoding is the firm's
   call (stated, checker `decisive`, conf 89 vs 75), the short-maturity
   preference its expression; canonicalizing on broad Duration also matches the
   GlobalX cluster's treatment. Taxonomy fields rebuilt for the Duration leaf.

4. **State Street / Japan Duration: O → U.** Source verified (both rows are the
   SAME document ingested in both batches — identical URL). The full sentence
   explicitly names Japan: "Even in Japan, where policy remains more
   accommodative, gradual normalization has reduced the willingness of
   investors to absorb duration risk", and the section concludes term premia
   stay elevated, "limiting the scope for sustained rallies in duration" — so
   the checker's "does not name Japan" objection was an artifact of the clipped
   quote. The O rested on a one-step inference from the BoJ dovish-gauge remark
   that the document itself walks back for Japanese duration demand.

## Needs-human resolved (1)

5. **Triodos Investment Management / Oil: keep U, drop UNCERTAIN.** The firm
   names a reference scenario — "Oil prices peak in Q2 and return to
   pre-conflict levels" — i.e. a base-case declining path, and the Advanced
   Economies doc itself says the Iran deal "increased the likelihood of a
   gradual normalisation". The prolonged-disruption scenario is the risk case,
   not the base case; a named base case is a call, so U over UNCERTAIN. Row
   stays inferred/Medium/review-flagged.

## Accepted as reconciled (15 superseded rows)

- **Charles Schwab / US Equities (UNCERTAIN → N):** same balanced stance in
  both docs; the newer Mid-Year Outlook states it as a balanced regional view
  (stated, High).
- **RBC GAM / Fixed Income - General/Global (2× O → U):** the standing position
  is still an underweight ("we NARROWED our prior underweight"); the two O rows
  describe the direction of change / modest return expectations, not the
  position. Textbook reduce/neutralize→resulting-stance.
- **RBC Wealth / Intermediate US Treasuries (N → U):** the dedicated
  bond-market piece is the richer statement — hold ~4.50% "but with an upward
  bias" and "scope to test 4.80 / 5.0" — a bearish price tilt; the N row is the
  same house view via a scrambled-page-degraded quote.
- **TwentyFour / Short-Dated US Treasuries (UNCERTAIN → N):** stated "short end
  looks more fairly priced" beats an inferred conditional-Fed-risk non-call.
- **Wellington / Credit - General (U → N):** explicit "we have turned neutral
  on credit", most recent (08/07), conf 86.
- **AllianceBernstein / US Equities (N, U → O):** "equity exposure should favor
  the US" is the explicit allocation directive; the N is a non-conviction
  "agnostic" remark, the U a within-market valuation-selectivity caution.
- **Angel Oak / US Credit U → US IG Credit O:** the printed relative-yield
  chart (IG cheapest vs stocks in decades, conf 90) is the published level and
  IG-specific; the U was a securitized-vs-corporates relative preference. The
  in-run arbiter had already resolved this the same way.
- **Barclays Private / Duration - Long O → Duration N:** one sentence, stated
  neutral (4-6y) with a CONDITIONAL long-end extension on yield spikes; the
  conditional bias should not stand as a separate O.
- **Citizens / US Duration U → Duration N:** explicit stated "duration, should
  remain neutral" beats the inferred higher-for-longer U; the U row's own
  checker said so.
- **GlobalX / Duration - Short U → Duration O:** one position — the short bias
  fades and "investors can be compensated for adding some interest rate risk";
  adding duration is the actionable call.
- **PGIM / Euro Govt Bonds (2× U → O):** SOURCE VERIFIED — p.13 "Summary Asset
  Class Views" market scores use a 5-point excess-return scale (Selloff /
  Correction / Carry / Modest tightening / Bull market) and Europe's dot is
  "Modest tightening", the 4/5 positive score. The published rating beats the
  two ECB-hike macro inferences (published-level rule).
- **PICTET AM / Global Equities U → Equities - General N:** the published
  portfolio table (Equities 55% vs benchmark 55%) is the concrete positioning;
  "trim global equity exposure" is inferred prose (published-level rule).
- **RBC GAM / Emerging Markets Equities (U → N; structural O kept separate):**
  "reduced ... to neutral from a slight overweight" is the standing tactical
  position and most recent; the strategic supercycle O is a different-horizon
  claim, correctly kept and flagged.
- **RBC Wealth / US Credit U → US IG Credit O:** on the IG leaf, the
  IG-specific "That said, all-in yields remain attractive (5.3% vs 4.7% 5y
  avg)" is the paragraph's operative conclusion; the U sentence is about credit
  markets broadly. Borderline O-vs-N — stays review-flagged.

## Resulting counts

- output.csv: 3,631 → 3,630 rows (Triodos UNCERTAIN dropped; overrides are
  in-place swaps).
- failures-client.csv: 1,385 → 1,386 rows (Triodos UNCERTAIN added as
  superseded; four loser/primary swaps edited in place, notes marked
  "human review").
