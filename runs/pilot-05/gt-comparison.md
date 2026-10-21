# pilot-05 vs ground truth — comparison results

Comparison of `runs/pilot-05/output.csv` (119 rows) against
`ground-truth/pilot-ground-truth.csv` (82 rows), run 2026-07-06 (hybrid:
deterministic firm+leaf join, then five parallel per-firm judgment agents that
verified every non-exact bucket against the ingested source PDFs/snapshots).
Row-level judgments are frozen in `gt-judgments/` (phase-1 join JSONs +
per-firm judgment JSONs). Counts reconcile: 82 = 44 exact + 9 near-leaf + 29
GT-only; 119 = 44 exact + 9 near-leaf + 66 model-only.

## Headline numbers

- Phase 1 (deterministic): 44 exact_match (40 view-agree, 4 disagree),
  75 model_only, 38 gt_only.
- After judgment: 53/119 model rows align with a GT call (44 exact + 9
  near-leaf). 47 of the 53 agree on View. All 6 disagreements were judged
  **convention disputes, none a model reading error**: 5 are table-vs-prose
  or net-of-mixed-signals conflicts where both readings are in the source
  (AB ×4, Aberdeen ×1), 1 is a GT-side inference not grounded in the ingested
  doc (PIMCO quality-credit O vs IG N). One disagreement is opposite-sign
  (AB EM sovereigns O-vs-U, near-leaf, table-vs-prose).
- Of the 66 remaining model-only rows: **38 defensible GT omissions** (29 of
  them JPM — every GAA views-table dial the model enumerated was verified
  correct against the rendered p.6 table), 18 convention disputes, **10
  overreaches** (8.4% of the run; 6 are AB forecast-table rows built on
  4–14bp deltas or contradicted by prose, 2 are JPM soft-commentary rows,
  2 are Schroders inference rows).
- Recall: **53/82 raw (65%)**, up from 42/82 (51%) in pilot-04. Excluding the
  6 PIMCO GT rows judged not grounded in the ingested 2-page PDF: 53/76
  (70%). Two further JPM GT rows' claims are covered by model rows under
  adjacent leaves.

## Per-firm recall (matched / GT rows)

| Firm | Matched | Notes |
|---|---|---|
| Schroders (grouped pair) | 28/28 (100%) | 26 exact + 2 near-leaf; the 2 "misses" are pure leaf-name drift vs pilot-04, views agree |
| J.P. Morgan (grouped pair) | 8/13 (62%) | +2 more GT claims covered under adjacent leaves (Duration - Short, Securitized) → effectively 10/13; all 24 GAA dial signs verified correct |
| AllianceBernstein | 6/12 (50%) | misses are country-equity/cash calls GT inferred from macro prose |
| PIMCO | 7/15 (47%) | 6 of 8 misses cite content absent from the ingested 2-pager (GT authored from the full Cyclical Outlook article) → 7/9 (78%) on in-scope rows |
| Aberdeen | 4/14 (29%) | up from 0/14; all GT-cited passages ARE in the ingested snapshot — gap is inference granularity, not ingestion |

## Root-cause distribution of the 29 remaining misses

- **20 inference depth** (Aberdeen 9, AB 5, JPM 3, PIMCO 3) — GT converts
  macro prose into country/asset-level allocation calls (e.g. Thailand
  political risk → Thailand Equities U; fiscal-slippage paragraphs → EM
  Sovereigns U; "remain active" → Cash N). The model reads the same passages
  but does not make analyst-style multi-step inferences. This is now the
  dominant gap and is a scope/convention question, not an extraction bug.
- **6 GT rows not grounded in the ingested source** (PIMCO 5, Aberdeen Oil 1)
  — commentary cites reasoning that does not exist in the ingested document.
- **2 covered under an adjacent leaf** (JPM Short-Dated US Treasuries, US
  Agency MBS) — the model carried the same claim on a different leaf.
- **1 not_emitted** (AB EM Basket U) — explicit prose the model skipped while
  emitting the USD O side of the same passage.

## Precision profile (vs pilot-04 inversion)

Of 119 rows: 47 GT-agreeing + 38 defensible GT omissions = 85 solid (71%);
18 convention disputes (15%); 6 aligned-but-disagreeing (all convention);
10 overreaches (8.4%). Pilot-04 was 50 rows with 1 overreach — the swap
traded a precision-safe/recall-poor profile for recall-rich with a real but
localized overreach tail.

The overreach tail has one dominant cause: **the forecast-delta convention
has no materiality floor**. codex enumerated AB's p.10 table converting any
yield/FX delta into a view — including 4bp (Asia FI, emitted twice on two
leaves), 10bp (Polish Bonds), and ~1-cent FX moves (EUR O, contradicting the
doc's own USD-strength view). Where table endpoint and prose stance conflict,
the model always followed the table and GT always the prose — that single
convention choice explains all 4 exact view mismatches and most convention
disputes. Secondary issues: the same global-duration call emitted on three
leaves (Duration / Global Govt Bonds/SSAs / DM Sovereigns); "close the
overweight" mapped to U instead of N (Schroders value); a hedged risk note
mapped to U instead of UNCERTAIN (AB private markets).

## Rubric / checker behaviour

The 5 review flags were well-aimed at prose softness: 4 sit exactly on JPM's
soft-commentary rows (Oil, Gold, Momentum, US Small Cap — the run's 2
commentary overreaches plus 2 convention disputes), 1 on an Aberdeen
region-mapping stretch. But the other 8 overreaches all pass at 75/High/none:
table-evidence rows satisfy the key-token check and the opus checker
confirmed them because they are internally consistent with the printed
numbers. Catching them needs a deterministic materiality gate on
numeric-delta evidence, not a stronger checker. Positive: the opus checker
verified all 24 JPM GAA dial signs correctly and its one hard kill this run
(AB JPY) was a genuine judgment call.

## Open questions for the analyst/client

1. **Forecast-table deltas**: is a house forecast (yield/FX endpoint) a
   "view" at all? If yes, what materiality floor (e.g. ≥25bp / ≥2% FX), and
   does prose stance override the table when they conflict?
2. **Inference depth**: should the system make analyst-style macro-to-
   allocation inferences (country equities from macro prose), or only carry
   stated/dial views? This now bounds recall at ~70-75% if answered "stated
   only".
3. **Level policy**: main dial + sub-dials both, or one level? (GT is
   internally inconsistent on Schroders: keeps Equities/Commodities mains but
   drops Govt bonds/Credit mains.)
4. Leaf-snapping conventions for the 9 near-leaf pairs (broad vs specific;
   e.g. Equities - General vs Global Equities, High Quality Issuers vs
   Global IG Credit).
5. **PIMCO source scope**: GT was authored from the full Cyclical Outlook web
   article; the pilot ingests only the 2-page infographic PDF. Ingest the
   article (or re-scope those 6 GT rows)?
6. Aberdeen Oil U appears structurally disputable (encodes "oil risk is bad
   for EMs", but an oil-spike scenario is bullish the oil price itself).
