# Evaluation — test2-01 vs ground truth

Deterministic ground-truth comparison (no LLM calls). Buckets use the pilot-05 phase-1 vocabulary: `exact_match` (firm+leaf join), `model_only`, `gt_only`. Firm names are normalized before joining.

## Headline numbers

- Ground-truth rows: **89**
- Model rows: **142**
- `exact_match`: **68** (63 view-agree, 5 disagree, 0 UNCERTAIN/abstain)
- `model_only`: **74**
- `gt_only` (missed calls): **21**
- Raw leaf-match recall: **68/89 (76.4%)**
- View-agreement among decided matches (UNCERTAIN excluded): **63/68 (92.6%)**

## Per-firm breakdown

| Firm | GT | Model | Matched | Agree | Disagree | Abstain | Model-only | GT-only | Recall% |
|---|---|---|---|---|---|---|---|---|---|
| BlackRock | 16 | 16 | 3 | 2 | 1 | 0 | 13 | 13 | 18.8 |
| Franklin Templeton | 24 | 33 | 23 | 21 | 2 | 0 | 10 | 1 | 95.8 |
| Goldman Sachs Asset Management | 9 | 39 | 8 | 8 | 0 | 0 | 31 | 1 | 88.9 |
| T. Rowe Price | 24 | 30 | 22 | 20 | 2 | 0 | 8 | 2 | 91.7 |
| Wellington Management | 16 | 24 | 12 | 12 | 0 | 0 | 12 | 4 | 75.0 |

## UNCERTAIN as abstain / coverage

0 matched model rows are `UNCERTAIN` — scored as abstain, neither right nor wrong (ground truth carries no UNCERTAIN). They are excluded from the view-agreement denominator above.

## View disagreements (matched leaf, opposite call)

| Firm | Leaf | Model | GT | Review flag |
|---|---|---|---|---|
| BlackRock | Healthcare/Pharma | O | N | review |
| T. Rowe Price | Europe Equities | O | N | review |
| T. Rowe Price | UK IG Credit | N | U | none |
| Franklin Templeton | EM Debt - Local Currency | U | N | none |
| Franklin Templeton | US Large Cap | O | N | review |

## Missed calls (`gt_only`)

Every ground-truth call the pipeline did not emit under the same leaf. A miss is likely costlier than a wrong call, so this is the primary review list. Near-leaf column lists same-firm, agreeing-view model rows on a *different* leaf (a suggestion, never an auto-match).

| Firm | Leaf | GT view | Near-leaf hint |
|---|---|---|---|
| BlackRock | Brazil Equities | O | Asia Equities (O, 0.333); Global Equities (O, 0.333); Mining Equities (O, 0.333) |
| BlackRock | Defence/Aerospace | O | — |
| BlackRock | High Dividend/Dividends/Income | O | — |
| BlackRock | IT/Tech/Telecomms (inc. AI) | U | — |
| BlackRock | LatAm Equities | O | Asia Equities (O, 0.333); Global Equities (O, 0.333); Mining Equities (O, 0.333) |
| BlackRock | Momentum | N | — |
| BlackRock | Quality | N | — |
| BlackRock | UK Large Cap | O | — |
| BlackRock | UK Small Cap | O | — |
| BlackRock | US Equities Equal-Weighted | O | Asia Equities (O, 0.2); Global Equities (O, 0.2); Mining Equities (O, 0.2) |
| BlackRock | US Large Cap | N | — |
| BlackRock | US Mega-Cap (Tech) | U | — |
| BlackRock | US Value | O | — |
| Franklin Templeton | Global HY | N | Global Govt Bonds/SSAs (N, 0.2) |
| Goldman Sachs Asset Management | Europe Fixed Income | O | Fixed Income - General/Global (O, 0.5); Europe Equities (O, 0.25); High Dividend/Dividends/Income (O, 0.167) |
| T. Rowe Price | Europe IG Credit | U | Europe Fixed Income (U, 0.2) |
| T. Rowe Price | US IG Credit | U | — |
| Wellington Management | Euro Govt Bonds | N | — |
| Wellington Management | Japan Equities | N | Asia Equities (N, 0.333) |
| Wellington Management | UK Gilts | N | — |
| Wellington Management | US Treasuries | N | US Duration (N, 0.333) |

## Review-flag hit analysis

- View disagreements on review-flagged rows: **3/5**
- `model_only` rows on review-flagged rows: **30/74**
- Missed calls with a review-flagged near-leaf suggestion: **4/21**

## Column distributions

- `band`: High=114, Medium=28
- `basis`: inferred=6, stated=136
- `checker_strength`: adequate=58, decisive=76, thin=8

## Quote-verbatim spot check

_best-effort: evidence_kind inferred from commentary text; the raw candidate schema is not persisted to the run._

- Passed: **142**, Failed: **0**, Unparseable commentary: 0, No snapshot: 0
