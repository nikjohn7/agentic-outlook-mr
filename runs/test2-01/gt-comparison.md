# test2-01 — Ground-truth comparison (judgment pass)

_Branch phase-3. Blind run `test2-01` (7 new sources, 2 grouped pairs) compared
against `ground-truth/test2-ground-truth.csv` (89 rows / 5 firms). Two stages:
the deterministic `src.eval` join (`runs/test2-01/eval/`, no LLM), then this
judgment pass — five parallel per-firm agents verifying every non-exact
worksheet row against the ingested `work/test2-01/` snapshots + source PDFs.
Row-level verdicts in `runs/test2-01/gt-judgments/*.judgment.json`; worksheet
judgment/notes columns filled (`eval/judgment-worksheet.csv`)._

## Headline

- **Raw leaf-match recall 68/89 (76.4%)** — the best raw recall of any run
  (pilot-06 65.9%). **View-agreement among matched 63/68 (92.6%)**. **Quote
  spot check 142/142 pass, 0 fail.**
- **Grounded-adjusted recall ≈ 75/83 (90.4%)**: add the 7 `near_leaf_covered`
  misses (same claim/view, adjacent leaf) to the 68 exact = 75 covered; drop the
  6 `not_grounded` GT rows (authored from fuller material absent from the
  ingested source) from the denominator.
- **Only 4 genuine misses (`recall_gap`)** out of 21 gt_only: **2 are fixable**
  (evidence-gated print-captured dials, below) and **2 are the one costly
  reading gap** (BlackRock's central caution — below).
- **Precision is excellent: 1 overreach in 74 `model_only` rows** (66
  sound_breadth + 7 near_leaf_of_gt + 1 overreach). Across all 142 kept rows the
  judgment pass found **exactly 2 real defects** (1 overreach + 1 view reading
  error) — and they are the **same defect class** (see EM-Debt below).
- **Both post-pilot-06 changes validated positively** (Change-1 call_language,
  Change-2 country-granularity — below).

## Per-firm

| Firm | GT | Model | Exact | Raw recall% | Misses (rg/nl/ng/do) | model_only (sb/or/nl) | Note |
|---|---|---|---|---|---|---|---|
| BlackRock | 16 | 16 | 3 | 18.8 | 13 (2/1/6/4) | 13 (13/0/0) | Raw % badly understates: 10/13 misses are GT-provenance (equity-only PDF); 13/13 model calls grounded |
| Franklin Templeton | 24 | 33 | 23 | 95.8 | 1 (0/1/0/0) | 10 (8/0/2) | Reproduced the whole pendulum grid; 0 overreach |
| Goldman Sachs AM | 9 | 39 | 8 | 88.9 | 1 (0/1/0/0) | 31 (31/0/0) | 31/31 model_only sound — faithful p.12 grid enumeration |
| T. Rowe Price | 24 | 30 | 22 | 91.7 | 2 (0/2/0/0) | 8 (5/1/2) | Grouping rescued the gated monthly dials via the UK-view PDF; 1 overreach |
| Wellington Mgmt | 16 | 24 | 12 | 75.0 | 4 (2/2/0/0) | 12 (9/0/3) | 2 misses evidence-gated (fixable), 0 reading errors, 0 overreach |

rg=recall_gap · nl=near_leaf_covered · ng=not_grounded · do=defensible_omission
· sb=sound_breadth · or=overreach · nl(mo)=near_leaf_of_gt

## Misses (21 gt_only) — decomposed

| Class | N | Costly? | Meaning |
|---|---|---|---|
| not_grounded | 6 | no | GT call not in the ingested source (all BlackRock — GT authored from a fuller multi-asset corpus: bonds/credit/MyMap dials absent from this equity-only PDF) |
| defensible_omission | 4 | no | Source signal too weak/passing to require a row (all BlackRock: US Value, US Large Cap, Momentum, High Dividend) |
| near_leaf_covered | 7 | no | Model expressed the same view on an adjacent leaf (US Treasuries N→US Duration N; Global HY N→Leveraged Loans N; Europe FI O→Green/Blue Bonds O; US/Europe IG Credit U→US/Europe Fixed Income U; etc.) |
| **recall_gap** | **4** | **yes** | 2 fixable (Wellington Japan Equities N + UK Gilts N, emitted-but-evidence-gated) + 2 costly (BlackRock IT/Tech U + US Mega-Cap U) |

## The two real defects (both EM-Debt-Local-Currency)

The single model view **reading error** (Franklin EM Debt - Local Currency `U`
vs GT `N`) and the single **overreach** (T. Rowe Price EM Debt - Local Currency
`U`, model_only) are the **same failure**: the source says *reduce / neutralize*
EM-debt exposure, and the model mapped the *direction of travel* to `U` instead
of landing on the **resulting stance** `N`. This is exactly the pilot-05
convention (close/trim → resulting stance) **not firing on "neutralize/reduce"
language**. Franklin is worse because the model read the *same p.12 dial*
correctly as `N` for the "EM Debt - General" leaf but `U` for the local-currency
leaf — internally inconsistent, and it slipped through unflagged (conf 75, no
review). **Actionable:** reinforce the trim→resulting-stance convention for
"reduce/neutralize" verbs in `conventions.md`/`brain.md`; consider a checker
consistency probe when the same source+dial yields opposite signs on sibling
leaves.

## The one costly recall gap (BlackRock central caution)

BlackRock's whole thesis is *diversify away from* expensive asset-light mega-cap
AI winners ("vulnerable to profit taking," FCF drawn "toward zero," lowest S&P
FCF yield in 25 years). GT captured this as **US Mega-Cap (Tech) U** and
**IT/Tech/Telecomms U**; the model emitted **only bullish tech** (Technology
Sector O on Asian semis) and never captured the cautionary side — taking the
opposite sign on the document's core call, unflagged. The model is not wrong
that Asian semis are favored (grounded), but it read a two-sided tech stance
one-sidedly. **Actionable:** this is an inference-scope/two-sidedness gap, not an
extraction bug — the caution is prose, not a dial.

## View disagreements (5) — adjudicated

| Firm | Leaf | Model | GT | Verdict |
|---|---|---|---|---|
| BlackRock | Healthcare/Pharma | O | N | **model_correct** — source squarely constructive (p.7 FCF/valuation), GT too conservative |
| T. Rowe Price | UK IG Credit | N | U | **GT error** — both the monthly dial and UK-view p.3 grid show Neutral; model's N is right |
| Franklin Templeton | US Large Cap | O | N | convention_dispute — no printed large-cap dial; two-sided prose |
| T. Rowe Price | Europe Equities | O | N | convention_dispute — cross-doc conflict (April monthly dial U vs March UK grid O); GT's N is the better reconciliation, model kept the stale-O |
| Franklin Templeton | EM Debt - Local Currency | U | N | **model reading error** (the EM-Debt defect above) |

Net: 1 genuine model view error, 1 GT error, 2 convention disputes, 1
model-correct → the model's view calls are defensible on **4 of 5** disagreements
and on **67 of 68** matched leaves.

## Change validations (the point of this run)

- **Change-2 (country-granularity inference) — validated, recall-POSITIVE.**
  BlackRock's inferred `Taiwan Equities O` and `South Korea Equities O` are both
  grounded in named-country prose (Taiwan earnings +34%; South Korea +220% + a
  corporate-reform/shareholder-value program + shipbuilder tailwinds) and add
  recall the GT never enumerated — landing on the **named country leaves**
  alongside the stated `Asia Equities O`, exactly the intended multi-call
  pattern with **no snapping** to the regional aggregate. GSAM's inferred `China
  Equities N` is likewise a grounded single-step. This directly fixes the
  pilot-06 Aberdeen "Asia Equities O" snapping. All inferred rows correctly
  segregated one band below stated with review flags; **0 hallucinated across
  the 6 inferred rows** (first two firms' audits).
- **Change-1 (`call_language` persisted) — validated.** Populated on all 142
  rows; `explicit_dial` dominant (83, the four dial-grid sources), `implied` on
  exactly the 6 inferred rows; the explicit_dial→explicit_stance prose-downgrade
  guard held (0 explicit_dial on prose).

## Systemic findings for the fix list

1. **Evidence gate vs print-captured HTML dial grids (recall risk).** All 23
   `evidence_check_failed` failures land on the two print-to-PDF sources
   (TRP-Monthly 16, Wellington-Quarterly 7): the visual/table key-token check
   can't find the rendered dial tokens in the print-captured snapshot text, so
   legitimate dial calls are rejected. It cost **2 Wellington recall misses**
   (Japan Equities N, UK Gilts N); T. Rowe Price escaped only because the
   grouped UK-view **PDF** carried the same dials in clean text and rescued
   them. Grouping is thus doing double duty as an ingest-robustness backstop —
   but a standalone print-captured grid has no such rescue. Highest-value fix.
2. **EM-Debt "reduce/neutralize → U" convention gap** (2 defects, above).
3. **Two-sidedness / inference scope** on prose cautionary stances (BlackRock
   mega-cap, above) — the one costly recall gap that isn't ingest-related.
4. **GT provenance mismatch (BlackRock).** 10 of 16 BlackRock GT rows reference
   material (bonds, credit, MyMap/BII multi-asset dials) absent from the ingested
   equity-only PDF — a source-scope decision for the analyst, like the pilot-05
   PIMCO 2-pager. Raw BlackRock recall (18.8%) is not a model quality signal.
5. **Materiality gate UNEXERCISED a 3rd time** — 0 forecast_delta candidates
   emitted (dial/grid-heavy sources classify as stated/inferred).
6. **GSAM horizon/conditionality flattening** (methodological caveat, not a
   defect): the model marks every p.12 grid cell `O`, including tail-risk hedges
   in the "Key Upside/Downside Risks" column; a slight dollar-negative USD tilt
   was read `N`. Per-cell generosity, not fabrication.

## Verdict

The two post-pilot-06 changes land cleanly and the run is the strongest yet on
both recall (90.4% grounded-adjusted) and precision (1 overreach / 74). The
actionable defects are narrow and specific: the print-captured-dial evidence
gate (the one real recall lever), the EM-Debt reduce→U convention, and the
BlackRock two-sided-prose caution gap. None are blind-protocol or extraction-
integrity failures. GT itself carries ≥1 error (TRP UK IG Credit) and ≥6
not-grounded rows (BlackRock corpus scope) worth reconciling with the analyst.
