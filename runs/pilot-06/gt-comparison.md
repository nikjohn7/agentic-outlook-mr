# pilot-06 vs ground truth — comparison results

Comparison of `runs/pilot-06/output.csv` (106 rows) against
`ground-truth/pilot-ground-truth.csv` (82 rows), run 2026-07-06 (hybrid:
deterministic firm+leaf join via `src/eval.py`, then five parallel per-firm
judgment agents that verified every non-exact bucket against the ingested
source PDFs/snapshots — never from the row text alone). Branch: **phase-3**.
Row-level judgments are frozen in `gt-judgments/` (per-firm judgment JSONs);
the phase-1 join and the filled judgment worksheet are in `eval/`. Counts
reconcile: 82 GT = 54 exact + 16 near-leaf-covered + 12 residual misses;
106 model = 54 exact + 52 model-only.

## Headline numbers

- **True recall (leaf-match): 70/82 (85.4%)** = 54 exact_match + 16
  near_leaf_covered — up sharply from pilot-05's 53/82 (65%). Because **0 GT
  rows are not-grounded this run** (see PIMCO below), the grounded-subset
  recall equals the raw recall: **85.4%** (pilot-05 had to discount 6
  not-grounded PIMCO rows to reach a 70% in-scope figure — that discount is
  now gone).
- **View agreement**: 49/53 decided exact matches agree (92.5%); including the
  16 near-leaf pairs, 64/69 decided matches agree (**92.8%**). The single
  near-leaf view divergence is PIMCO Global IG Credit (model O vs GT N),
  itself a convention dispute.
- **Overreach: 1 of 106 model rows (0.9%)** — JPM Gold/Precious O — and it is
  **review-flagged (flag hit-rate 1/1 = 100%)**. This is the headline
  precision result: pilot-05 had 10 overreaches (8.4%), 8 of them passing at
  75/High unflagged. The AB forecast-table micro-delta tail that produced most
  of those is entirely gone (see movement table).
- **View disagreements: 4 exact matches, all convention disputes, 0 reading
  errors, all 4 review-flagged (4/4 earned).**
- Quote-verbatim spot check (deterministic, `src/eval.py`): **106/106 pass, 0
  fail.**

## Per-firm recall (true recall = exact + near-leaf, / GT rows)

| Firm | Exact (agree/dis/abst) | Near-leaf | True recall | Notes |
|---|---|---|---|---|
| Schroders (grouped pair) | 26 (26/0/0) | 2 | **28/28 (100%)** | both gt_only rows are near-leaf drift (Global Equities←Equities-General; EM Debt HC←EM IG Credit); zero true misses |
| PIMCO | 9 (8/1/0) | 4 | **13/15 (86.7%)** | full 11p source resolves all pilot-05 grounding gaps; 2 genuine not_emitted misses (EM FI, Europe FI) |
| AllianceBernstein | 9 (9/0/0) | 1 | **10/12 (83.3%)** | overreach tail eliminated; country-macro inferences now recall-positive; 2 residual inference misses (China Eq, Cash) |
| J.P. Morgan (grouped pair) | 6 (6/0/0) | 4 | **10/13 (76.9%)** | all 25 GAA p.6 dial signs re-verified correct; 3 GFICC inference misses (Cash, Subordinated, Europe HY) |
| Aberdeen Investments | 4 (0/3/1) | 5 | **9/14 (64.3%)** | weak spot; country-granularity snapping into one "Asia Equities O" leaf; see below |

## Root-cause distribution of misses

Of 28 phase-1 gt_only rows, judgment recovered **16 as near_leaf_covered**
(the model emitted the same claim + agreeing view on an adjacent/synonymous
leaf), leaving **12 residual true misses**:

- **9 inference_depth** (Aberdeen 4: India Eq, EM Debt-Local Currency, Eastern
  Europe Eq, Thailand Eq; JPM 3: Cash, Subordinated, Europe HY; AB 2: China
  Eq, Cash) — GT converts macro/thematic prose into an allocation call the
  model did not make. Scope/convention question, not an extraction bug.
- **3 not_emitted** (PIMCO 2: Emerging Markets FI N, Europe FI N — now grounded
  in the 11p source at p.5 but the model emitted no geographic-FI leaf;
  Aberdeen 1: Data Centers O — a **regression**, the model emitted this
  thematic row in pilot-05 and dropped it this run).
- **0 not_grounded** — down from 6.

### pilot-05 → pilot-06 movement

| Root cause | pilot-05 | pilot-06 | What happened |
|---|---|---|---|
| inference_depth | 20 | **9** | AB made the country/macro→allocation inferences that were pilot-05 misses (Japan Eq, Europe Eq, UK Eq, Japan Govt Bonds, EM Basket now **exact matches**), lifting AB recall |
| not_grounded | 6 | **0** | PIMCO now ingests the full 11-page Cyclical Outlook; every pilot-05 not-grounded row (US Agency MBS, Securitized, Equities, EM FI, Europe FI) is now grounded in-source |
| near_leaf_covered (recovered) | ~9+2 | **16** | driven by Aberdeen country-granularity snapping (5) + PIMCO/JPM adjacency (8) |
| not_emitted | 1 | **3** | PIMCO EM FI/Europe FI (now-grounded skips) + Aberdeen Data Centers regression |

**Confirmed: Aberdeen country-granularity snapping is the dominant new
pattern** — but it manifests mostly as *recovered* near_leaf_covered rows, not
true misses. One regional "Asia Equities O" leaf legitimately carries the
Taiwan/South Korea/Malaysia country-O claims **and** the AI-sector O (all the
same evidence, same view), so 4 of Aberdeen's gt_only rows are one emitted call
at coarser grain. A 5th, "Emerging Markets - Sovereigns U", is a pure
taxonomy-naming split against the model's synonymous "EM Sovereign Bonds U"
leaf. Net: ~5 of Aberdeen's 10 "misses" are granularity/naming artifacts, which
is why its raw exact recall (28.6%) badly understates its true recall (64.3%).
The genuine Aberdeen gaps are 4 inference-depth country calls + 1 regression.
A caveat worth flagging: the Asia-Equities-O evidence names **Thailand
positively** ("broadened out to... Malaysia and Thailand"), so the coarse leaf
actively *contradicts* GT's country-level Thailand Equities **U** — a concrete
failure mode of region→country snapping masking country downside.

## Precision profile

Of 106 model rows: 49 exact view-agree + 42 defensible_gt_omission = **91 solid
(85.8%)**; 13 convention disputes (12.3%: 4 exact view-disagreements + 9
model-only); 1 UNCERTAIN abstain (Aberdeen Oil); **1 overreach (0.9%)**. This
inverts pilot-05's profile (85 solid but 10 overreaches / 8.4%): pilot-06 keeps
the recall gains while collapsing the overreach tail to a single flagged row.

The 52 model-only rows break down as **42 defensible_gt_omission** (30 of them
JPM — every GAA p.6 "Active allocation views" dial the model enumerated was
re-verified against the rendered table; GT simply chose not to carry the dial
table), **9 convention_dispute** (level/leaf/reading conventions — Schroders
main-vs-sub dials, JPM Quality/Momentum/Value alpha-signals, PIMCO High Quality
Issuers, Aberdeen EM Credit), and **1 overreach** (JPM Gold/Precious O — "gold's
role as a portfolio diversifier remains intact" is constructive prose, not a
stated overweight; the new inferred tier caught it: basis:inferred, capped
72/Medium, review-flagged).

## Rubric / checker behaviour

**View disagreements (4, all review-flagged, all convention disputes):**
- Aberdeen China Equities U vs N — tariff-wall negative (p.3) vs AI+ positives
  (p.4); model tilts a notch bearish, GT's N is the better-grounded net.
- Aberdeen Emerging Markets Equities O vs N — "AI tailwind for EMs overall" vs
  "complex and uneven... selectivity critical"; a net-stance weighting dispute.
- Aberdeen LatAm Fixed Income N vs O — elevated real rates / wide differentials
  (p.6) favour GT's O carry story; the model's N under-reads the carry. GT
  direction is the stronger read here.
- PIMCO Equities - General U vs N — "rebalancing" over-concentrated equities
  (p.8) reads as rebalance-to-neutral (GT N); the model over-inferred direction
  to U. Flag earned.

None is a reading error; all four sit on prose that states no allocation, and
all four are correctly review-flagged. Aberdeen's Oil row (model UNCERTAIN vs GT
U, scored as abstain) is a **correct** application of the new
hedged-risk→UNCERTAIN convention: the source frames a Hormuz oil-spike only as a
downside *scenario* for EM importers, and GT's U conflates that with an
oil-price underweight (the same structurally disputable GT row flagged in
pilot-05). First kept UNCERTAIN in the program, and it landed on the right row.

**Inferred-tier audit (17 rows — first live audit):** all 17 trace to real
source passages; **zero hallucinated/ungrounded**. Seven AB rows (Europe
Duration N, Europe Eq U, UK Eq U, Japan Eq N, Japan Govt Bonds U, EM FI U, EM
Basket U) are sound single-step inferences and **all agree with GT** — inference
here is recall-positive, the mirror image of pilot-05 where these same calls
were GT-only misses. Genuine over-reads among inferred rows are **2** — JPM
Gold/Precious O and PIMCO Equities-General U — both directional over-reads of
hedged/rebalance prose, and both were **caught by the tier**: capped one band
below stated (72–74/Medium) with a forced review flag, so neither reached High.
Aberdeen's disputable-direction inferred rows (China U, LatAm FI N) are the
flagged convention disputes above. Verdict: the tier is working as designed —
segregate, cap, flag; no unflagged inference reached High.

**Thin-tier audit (13 rows — first live audit):** "thin" is correctly earned
for the genuinely hedged/inferred rows — AB's hedged USD O ("while the conflict
remains intense... path thereafter is less clear"), the inferred U's, and JPM's
Momentum/Value alpha-signal rows ("can favor momentum strategies", "pocket of
opportunity in U.S. small caps" — market-signal observations, not committed
tilts). **However**, for 3 stated, verbatim-explicit rows on scrambled PIMCO/JPM
pages — PIMCO PD-Asset-Based/Asset-Backed O ("we favor asset-based finance
(ABF)", p.7), PIMCO US Treasuries O ("we prefer a modest overweight to
duration... Treasury market... safe haven", p.8), JPM Securitized/Structured O
— "thin" is a **mechanical artifact of scrambled-page key-token degradation**
understating strong evidence, not a real evidence-quality judgment. So the thin
grade currently conflates two things: hedged/soft evidence (correct) and
scrambled-page verbatim degradation (artifact). Worth a rubric note.

**Materiality gate:** **UNEXERCISED again** — the analyzer emitted no
`forecast_delta` candidates this run (0 kept, 0 `delta_below_materiality`
failures), because codex classified AB's forecast content as broad
stated/inferred country views rather than enumerating the p.10 yield/FX table.
The gate + caps remain code-live and unit-tested but are not behaviorally
demonstrated on a live run. This is the one fix-wave item without a live
demonstration.

## Open questions for the analyst/client

1. **Leaf-matching convention (now the biggest recall lever)**: should the
   evaluator/pipeline credit region→country (Asia Equities O ⊇ Taiwan/Korea/
   Malaysia Eq O) and synonymous leaves (EM Sovereign Bonds ≡ Emerging Markets -
   Sovereigns)? Doing so recovers ~5 Aberdeen rows and lifts several firms. The
   flip side (Thailand) shows coarse leaves can also *hide* an opposite
   country call — so the answer bears on output granularity, not just scoring.
2. **Inference-depth scope**: the inferred tier is built and calibrated; the 9
   residual inference_depth misses (and AB's recall lift) show the model *can*
   make these calls when prompted. Is analyst-style macro→allocation inference
   in scope, and at what confidence ceiling?
3. **Two view disagreements where GT's direction is arguably stronger**
   (Aberdeen LatAm FI, China Equities) — confirm the house reading of net
   stance on a prose-only source.
4. **Thin-grade semantics**: separate "hedged/soft evidence" from
   "scrambled-page verbatim degradation" so the two verbatim-explicit PIMCO
   stated rows are not penalised as thin?
5. **Materiality gate demonstration**: accept unit-test coverage, or require a
   source with a live forecast-delta table to demonstrate the gate end-to-end?
6. Aberdeen Data Centers O regression — the model emitted this grounded
   thematic row in pilot-05 but dropped it this run; investigate whether the
   inference-tier prompt or dedup suppressed it.

## Does this run support closing Phase 2?

**Yes, on quality grounds.** True recall rose 65% → 85.4%, not-grounded went
6 → 0, the overreach tail collapsed 10 → 1 (100% flag hit-rate), all view
disagreements are convention disputes (0 reading errors, 4/4 flagged), the
first UNCERTAIN abstain landed correctly, and the two new tiers (inferred,
Rubric-v2 thin) are live and behaving as designed. The residual gaps are
convention/scope decisions for the client (leaf granularity, inference scope),
not extraction defects. The two caveats to record at close: the **materiality
gate is still unexercised** (code-live/unit-tested only), and the **thin grade
conflates hedged evidence with scrambled-page degradation**.
